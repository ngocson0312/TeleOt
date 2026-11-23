"""Stand-ins for the python-telegram-bot objects the handlers touch."""
from __future__ import annotations

from types import SimpleNamespace

USER = 42


class FakeMessage:
    def __init__(self, text="", caption=None, document=None):
        self.text = text
        self.caption = caption
        self.document = document
        self.replies: list[tuple[str, dict]] = []
        self.docs: list[tuple[str, bytes, str | None]] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))

    async def reply_document(self, document, filename=None, caption=None, **kw):
        self.docs.append((filename, document, caption))


class FakeBot:
    def __init__(self, member_status="administrator"):
        self.sent: list[tuple[int, str]] = []
        self.docs: list[tuple[int, str]] = []
        self.member_status = member_status

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def send_document(self, chat_id, document, filename=None, **kw):
        assert document[:2] == b"PK"  # xlsx is a zip
        self.docs.append((chat_id, filename))

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.member_status)


def fake_document(data: bytes, name="chi-tieu.xlsx"):
    async def download_as_bytearray():
        return bytearray(data)

    async def get_file():
        return SimpleNamespace(download_as_bytearray=download_as_bytearray)

    return SimpleNamespace(file_name=name, file_size=len(data), get_file=get_file)


def update_for(chat_id, text="", *, chat_type="supergroup", user=USER, caption=None, document=None):
    msg = FakeMessage(text, caption, document)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
                             effective_user=SimpleNamespace(id=user), effective_message=msg, callback_query=None)
    return update, msg


def context_for(svc, args=(), bot=None, chat_data=None):
    return SimpleNamespace(application=SimpleNamespace(bot_data={"svc": svc}), args=list(args),
                           bot=bot or FakeBot(), chat_data={} if chat_data is None else chat_data)
