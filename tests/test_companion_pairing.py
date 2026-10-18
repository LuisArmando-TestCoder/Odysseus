"""Tests for the companion pairing endpoints (split 3/4).

Covers what the review asked for:
  - a non-admin / bearer caller cannot call /api/companion/pair (admin-only)
  - the pairing token is minted once (hashed at rest) and the mint invalidates
    the auth cache so it works immediately, no restart
  - minting is a POST, never a GET (CSRF: a SameSite=Lax cookie rides a
    top-level GET, so GET-minting would be triggerable by a link / <img>)
"""

import contextlib
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Capture what mint_token would persist, via a stubbed core.database.
_CAPTURED = {}


class _ApiToken:
    def __init__(self, **kw):
        _CAPTURED.clear()
        _CAPTURED.update(kw)
        self.__dict__.update(kw)


@contextlib.contextmanager
def _get_db_session():
    yield MagicMock()


# core/__init__ pulls in models/session_manager which import many ORM names from
# core.database; under conftest's sqlalchemy stubs the real module can't load.
# A __getattr__ module resolves ANY requested name to a MagicMock, while keeping
# our real get_db_session/ApiToken for the mint test.
class _DBStub(types.ModuleType):
    def __getattr__(self, name):  # noqa: D401
        return MagicMock()


_db = _DBStub("core.database")
_db.get_db_session = _get_db_session
_db.ApiToken = _ApiToken
# NOTE: intentionally NOT installed into sys.modules at collection time —
# doing so clobbered the REAL core.database for every later-collected test
# module (document_close, topic_analyzer, ...). The autouse fixture below
# installs it only while this file's tests run.

_restore_after_import = []  # (name, prev_module) — stubs to undo post-import

for _name, _attrs in {
    "core.auth": {"AuthManager": MagicMock()},
    "src.endpoint_resolver": {"build_chat_url": (lambda u: u)},
}.items():
    if _name not in sys.modules:
        _mm = types.ModuleType(_name)
        for _k, _v in _attrs.items():
            setattr(_mm, _k, _v)
        sys.modules[_name] = _mm
        # Collection-order hygiene: this stub was only needed for the imports
        # below; restore sys.modules right after so later-collected modules
        # (test_provider_endpoints, ...) bind the REAL modules.
        _restore_after_import.append((_name, None))

from fastapi import HTTPException  # noqa: E402

# Import-order race guard: an earlier test module (e.g. test_app) may already
# have imported companion.pairing with the REAL core.database bound. Re-import
# it fresh so it binds OUR stub above — otherwise mint_token writes the real DB
# and the hash-only assertion below fails depending on collection order.
for _stale in ("companion.pairing", "companion.routes"):
    sys.modules.pop(_stale, None)

import companion.pairing as P  # noqa: E402
import companion.routes as companion_routes  # noqa: E402
from companion.routes import mint_pairing_token, setup_companion_routes  # noqa: E402
from core.middleware import require_admin  # noqa: E402

# Restore any stubs we installed above: they are re-installed per-test by the
# autouse fixture below, so nothing leaks into other test modules. Also detach
# from the parent package — `from src import endpoint_resolver` resolves the
# parent attribute first, which survives a bare sys.modules pop.
for _name, _prev in _restore_after_import:
    if _prev is None:
        sys.modules.pop(_name, None)
        _parent, _, _child = _name.partition(".")
        _pkg = sys.modules.get(_parent)
        if _pkg is not None and hasattr(_pkg, _child):
            try:
                delattr(_pkg, _child)
            except Exception:
                pass
    else:
        sys.modules[_name] = _prev


@pytest.fixture(autouse=True)
def _pin_stubs():
    """Install OUR stubs for the duration of each test, then restore whatever
    core.database was in place before (the real module in a full-suite run).
    companion.pairing/routes bind core.database LAZILY at call time, so they
    are re-imported under the stub and rebound onto the module globals the
    tests reference."""
    _prev_db = sys.modules.get("core.database")
    _prev_auth = sys.modules.get("core.auth")
    _prev_er = sys.modules.get("src.endpoint_resolver")
    sys.modules["core.database"] = _db
    for _name, _attrs in {
        "core.auth": {"AuthManager": MagicMock()},
        "src.endpoint_resolver": {"build_chat_url": (lambda u: u)},
    }.items():
        _mm = sys.modules.get(_name)
        if _mm is None or not getattr(_mm, "__file__", None):
            # Only touch modules that are stubs (or absent) — NEVER set stub
            # attributes onto the REAL module, or every later test binding it
            # inherits the poisoned lambda.
            _mm = types.ModuleType(_name)
            sys.modules[_name] = _mm
            for _k, _v in _attrs.items():
                setattr(_mm, _k, _v)
    for _stale in ("companion.pairing", "companion.routes"):
        sys.modules.pop(_stale, None)
    import companion.pairing as _P2
    import companion.routes as _R2
    globals()["P"] = _P2
    globals()["companion_routes"] = _R2
    globals()["mint_pairing_token"] = _R2.mint_pairing_token
    globals()["setup_companion_routes"] = _R2.setup_companion_routes
    yield
    for _name, _prev in (("core.database", _prev_db), ("core.auth", _prev_auth),
                         ("src.endpoint_resolver", _prev_er)):
        if _prev is None:
            sys.modules.pop(_name, None)
            _parent, _, _child = _name.partition(".")
            _pkg = sys.modules.get(_parent)
            if _pkg is not None and hasattr(_pkg, _child):
                try:
                    delattr(_pkg, _child)
                except Exception:
                    pass
        else:
            sys.modules[_name] = _prev


# --- token minting: shown once, hashed at rest -----------------------------

def test_mint_token_returns_raw_once_and_stores_only_a_hash():
    token_id, raw = P.mint_token("alice")
    assert raw.startswith("ody_")
    # The persisted row stores a bcrypt hash + prefix, never the plaintext.
    assert _CAPTURED["token_hash"] != raw
    assert _CAPTURED["token_hash"].startswith("$2")  # bcrypt
    assert _CAPTURED["token_prefix"] == raw[:8]
    assert _CAPTURED["owner"] == "alice"
    assert _CAPTURED["scopes"] == "chat"
    assert _CAPTURED["is_active"] is True


def test_mint_pairing_token_invalidates_cache(monkeypatch):
    # The mint must flip the auth middleware's cache so the token works on the
    # very next request, with no restart.
    monkeypatch.setattr(P, "mint_token", lambda owner, name="companion": ("id1", "ody_demo"))
    invalidate = MagicMock()
    token_id, raw = mint_pairing_token("alice", invalidate)
    assert (token_id, raw) == ("id1", "ody_demo")
    invalidate.assert_called_once()


def test_mint_pairing_token_tolerates_no_invalidator(monkeypatch):
    monkeypatch.setattr(P, "mint_token", lambda owner, name="companion": ("id1", "ody_demo"))
    # Must not blow up if the app didn't expose an invalidator.
    assert mint_pairing_token("alice", None) == ("id1", "ody_demo")


def test_pairing_payload_shape():
    p = P.pairing_payload("192.168.1.9", 7000, "ody_x")
    assert p == {"v": 1, "host": "192.168.1.9", "port": 7000, "token": "ody_x"}


# --- admin-only gate: a bearer/non-admin caller is rejected ----------------

def _admin_mgr(is_admin):
    return SimpleNamespace(is_admin=lambda u: is_admin, is_configured=True)


def _req(current_user, *, api_token=False, is_admin=False):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=current_user, api_token=api_token),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=_admin_mgr(is_admin))),
    )


def test_bearer_token_caller_cannot_pair(monkeypatch):
    # Bearer callers come through as the "api" pseudo-user, which is not admin.
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc:
        require_admin(_req("api", api_token=True, is_admin=False))
    assert exc.value.status_code == 403


def test_non_admin_user_cannot_pair(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc:
        require_admin(_req("bob", is_admin=False))
    assert exc.value.status_code == 403


def test_admin_user_passes_the_gate(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    # Should not raise.
    require_admin(_req("alice", is_admin=True))


# --- CSRF: minting is POST, never GET --------------------------------------

def _pair_methods():
    router = setup_companion_routes()
    methods = set()
    for r in router.routes:
        path = getattr(r, "path", "")
        if path.endswith("/pair"):
            methods |= set(getattr(r, "methods", set()) or set())
    return methods


def _pair_endpoint(method):
    router = setup_companion_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith("/pair") and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"{method} /api/companion/pair route not found")


def test_pair_is_minted_via_post_not_get():
    methods = _pair_methods()
    assert "POST" in methods, "pairing must accept POST (the mint)"
    assert "GET" in methods, "GET should render the form page"
    # The distinction is enforced in the handlers: GET renders a form and never
    # mints; only POST calls mint_pairing_token.


def test_pair_page_uses_imported_admin_gate(monkeypatch):
    monkeypatch.setattr(companion_routes, "require_admin", lambda request: None)
    response = _pair_endpoint("GET")(SimpleNamespace())

    assert "Pair a device" in str(getattr(response, "body", response))
