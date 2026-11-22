"""Build the phone-sized PDF and one PNG per page from gioi-thieu.html.

Run:  python tai-lieu/nguon/build.py
Needs Google Chrome (or Edge) and PyMuPDF (pip install pymupdf).
"""
import pathlib
import re
import subprocess
import sys
import tempfile

import pymupdf as fitz

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent
HTML = HERE / "gioi-thieu.html"
PDF = OUT / "Tro-ly-bao-cao-KiotViet-Telegram.pdf"
IMG_DIR = OUT / "anh-gui-zalo"
IMG_WIDTH = 1080  # px, a typical phone screen width

# Chrome first: headless Edge on this machine exits without writing anything.
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for b in BROWSERS:
        if pathlib.Path(b).exists():
            return b
    sys.exit("Edge or Chrome not found")


def run_browser(browser: str, profile: str, *args: str) -> subprocess.CompletedProcess:
    cmd = [browser, "--headless=new", "--disable-gpu", f"--user-data-dir={profile}", *args, HTML.as_uri()]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def main() -> None:
    browser = find_browser()
    # The browser can keep a lock on its profile for a moment after exiting.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        dom = run_browser(browser, profile, "--dump-dom").stdout
        m = re.search(r'data-overflow="([^"]*)"', dom)
        overflow = m.group(1) if m else "unknown"
        if overflow != "none":
            print(f"WARNING: content overflows on page(s): {overflow}")
        run_browser(browser, profile, "--no-pdf-header-footer", f"--print-to-pdf={PDF}")

    doc = fitz.open(PDF)
    IMG_DIR.mkdir(exist_ok=True)
    for old in IMG_DIR.glob("trang-*.png"):
        old.unlink()
    for i, page in enumerate(doc, start=1):
        zoom = IMG_WIDTH / page.rect.width
        page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(IMG_DIR / f"trang-{i:02d}.png")
    print(f"PDF: {PDF} ({doc.page_count} pages)")
    print(f"Images: {IMG_DIR}")


if __name__ == "__main__":
    main()
