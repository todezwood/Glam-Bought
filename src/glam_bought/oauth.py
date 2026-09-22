"""OAuth2 authorization-code flow shared by Oura and Google. One token file per provider.

Tokens are always re-read from disk (Oura refresh tokens are single-use, so a stale in-memory
copy is fatal), written atomically, and refreshed under a per-provider lock.
"""
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import config


class NotConnected(RuntimeError):
    """No usable token: the user must (re)connect."""


class Unavailable(RuntimeError):
    """Transient trouble at the token endpoint or network; the stored token is kept."""


PROVIDERS = {
    "oura": dict(
        auth_url="https://cloud.ouraring.com/oauth/authorize",
        token_url="https://api.ouraring.com/oauth/token",
        scope="daily heartrate personal",
        client_id=lambda: config.OURA_CLIENT_ID,
        client_secret=lambda: config.OURA_CLIENT_SECRET,
        extra_auth={},
    ),
    "google": dict(
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="https://www.googleapis.com/auth/calendar.events",
        client_id=lambda: config.GOOGLE_CLIENT_ID,
        client_secret=lambda: config.GOOGLE_CLIENT_SECRET,
        extra_auth={"access_type": "offline", "prompt": "consent"},
    ),
}
_states: dict[str, str] = {}  # provider -> pending state nonce
_locks = {p: threading.Lock() for p in PROVIDERS}
TIMEOUT = 15
_REVOKED = ("invalid_grant", "invalid_client", "invalid_token")


def token_path(provider: str) -> Path:
    return config.TOKENS_DIR / f"{provider}.json"


def configured(provider: str) -> bool:
    spec = PROVIDERS[provider]
    return bool(spec["client_id"]() and spec["client_secret"]())


def connected(provider: str) -> bool:
    try:
        return bool(_load(provider).get("access_token"))
    except NotConnected:
        return False


def redirect_uri(provider: str) -> str:
    return f"{config.OAUTH_BASE_URL}/oauth/{provider}/callback"


def authorize_url(provider: str) -> str:
    spec = PROVIDERS[provider]
    state = secrets.token_urlsafe(16)
    _states[provider] = state
    params = {
        "response_type": "code",
        "client_id": spec["client_id"](),
        "redirect_uri": redirect_uri(provider),
        "scope": spec["scope"],
        "state": state,
        **spec["extra_auth"],
    }
    return f"{spec['auth_url']}?{urlencode(params)}"


def exchange_code(provider: str, code: str, state: str) -> dict:
    expected = _states.pop(provider, None)
    if not expected:
        raise ValueError(f"no pending OAuth state; start again at /connect/{provider}")
    if state != expected:
        raise ValueError("state mismatch")
    spec = PROVIDERS[provider]
    r = _post_token(spec, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(provider),
        "client_id": spec["client_id"](),
        "client_secret": spec["client_secret"](),
    })
    if r.status_code != 200:
        raise Unavailable(f"{provider} token exchange failed ({r.status_code}): {r.text[:200]}")
    return _save(provider, r.json(), old=None)


def refresh(provider: str) -> dict:
    """Force one refresh (used by the smoke test to prove rotation + persistence)."""
    with _locks[provider]:
        return _refresh(provider, _load(provider), force=True)


def access_token(provider: str) -> str:
    with _locks[provider]:
        tok = _load(provider)
        if tok.get("expires_at", 0) - 60 < time.time():
            tok = _refresh(provider, tok)
        return tok["access_token"]


def disconnect(provider: str) -> None:
    path = token_path(provider)
    if path.exists():
        path.unlink()


def request(provider: str, method: str, url: str, **kw) -> requests.Response:
    """Bearer request. On 401, refresh once (if nobody else already did) and retry once."""
    kw.setdefault("timeout", TIMEOUT)
    used = access_token(provider)
    r = _send(method, url, used, **kw)
    if r.status_code != 401:
        return r
    with _locks[provider]:
        tok = _load(provider)
        if tok.get("access_token") == used:
            tok = _refresh(provider, tok, force=True)
        fresh = tok["access_token"]
    r = _send(method, url, fresh, **kw)
    if r.status_code == 401:
        raise NotConnected(f"{provider} rejected the token twice; reconnect at /connect/{provider}")
    return r


def _send(method, url, token, **kw):
    headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {token}"}
    try:
        return requests.request(method, url, headers=headers, **kw)
    except requests.RequestException as e:
        raise Unavailable(f"network error: {e}") from e


def _post_token(spec, data):
    try:
        return requests.post(spec["token_url"], data=data, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise Unavailable(f"token endpoint unreachable: {e}") from e


def _seed_from_env(provider: str, path: Path) -> None:
    """In a container tokens/ starts empty and is wiped on restart. If <PROVIDER>_TOKEN_JSON holds the
    laptop's token file (one line of JSON), write it to disk once so the app starts connected."""
    raw = os.getenv(f"{provider.upper()}_TOKEN_JSON", "").strip()
    if not raw:
        return
    try:
        tok = json.loads(raw)
    except ValueError:
        return
    if not isinstance(tok, dict) or not tok.get("access_token"):
        return
    config.TOKENS_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(tok, indent=2))
    os.replace(tmp, path)


def _load(provider: str) -> dict:
    path = token_path(provider)
    if not path.exists():
        _seed_from_env(provider, path)
    if not path.exists():
        raise NotConnected(f"{provider} is not connected; open /connect/{provider} on the laptop")
    try:
        tok = json.loads(path.read_text() or "{}")
    except ValueError:
        tok = {}
    if not tok.get("access_token"):
        raise NotConnected(f"{provider} token file is empty; reconnect at /connect/{provider}")
    return tok


def _save(provider: str, new: dict, old: dict | None) -> dict:
    spec = PROVIDERS[provider]
    tok = {
        "access_token": new["access_token"],
        "refresh_token": new.get("refresh_token") or (old or {}).get("refresh_token"),
        "expires_at": time.time() + float(new.get("expires_in", 3600)),
        "token_type": new.get("token_type", "Bearer"),
        "scope": new.get("scope", spec["scope"]),
        "obtained_at": config.local_now().isoformat(),
    }
    config.TOKENS_DIR.mkdir(exist_ok=True)
    path = token_path(provider)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(tok, indent=2))
    os.replace(tmp, path)
    return tok


def _refresh(provider: str, tok: dict, force: bool = False) -> dict:
    """Caller holds the provider lock. Re-reads disk first: another thread/process may have refreshed."""
    tok = _load(provider)
    if not force and tok.get("expires_at", 0) - 60 > time.time():
        return tok
    if not tok.get("refresh_token"):
        disconnect(provider)
        raise NotConnected(f"{provider} has no refresh token; reconnect at /connect/{provider}")
    spec = PROVIDERS[provider]
    r = _post_token(spec, {
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": spec["client_id"](),
        "client_secret": spec["client_secret"](),
    })
    if r.status_code == 200:
        return _save(provider, r.json(), old=tok)
    body = r.text[:300]
    if r.status_code in (400, 401) and any(k in body for k in _REVOKED):
        disconnect(provider)
        raise NotConnected(f"{provider} token revoked or expired; reconnect at /connect/{provider}")
    raise Unavailable(f"{provider} refresh failed ({r.status_code}): {body}")
