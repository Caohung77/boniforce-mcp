from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class CryptoError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise CryptoError("BF_ENCRYPTION_KEY is not set; run `boniforce-mcp genkey`.")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("Stored token cannot be decrypted (wrong key?).") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()
