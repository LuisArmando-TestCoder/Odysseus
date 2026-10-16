import sys

# conftest.py stubs src.database with a fake module; webhook_manager imports
# `from src.database import SessionLocal, Webhook`, and src.database re-exports
# `from core.database import *`. When run alphabetically after other tests,
# `core.database` is left as a MagicMock stub whose auto-generated `__all__` is
# itself a MagicMock, so `from core.database import *` raises:
#   TypeError: Item in core.database.__all__ must be str, not MagicMock
# A real module has `__file__` set to a str, whereas the stubs (MagicMock / bare
# ModuleType) do not. Drop the polluted stub so the real core.database — with a
# valid `__all__` — loads when src.database imports it.
_core_db = sys.modules.get("core.database")
if _core_db is not None and not isinstance(getattr(_core_db, "__file__", None), str):
    sys.modules.pop("core.database", None)

if "src.database" in sys.modules:
    del sys.modules["src.database"]

import pytest
from src.webhook_manager import validate_webhook_url


def test_webhook_url_ssrf_mitigation():
    # SSRF bypasses that must be rejected, including IPv6 unspecified and
    # IPv4-mapped IPv6 (loopback + cloud metadata).
    private_urls = [
        "http://[::]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://127.0.0.1/",
        "http://0.0.0.0/",
    ]
    for url in private_urls:
        with pytest.raises(ValueError) as exc:
            validate_webhook_url(url)
        assert "private/internal addresses" in str(exc.value)

    # A clearly public IP literal must still be accepted.
    public_url = "http://93.184.216.34/"
    assert validate_webhook_url(public_url) == public_url
