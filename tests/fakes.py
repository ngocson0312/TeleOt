"""The demo shop (app/demo.py) pinned to a fixed day, so test numbers never move."""
from __future__ import annotations

from datetime import date, datetime

from app.demo import DemoShop

TODAY = date(2026, 11, 23)
NOW = datetime(2026, 11, 23, 8, 0, 0)


class FakeShop(DemoShop):
    def __init__(self):
        super().__init__(TODAY)
