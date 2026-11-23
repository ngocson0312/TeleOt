import asyncio

import pytest
from openpyxl import load_workbook

from app import cli
from app.config import get_settings
from app.crypto import generate_key
from app.kiotviet.client import KiotVietClient
from app.probe import Probe, render
from tests.fakes import NOW, FakeShop


def test_probe_answers_open_questions(tmp_path):
    shop = FakeShop()
    client = KiotVietClient("nppthu", "cid", "sec", http=shop.http())
    checks = asyncio.run(Probe(client, tmp_path).run(NOW))
    answers = {c.title: (c.answer, c.ok) for c in checks}
    assert answers["Body lấy token dùng scopes hay scope"][0] == "chạy được với: scopes, scope"
    assert answers["Tên trường định mức tồn"] == ("min: minQuality, max: maxQuality", True)
    assert answers["invoiceDetails là mảng hay object"][0] == "list: 34, dict: 1"
    assert answers["Trả hàng có soldById không"][1] is True  # falls back to invoiceId
    assert "1 dòng có masterUnitId" in answers["Hàng nhiều đơn vị tính (thùng/hộp)"][0]
    assert (tmp_path / "products_day_du.json").exists()
    text = render(checks, "nppthu", NOW)
    assert "✔ Chi nhánh / kho: 2 chi nhánh" in text
    # ket-qua.txt must not leak product names or prices
    assert "Bánh quy" not in text and "12000" not in text
    # only GETs after the token calls
    assert all(r.method == "GET" for r in shop.requests if r.url.host == "public.kiotapi.com")


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")
    monkeypatch.setenv("SECRET_KEY", generate_key())
    monkeypatch.setenv("KIOTVIET_RETAILER", "nppthu")
    monkeypatch.setenv("KIOTVIET_CLIENT_ID", "cid")
    monkeypatch.setenv("KIOTVIET_CLIENT_SECRET", "topsecret")
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_cli_setup_flow(cli_env, capsys, monkeypatch):
    shop = FakeShop()
    real_init = cli.Services.__init__

    def with_fake_shop(self, settings, db, http_factory=None):
        real_init(self, settings, db, http_factory=shop.http)

    monkeypatch.setattr(cli.Services, "__init__", with_fake_shop)

    cli.main(["tenant-add", "--name", "NPP Thử", "--from-env", "--no-check"])
    cli.main(["chat-add", "-100123", "--tenant", "1"])
    cli.main(["chat-add", "-100777", "--owner"])
    cli.main(["tenant-set", "1", "report_times", "7:30,17:00"])
    cli.main(["tenant-set", "1", "low_stock_qty", "12"])
    cli.main(["sync", "1"])
    cli.main(["report", "1", "--excel"])
    cli.main(["tenant-list"])
    cli.main(["chat-list"])
    out = capsys.readouterr().out
    assert "Đã thêm NPP id=1: NPP Thử (nppthu)" in out
    assert "report_times = ['07:30', '17:00']" in out
    assert "Xong: 2 chi nhánh, 3 nhân viên, 7 mặt hàng, 35 hóa đơn, 3 phiếu trả hàng" in out
    assert "Báo cáo tồn kho · NPP Thử" in out and "<b>" not in out
    assert "-100777 · nhóm tổng (tất cả NPP)" in out
    assert "topsecret" not in out
    xlsx = next(cli_env.glob("ton-kho-npp-thu-*.xlsx"))
    assert load_workbook(xlsx).sheetnames[0] == "Sắp hết hàng"

    # the secret is stored encrypted
    import sqlite3
    raw = sqlite3.connect(cli_env / "cli.db").execute("select client_secret_enc from tenants").fetchone()[0]
    assert "topsecret" not in raw


def test_cli_tenant_key_replaces_secret(cli_env, capsys, monkeypatch):
    import sqlite3
    cli.main(["tenant-add", "--name", "NPP Thử", "--from-env", "--no-check"])
    monkeypatch.setattr("builtins.input", lambda prompt: "")  # keep the old Client ID
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "secret-moi")
    cli.main(["tenant-key", "1", "--no-check"])
    assert "Đã lưu mã mới cho NPP Thử" in capsys.readouterr().out
    svc = cli._services()
    t = svc.tenant(1)
    assert t.client_id == "cid" and svc.box.decrypt(t.client_secret_enc) == "secret-moi"
    raw = sqlite3.connect(cli_env / "cli.db").execute("select client_secret_enc from tenants").fetchone()[0]
    assert "secret-moi" not in raw
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "")
    with pytest.raises(SystemExit):
        cli.main(["tenant-key", "1", "--no-check"])


def test_cli_demo_create_refresh_remove(cli_env, capsys, monkeypatch):
    import sqlite3
    monkeypatch.setattr(cli, "now_local", lambda: NOW)
    monkeypatch.setattr("app.services.now_local", lambda: NOW)
    monkeypatch.setattr("app.sync.runner.now_local", lambda: NOW)
    cli.main(["chat-add", "-100777", "--owner"])

    def count(table, where=""):
        return sqlite3.connect(cli_env / "cli.db").execute(f"select count(*) from {table} {where}").fetchone()[0]

    cli.main(["demo", "--chat", "6555"])
    out = capsys.readouterr().out
    assert "NPP mẫu id=1" in out and "7 mặt hàng, 35 hóa đơn" in out  # synced from the built-in fake shop
    assert count("chats", "where chat_id=6555 and tenant_id=1") == 1 and count("targets") == 2

    cli.main(["demo", "--chat", "-100777"])  # refresh; the owner group stays the owner group
    assert count("tenants") == 1 and count("targets") == 2 and count("invoices") == 35
    assert count("chats", "where chat_id=6555") == 1 and count("chats", "where role='owner'") == 1

    cli.main(["demo", "--remove"])
    assert count("tenants") == 0 and count("invoices") == 0 and count("products") == 0
    assert count("chats") == 1  # only the owner group is left
    with pytest.raises(SystemExit):
        cli.main(["demo", "--remove"])


def test_cli_rejects_bad_settings(cli_env):
    cli.main(["tenant-add", "--name", "A", "--from-env", "--no-check"])
    with pytest.raises(SystemExit):
        cli.main(["tenant-set", "1", "report_times", "25:00"])
    with pytest.raises(SystemExit):
        cli.main(["tenant-set", "1", "khong_co", "1"])
    with pytest.raises(SystemExit):
        cli.main(["chat-add", "5"])


def test_cli_staff_commands(cli_env, capsys, monkeypatch):
    shop = FakeShop()
    real_init = cli.Services.__init__
    monkeypatch.setattr(cli.Services, "__init__",
                        lambda self, settings, db, http_factory=None: real_init(self, settings, db, shop.http))
    monkeypatch.setattr(cli, "now_local", lambda: NOW)
    monkeypatch.setattr("app.services.now_local", lambda: NOW)
    cli.main(["tenant-add", "--name", "NPP Thử", "--from-env", "--no-check"])
    cli.main(["sync", "1"])
    capsys.readouterr()

    cli.main(["staff-check", "1", "hôm nay"])
    out = capsys.readouterr().out
    assert "Đối soát doanh thu nhân viên · NPP Thử · hôm nay 23/11" in out
    lan = next(line for line in out.splitlines() if line.startswith("Lan "))
    assert lan.split() == ["Lan", "1", "60.000", "60.000", "0"]
    assert "revenue_mode = invoice_total" in out

    cli.main(["top", "1", "hôm nay"])
    assert "🥇 Lan: 60.000đ" in capsys.readouterr().out

    template = cli_env / "mau.xlsx"
    cli.main(["targets-template", "1", "--month", "11/2026", "--out", str(template)])
    wb = load_workbook(template)
    for row in wb["Chỉ tiêu"].iter_rows(min_row=2):
        if row[1].value == "Tuấn":
            row[5].value = 10_000_000
    wb.save(template)
    cli.main(["targets-import", "1", str(template)])
    assert "✅ Đã nạp 1 chỉ tiêu tháng 11/2026" in capsys.readouterr().out

    cli.main(["tenant-set", "1", "ranking_time", "17:30"])
    cli.main(["tenant-set", "1", "revenue_mode", "lines"])
    cli.main(["tenant-set", "1", "skip_sundays", "true"])
    out = capsys.readouterr().out
    assert "ranking_time = '17:30'" in out and "revenue_mode = 'lines'" in out and "skip_sundays = True" in out
    cli.main(["tenant-set", "1", "ranking_time", "off"])
    assert "ranking_time = None" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["tenant-set", "1", "revenue_mode", "abc"])


def test_cli_owner_group_and_operations(cli_env, capsys, monkeypatch):
    shop = FakeShop()
    real_init = cli.Services.__init__
    monkeypatch.setattr(cli.Services, "__init__",
                        lambda self, settings, db, http_factory=None: real_init(self, settings, db, shop.http))
    monkeypatch.setattr("app.services.now_local", lambda: NOW)
    monkeypatch.setattr("app.sync.runner.now_local", lambda: NOW)
    monkeypatch.setenv("BACKUP_DIR", str(cli_env / "sao-luu"))
    get_settings.cache_clear()
    cli.main(["tenant-add", "--name", "NPP Thử", "--from-env", "--no-check"])
    cli.main(["sync", "1"])
    cli.main(["chat-add", "-100777", "--owner"])
    out = capsys.readouterr().out
    assert "nhận: tóm tắt tồn kho lúc 08:00 · xếp hạng: tự động · cảnh báo gấp: không · so sánh NPP lúc 18:30" in out

    cli.main(["chat-set", "-100777", "summary_times", "7:00,17:30"])
    cli.main(["chat-set", "-100777", "urgent", "true"])
    cli.main(["chat-set", "-100777", "compare_time", "off"])
    cli.main(["chat-set", "-100777", "ranking", "auto"])
    cli.main(["chat-list"])
    out = capsys.readouterr().out
    assert "summary_times = ['07:00', '17:30']" in out and "compare_time = None" in out
    assert "tóm tắt tồn kho lúc 07:00, 17:30 · xếp hạng: tự động · cảnh báo gấp: có · so sánh NPP: tắt" in out
    for bad in (["inventory", "het"], ["khong_co", "1"], ["compare_time", "25:00"]):
        with pytest.raises(SystemExit):
            cli.main(["chat-set", "-100777", *bad])

    cli.main(["status"])
    cli.main(["compare", "hôm nay"])
    cli.main(["backup"])
    out = capsys.readouterr().out
    assert "✅ NPP Thử (id 1): đồng bộ lúc 08:00 23/11" in out
    assert "So sánh NPP · hôm nay 23/11" in out and "<b>" not in out
    assert "Đã sao lưu:" in out and len(list((cli_env / "sao-luu").glob("teleot-*.db"))) == 1
