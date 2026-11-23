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
