"""Running 24/7: one bot per machine, log file, install scripts that survive git on Windows."""
import logging
import shutil
import subprocess
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from app import main as main_mod
from app.config import Settings, get_settings, log_handlers
from app.crypto import generate_key
from app.singleton import acquire_lock

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def test_lock_allows_one_holder(tmp_path):
    path = tmp_path / "data" / "bot.lock"
    first = acquire_lock(path)
    assert first is not None
    assert acquire_lock(path) is None
    first.close()  # the OS also releases it when the process dies
    again = acquire_lock(path)
    assert again is not None
    again.close()


def test_second_bot_exits_with_code_3(tmp_path, monkeypatch):
    lock_path = tmp_path / "bot.lock"
    monkeypatch.setattr(main_mod, "LOCK_FILE", lock_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("SECRET_KEY", generate_key())
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    get_settings.cache_clear()
    held = acquire_lock(lock_path)
    try:
        with pytest.raises(SystemExit) as e:
            main_mod.main()
        assert e.value.code == main_mod.ALREADY_RUNNING
        assert not (tmp_path / "x.db").exists()  # stopped before touching the database
    finally:
        held.close()
        get_settings.cache_clear()


def test_log_file_setting(tmp_path):
    assert Settings(_env_file=None, log_file="").log_file is None
    [console] = log_handlers(None)
    assert type(console) is logging.StreamHandler
    [handler] = log_handlers(tmp_path / "logs" / "teleot.log")
    try:
        assert isinstance(handler, TimedRotatingFileHandler) and handler.encoding == "utf-8"
        assert handler.backupCount == 14 and (tmp_path / "logs").is_dir()
    finally:
        handler.close()


def test_install_scripts_keep_their_format():
    for name in ("cai-dat-linux.sh", "teleot.service"):
        assert b"\r" not in (DEPLOY / name).read_bytes(), f"{name} must use LF line endings"
    for ps1 in DEPLOY.glob("*.ps1"):  # Windows PowerShell 5.1 reads BOM-less files as ANSI
        assert ps1.read_bytes().startswith(b"\xef\xbb\xbf"), f"{ps1.name} needs a UTF-8 BOM"
    service = (DEPLOY / "teleot.service").read_text(encoding="utf-8")
    assert "@APP_DIR@/.venv/bin/python -m app.main" in service and "Restart=always" in service


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_linux_script_parses():
    subprocess.run(["bash", "-n", str(DEPLOY / "cai-dat-linux.sh")], check=True)
