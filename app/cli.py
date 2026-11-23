"""Operator commands: python -m app.cli <lệnh>. Run with -h for the list."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import html
import json
import re
import sys
from pathlib import Path

from sqlalchemy import select
from telegram import Bot

from app.bot.formatting import format_inventory_report
from app.config import DATA_DIR, ChatSettings, TenantSettings, get_settings, setup_logging
from app.crypto import CryptoError, SecretBox, generate_key
from app.db.models import Chat, Tenant
from app.db.session import Database
from app.kiotviet.client import KiotVietAuthError, KiotVietClient
from app.probe import run_probe
from app.reports.excel import inventory_workbook, report_filename
from app.reports.periods import Period, parse_period
from app.reports.staff import compute_stats
from app.reports.targets import import_targets, parse_month, summary_text, template_workbook
from app.services import Services
from app.textutil import fmt_qty, unaccent
from app.timeutil import now_local


def _services() -> Services:
    settings = get_settings()
    db = Database(settings.database_url)
    db.create_all()
    return Services(settings, db)


def _box(svc: Services) -> SecretBox:
    try:
        return svc.box
    except CryptoError as e:
        sys.exit(str(e))


# ---- tenants ------------------------------------------------------------------


def cmd_tenant_add(args) -> None:
    svc = _services()
    box = _box(svc)
    s = svc.settings
    if args.from_env:
        retailer, client_id = s.kiotviet_retailer, s.kiotviet_client_id
        secret = s.kiotviet_client_secret.get_secret_value()
        if not (retailer and client_id and secret):
            sys.exit("Thiếu KIOTVIET_RETAILER / KIOTVIET_CLIENT_ID / KIOTVIET_CLIENT_SECRET trong .env")
    else:
        retailer = args.retailer or input("Tên gian hàng (Retailer): ").strip()
        client_id = args.client_id or input("Client ID: ").strip()
        secret = getpass.getpass("Client Secret (không hiện khi gõ): ").strip()
    if not args.no_check:
        async def check():
            async with KiotVietClient(retailer, client_id, secret) as c:
                await c.fetch_token()
        try:
            asyncio.run(check())
            print("✔ Đã lấy được token KiotViet.")
        except KiotVietAuthError as e:
            sys.exit(f"✘ {e}\nKhông lưu. Kiểm tra lại mã, hoặc thêm --no-check để bỏ qua bước này.")
    with svc.db.session() as db:
        if db.scalar(select(Tenant).where(Tenant.retailer == retailer)):
            sys.exit(f"Gian hàng {retailer} đã có. Xem: python -m app.cli tenant-list")
        t = Tenant(name=args.name, retailer=retailer, client_id=client_id,
                   client_secret_enc=box.encrypt(secret), settings_json="{}", active=True)
        db.add(t)
        db.flush()
        print(f"Đã thêm NPP id={t.id}: {t.name} ({retailer})")
        print(f"Tiếp theo: python -m app.cli sync {t.id}")


def cmd_tenant_list(args) -> None:
    svc = _services()
    with svc.db.session() as db:
        tenants = list(db.scalars(select(Tenant).order_by(Tenant.id)))
        if not tenants:
            print("Chưa có NPP nào. Thêm bằng: python -m app.cli tenant-add --name \"Tên NPP\"")
        for t in tenants:
            state = "đang chạy" if t.active else "tạm dừng"
            print(f"{t.id}. {t.name} · gian hàng {t.retailer} · {state}")
            print(f"   cấu hình: {TenantSettings.from_json(t.settings_json).model_dump_json()}")


def _parse_value(raw: str, key: str = ""):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if key in ("report_times", "summary_times") and re.fullmatch(r"\d{1,2}:\d{2}(,\d{1,2}:\d{2})*", raw):  # 8:00,12:00
            return [f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in raw.split(",")]
        return raw


def cmd_tenant_set(args) -> None:
    svc = _services()
    with svc.db.session() as db:
        t = db.get(Tenant, args.tenant_id) or sys.exit(f"Không có NPP id={args.tenant_id}")
        if args.key == "active":
            t.active = _parse_value(args.value) in (True, 1, "true", "yes")
            print(f"{t.name}: {'đang chạy' if t.active else 'tạm dừng'}")
            return
        if args.key == "name":
            t.name = args.value
            print(f"Đã đổi tên thành {t.name}")
            return
        if args.key not in TenantSettings.model_fields:
            sys.exit("Khóa không hợp lệ. Dùng một trong: name, active, " + ", ".join(TenantSettings.model_fields))
        data = json.loads(t.settings_json or "{}")
        data[args.key] = _parse_value(args.value, args.key)
        try:
            new = TenantSettings.model_validate(data)
        except ValueError as e:
            sys.exit(f"Giá trị không hợp lệ: {e}")
        t.settings_json = new.to_json()
        print(f"Đã lưu {args.key} = {getattr(new, args.key)!r}. Bot đang chạy sẽ tự cập nhật lịch trong 5 phút.")


# ---- chats --------------------------------------------------------------------


def cmd_chat_add(args) -> None:
    svc = _services()
    if not args.owner and args.tenant is None:
        sys.exit("Cần --tenant ID (nhóm của một NPP) hoặc --owner (nhóm tổng xem tất cả)")
    with svc.db.session() as db:
        if args.tenant is not None and db.get(Tenant, args.tenant) is None:
            sys.exit(f"Không có NPP id={args.tenant}")
        chat = db.get(Chat, args.chat_id) or Chat(chat_id=args.chat_id)
        chat.role = "owner" if args.owner else "tenant"
        chat.tenant_id = None if args.owner else args.tenant
        chat.title = args.title
        db.add(chat)
    print(f"Đã cấp quyền chat {args.chat_id}: " + ("nhóm tổng, xem tất cả NPP" if args.owner else f"NPP id={args.tenant}"))
    print("   nhận: " + _describe_chat(ChatSettings.for_chat("owner" if args.owner else "tenant", None)))
    print(f"   đổi bằng: python -m app.cli chat-set {args.chat_id} <khóa> <giá trị>")


def _describe_chat(cs: ChatSettings) -> str:
    inventory = {"full": "báo cáo tồn kho đầy đủ của từng NPP",
                 "summary": f"tóm tắt tồn kho lúc {', '.join(cs.summary_times)}",
                 "off": "không nhận báo cáo tồn kho"}[cs.inventory]
    ranking = {None: "xếp hạng: tự động", True: "xếp hạng: có", False: "xếp hạng: không"}[cs.ranking]
    urgent = "cảnh báo gấp: " + ("có" if cs.urgent else "không")
    compare = f"so sánh NPP lúc {cs.compare_time}" if cs.compare_time else "so sánh NPP: tắt"
    return " · ".join([inventory, ranking, urgent, compare])


def cmd_chat_list(args) -> None:
    svc = _services()
    with svc.db.session() as db:
        chats = list(db.scalars(select(Chat).order_by(Chat.chat_id)))
        if not chats:
            print("Chưa có chat nào. Nhắn /id cho bot trong nhóm để lấy mã chat.")
        for c in chats:
            scope = "nhóm tổng (tất cả NPP)" if c.role == "owner" else f"NPP id={c.tenant_id}"
            print(f"{c.chat_id} · {scope}" + (f" · {c.title}" if c.title else ""))
            print(f"   {_describe_chat(ChatSettings.for_chat(c.role, c.settings_json))}")


def cmd_chat_set(args) -> None:
    svc = _services()
    with svc.db.session() as db:
        chat = db.get(Chat, args.chat_id) or sys.exit("Không có chat này. Thêm bằng: python -m app.cli chat-add")
        if args.key not in ChatSettings.model_fields:
            sys.exit("Khóa không hợp lệ. Dùng một trong: " + ", ".join(ChatSettings.model_fields))
        data = ChatSettings.for_chat(chat.role, chat.settings_json).model_dump()
        value = _parse_value(args.value, args.key)
        if args.key == "ranking" and value in ("auto", "tu dong", None):
            value = None
        data[args.key] = value
        try:
            new = ChatSettings.model_validate(data)
        except ValueError as e:
            sys.exit(f"Giá trị không hợp lệ: {e}")
        chat.settings_json = new.to_json(chat.role)
        print(f"Đã lưu {args.key} = {getattr(new, args.key)!r}. Bot đang chạy sẽ tự cập nhật lịch trong 5 phút.")
        print("   nhận: " + _describe_chat(new))


def cmd_chat_remove(args) -> None:
    svc = _services()
    with svc.db.session() as db:
        chat = db.get(Chat, args.chat_id)
        if chat is None:
            sys.exit("Không có chat này")
        db.delete(chat)
    print(f"Đã bỏ quyền chat {args.chat_id}")


# ---- sync / report ------------------------------------------------------------


def cmd_sync(args) -> None:
    svc = _services()
    _box(svc)
    result = asyncio.run(svc.sync(args.tenant_id, force_branches=True))
    print(f"Xong: {result.branches} chi nhánh, {result.staff} nhân viên, {result.products} mặt hàng, "
          f"{result.invoices} hóa đơn, {result.returns} phiếu trả hàng, {result.requests} request.")


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"</?(b|i|code)>", "", text))


def cmd_report(args) -> None:
    svc = _services()
    if svc.tenant(args.tenant_id) is None:
        sys.exit(f"Không có NPP id={args.tenant_id}")
    report = asyncio.run(svc.inventory_report(args.tenant_id))
    messages, _ = format_inventory_report(report, now_local())
    for m in messages:
        print(_strip_html(m))
        print()
    if args.excel:
        path = DATA_DIR / report_filename(report.tenant_name, "ton-kho", now_local())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(inventory_workbook(report))
        print(f"Đã ghi file Excel: {path}")


def cmd_send_report(args) -> None:
    svc = _services()
    token = svc.settings.telegram_bot_token.get_secret_value() or sys.exit("Thiếu TELEGRAM_BOT_TOKEN")
    chats = svc.report_recipients(args.tenant_id)
    if not chats:
        sys.exit("NPP này chưa có chat nào nhận báo cáo đầy đủ. Thêm bằng: python -m app.cli chat-add")

    async def go():
        async with Bot(token) as bot:
            await svc.send_inventory_report(bot, args.tenant_id, chats, force_excel=args.excel)

    asyncio.run(go())
    print(f"Đã gửi tới {len(chats)} chat.")


def cmd_probe(args) -> None:
    s = get_settings()
    if args.tenant is not None:
        svc = _services()
        t = svc.tenant(args.tenant) or sys.exit(f"Không có NPP id={args.tenant}")
        retailer, client_id, secret = t.retailer, t.client_id, _box(svc).decrypt(t.client_secret_enc)
    else:
        retailer, client_id = s.kiotviet_retailer, s.kiotviet_client_id
        secret = s.kiotviet_client_secret.get_secret_value()
        if not (retailer and client_id and secret):
            sys.exit("Điền KIOTVIET_RETAILER, KIOTVIET_CLIENT_ID, KIOTVIET_CLIENT_SECRET vào .env, "
                     "hoặc dùng --tenant ID")
    out = Path(args.out) if args.out else DATA_DIR / "probe" / retailer
    print(asyncio.run(run_probe(retailer, client_id, secret, out)))
    print(f"\nĐã lưu mẫu phản hồi vào {out} (dữ liệu thật, không commit, không gửi công khai).")
    print(f"Có thể gửi file {out / 'ket-qua.txt'}: chỉ có tên trường và số lượng.")


def cmd_gen_key(args) -> None:
    print(generate_key())


# ---- staff (phase 2) ------------------------------------------------------------


def _tenant(svc: Services, tenant_id: int) -> Tenant:
    return svc.tenant(tenant_id) or sys.exit(f"Không có NPP id={tenant_id}")


def _period(text: str) -> Period:
    return parse_period(text, now_local().date()) or sys.exit(
        f"Không hiểu kỳ {text!r}. Ví dụ: \"hôm qua\", \"tháng 11\", \"1/11-15/11\"")


def cmd_staff_check(args) -> None:
    """5.5: the two ways to count revenue, side by side, to compare with KiotViet's staff report."""
    svc = _services()
    t = _tenant(svc, args.tenant_id)
    period = _period(args.period)
    ts = svc.tenant_settings(t)
    by_total = compute_stats(svc.db, t.id, period, ts.model_copy(update={"revenue_mode": "invoice_total"}))
    by_lines = compute_stats(svc.db, t.id, period, ts.model_copy(update={"revenue_mode": "lines"}))
    print(f"Đối soát doanh thu nhân viên · {t.name} · {period.label}")
    print("So với KiotViet: Báo cáo → Nhân viên → Bán hàng, cùng kỳ.\n")
    head = f"{'Nhân viên':<24}{'Số đơn':>8}{'Tổng hóa đơn':>16}{'Cộng dòng hàng':>16}{'Hàng trả':>14}"
    print(head)
    print("-" * len(head))
    sums = [0, 0.0, 0.0, 0.0]
    for sid in sorted(by_total, key=lambda k: by_total[k].name):
        a, b = by_total[sid], by_lines[sid]
        sums = [sums[0] + a.orders, sums[1] + a.gross, sums[2] + b.gross, sums[3] + a.returns]
        print(f"{a.name[:23]:<24}{a.orders:>8}{fmt_qty(a.gross):>16}{fmt_qty(round(b.gross)):>16}"
              f"{fmt_qty(a.returns):>14}")
    print("-" * len(head))
    print(f"{'Tổng':<24}{sums[0]:>8}{fmt_qty(sums[1]):>16}{fmt_qty(round(sums[2])):>16}{fmt_qty(sums[3]):>14}")
    print(f"\nĐang dùng: revenue_mode = {ts.revenue_mode} "
          f"({'tổng hóa đơn' if ts.revenue_mode == 'invoice_total' else 'cộng dòng hàng'}), trừ hàng trả.")
    print(f"Cột nào khớp KiotViet thì đặt: python -m app.cli tenant-set {t.id} revenue_mode invoice_total|lines")


def cmd_top(args) -> None:
    svc = _services()
    t = _tenant(svc, args.tenant_id)
    for m in svc.ranking_messages(t, _period(args.period)):
        print(_strip_html(m))


def cmd_targets_template(args) -> None:
    svc = _services()
    t = _tenant(svc, args.tenant_id)
    today = now_local().date()
    month = parse_month(args.month, today) if args.month else f"{today:%Y-%m}"
    if month is None:
        sys.exit("Ghi tháng dạng 11/2026")
    slug = "-".join(unaccent(t.name).split())
    path = Path(args.out) if args.out else DATA_DIR / f"chi-tieu-{slug}-{month}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(template_workbook(svc.db, t.id, month))
    print(f"Đã ghi file mẫu: {path}")


def cmd_targets_import(args) -> None:
    svc = _services()
    t = _tenant(svc, args.tenant_id)
    data = Path(args.file).read_bytes()
    res = import_targets(svc.db, t.id, data, now_local().date())
    print(summary_text(res))
    if res.errors:
        sys.exit(1)


# ---- phase 3 ------------------------------------------------------------------


def cmd_status(args) -> None:
    svc = _services()
    for m in svc.status_messages(svc.all_tenants()):
        print(_strip_html(m))


def cmd_compare(args) -> None:
    svc = _services()
    tenants = svc.active_tenants() or sys.exit("Chưa có NPP nào đang chạy")
    for m in svc.comparison_messages(tenants, _period(args.period)):
        print(_strip_html(m))


def cmd_backup(args) -> None:
    svc = _services()
    path = svc.backup()
    print(f"Đã sao lưu: {path}" if path else "CSDL không phải SQLite: sao lưu bằng công cụ của CSDL đó.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Quản trị bot báo cáo KiotViet → Telegram")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gen-key", help="tạo SECRET_KEY cho .env").set_defaults(fn=cmd_gen_key)

    a = sub.add_parser("probe", help="giai đoạn 0: gọi thử API KiotViet, trả lời các câu hỏi ở mục 3")
    a.add_argument("--tenant", type=int, help="dùng mã của NPP đã thêm (mặc định: lấy từ .env)")
    a.add_argument("--out", help="thư mục lưu kết quả")
    a.set_defaults(fn=cmd_probe)

    a = sub.add_parser("tenant-add", help="thêm NPP (mã KiotViet được mã hóa trước khi lưu)")
    a.add_argument("--name", required=True, help="tên hiển thị trong báo cáo")
    a.add_argument("--retailer")
    a.add_argument("--client-id")
    a.add_argument("--from-env", action="store_true", help="lấy mã từ KIOTVIET_* trong .env")
    a.add_argument("--no-check", action="store_true", help="không thử lấy token trước khi lưu")
    a.set_defaults(fn=cmd_tenant_add)

    sub.add_parser("tenant-list", help="danh sách NPP").set_defaults(fn=cmd_tenant_list)

    a = sub.add_parser("tenant-set", help="đổi cấu hình NPP, ví dụ: tenant-set 1 report_times 8:00,12:00,17:00")
    a.add_argument("tenant_id", type=int)
    a.add_argument("key")
    a.add_argument("value")
    a.set_defaults(fn=cmd_tenant_set)

    a = sub.add_parser("chat-add", help="cấp quyền một nhóm Telegram (lấy mã bằng /id)")
    a.add_argument("chat_id", type=int)
    a.add_argument("--tenant", type=int)
    a.add_argument("--owner", action="store_true", help="nhóm tổng, xem tất cả NPP")
    a.add_argument("--title")
    a.set_defaults(fn=cmd_chat_add)

    sub.add_parser("chat-list", help="danh sách chat được cấp quyền").set_defaults(fn=cmd_chat_list)

    a = sub.add_parser("chat-set", help="chat nhận gì: chat-set <mã chat> inventory full|summary|off, "
                                       "summary_times 8:00,17:00, ranking true|false|auto, urgent true|false, "
                                       "compare_time 18:30|off")
    a.add_argument("chat_id", type=int)
    a.add_argument("key")
    a.add_argument("value")
    a.set_defaults(fn=cmd_chat_set)

    a = sub.add_parser("chat-remove", help="bỏ quyền một chat")
    a.add_argument("chat_id", type=int)
    a.set_defaults(fn=cmd_chat_remove)

    a = sub.add_parser("sync", help="đồng bộ ngay một NPP")
    a.add_argument("tenant_id", type=int)
    a.set_defaults(fn=cmd_sync)

    a = sub.add_parser("report", help="in báo cáo tồn kho ra màn hình (không gửi Telegram)")
    a.add_argument("tenant_id", type=int)
    a.add_argument("--excel", action="store_true", help="ghi thêm file Excel vào data/")
    a.set_defaults(fn=cmd_report)

    a = sub.add_parser("send-report", help="gửi ngay báo cáo tồn kho tới các nhóm của NPP")
    a.add_argument("tenant_id", type=int)
    a.add_argument("--excel", action="store_true", help="luôn gửi kèm file Excel")
    a.set_defaults(fn=cmd_send_report)

    a = sub.add_parser("staff-check", help="đối soát doanh thu nhân viên với KiotViet (2 cách tính)")
    a.add_argument("tenant_id", type=int)
    a.add_argument("period", nargs="?", default="hôm qua", help='kỳ, mặc định "hôm qua"; ví dụ "tháng 11"')
    a.set_defaults(fn=cmd_staff_check)

    a = sub.add_parser("top", help="in bảng xếp hạng nhân viên ra màn hình")
    a.add_argument("tenant_id", type=int)
    a.add_argument("period", nargs="?", default="hôm nay")
    a.set_defaults(fn=cmd_top)

    a = sub.add_parser("targets-template", help="ghi file Excel mẫu chỉ tiêu (điền sẵn nhân viên)")
    a.add_argument("tenant_id", type=int)
    a.add_argument("--month", help="tháng, ví dụ 12/2026 (mặc định tháng này)")
    a.add_argument("--out", help="đường dẫn file")
    a.set_defaults(fn=cmd_targets_template)

    a = sub.add_parser("targets-import", help="nạp chỉ tiêu từ file Excel")
    a.add_argument("tenant_id", type=int)
    a.add_argument("file")
    a.set_defaults(fn=cmd_targets_import)

    sub.add_parser("status", help="tình trạng đồng bộ từng NPP").set_defaults(fn=cmd_status)

    a = sub.add_parser("compare", help="so sánh các NPP đang chạy")
    a.add_argument("period", nargs="?", default="hôm nay")
    a.set_defaults(fn=cmd_compare)

    sub.add_parser("backup", help="sao lưu CSDL ngay (bot tự sao lưu lúc 03:00 mỗi ngày)").set_defaults(fn=cmd_backup)
    return p


def main(argv: list[str] | None = None) -> None:
    setup_logging(get_settings().log_level)
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
