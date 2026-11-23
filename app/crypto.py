"""Encrypt KiotViet client secrets at rest with a Fernet key from SECRET_KEY."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


class SecretBox:
    def __init__(self, key: str):
        if not key:
            raise CryptoError("Thiếu SECRET_KEY trong .env. Tạo bằng: python -m app.cli gen-key")
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as e:
            raise CryptoError("SECRET_KEY không hợp lệ. Tạo lại bằng: python -m app.cli gen-key") from e

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as e:
            raise CryptoError("Không giải mã được client secret: SECRET_KEY đã đổi?") from e
