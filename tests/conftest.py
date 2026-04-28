import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """Per-test isolated config: fresh SQLite + fresh keys."""
    from cryptography.fernet import Fernet

    from boniforce_mcp import auth as auth_mod
    from boniforce_mcp.config import get_settings

    db = tmp_path / "test.sqlite"
    monkeypatch.setenv("BF_DB_PATH", str(db))
    monkeypatch.setenv("BF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BF_OAUTH_SIGNING_KEY", auth_mod.generate_signing_key_pem())
    monkeypatch.setenv("BF_ISSUER_URL", "http://testserver")
    monkeypatch.setenv("BF_API_BASE", "https://api.boniforce.de")
    monkeypatch.setenv("BF_JWT_AUDIENCE", "boniforce-mcp")

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
