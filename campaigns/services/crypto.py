"""
Symmetric encryption for SMTP passwords stored in the database.

Uses Fernet (AES-128 in CBC mode with an HMAC) keyed from FERNET_KEY in
settings, which itself comes from the .env file and is never committed
to source control. Losing/rotating FERNET_KEY makes previously stored
passwords unreadable, so back it up if you rotate it.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    return Fernet(settings.FERNET_KEY.encode() if isinstance(settings.FERNET_KEY, str) else settings.FERNET_KEY)


def encrypt(plain_text: str) -> str:
    if not plain_text:
        return ""
    return _fernet().encrypt(plain_text.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError(
            "Could not decrypt the stored SMTP password. The FERNET_KEY in "
            ".env may have changed since it was saved."
        )
