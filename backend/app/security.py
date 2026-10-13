from cryptography.fernet import Fernet

from .config import settings


def _fernet() -> Fernet:
    if not settings.token_key:
        raise RuntimeError("TOKEN_KEY is not set")
    return Fernet(settings.token_key.encode())


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
