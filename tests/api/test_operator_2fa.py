"""Operator sign-in over the internet: TOTP (RFC 6238), recovery codes, short sessions in public
mode, sign out everywhere, browser-session cookies, login audit. Fakes only, no lab."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "actions"))
from test_engine import box  # noqa: E402,F401
from test_public_view import app_for, cl  # noqa: E402
from test_tester_passes import PW, csrf, otp, world  # noqa: E402,F401

from shimpaku.api import totp as T  # noqa: E402
from shimpaku.api.auth import AuthStore  # noqa: E402

_ = (box, world)

# ---------------------------------------------------------------- RFC 6238 Appendix B (SHA-1)
SEED = b"12345678901234567890"
RFC_VECTORS = [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
               (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")]


@pytest.mark.parametrize("t,code", RFC_VECTORS)
def test_rfc6238_appendix_b_sha1_vectors(t, code):
    assert T.totp(SEED, t, digits=8) == code


def test_six_digit_codes_are_the_last_six_of_the_rfc_vectors():
    for t, code in RFC_VECTORS:
        assert T.totp(SEED, t) == code[-6:]


def test_rfc4226_appendix_d_hotp_vectors():
    want = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"]
    assert [T.hotp(SEED, i) for i in range(10)] == want


def test_base32_round_trip_and_uri():
    s = T.b32_secret(SEED)
    assert T.b32_key(s) == SEED and T.b32_key(s.lower()) == SEED
    uri = T.otpauth_uri(s)
    assert uri.startswith("otpauth://totp/Shimpaku:operator?") and "issuer=Shimpaku" in uri and f"secret={s}" in uri


# ---------------------------------------------------------------- store
@pytest.fixture
def store(tmp_path):
    st = T.TotpStore(tmp_path / "totp.json")
    out = st.enable()
    st.t = 1_800_000_000.0
    st.clock = lambda: st.t
    st.key = T.b32_key(out["secret"])
    st.codes = out["recovery_codes"]
    st.secret = out["secret"]
    return st


def test_state_file_is_0600_and_holds_no_plain_recovery_codes(store):
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600
    raw = store.path.read_text()
    assert all(c not in raw and c.replace("-", "") not in raw for c in store.codes)
    assert store.secret not in repr(store)
    assert len(store.codes) == 8 and len(set(store.codes)) == 8


def test_window_is_one_step_either_side(store):
    t = store.t
    assert store.verify_code(T.totp(store.key, t - 30))
    store.t += 90  # a fresh period so the step above is not "already used"
    assert store.verify_code(T.totp(store.key, store.t + 30))
    store.t += 300
    assert not store.verify_code(T.totp(store.key, store.t - 60))
    assert not store.verify_code(T.totp(store.key, store.t + 60))


def test_replay_of_a_code_is_refused(store):
    c = T.totp(store.key, store.t)
    assert store.verify_code(c)
    assert not store.verify_code(c)            # same code, same window
    store.t += 15
    assert not store.verify_code(c)            # still inside its window
    store.t += 30
    assert not store.verify_code(T.totp(store.key, store.t - 30))  # an older or equal step
    assert store.verify_code(T.totp(store.key, store.t))


def test_replay_state_survives_a_restart(store):
    c = T.totp(store.key, store.t)
    assert store.verify_code(c)
    again = T.TotpStore(store.path)
    again.clock = store.clock
    assert not again.verify_code(c)


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34 5", None])
def test_malformed_codes_fail(store, bad):
    assert not store.verify_code(bad)


def test_recovery_code_is_single_use(store):
    c = store.codes[3]
    assert store.use_recovery(c.upper())  # case and dash tolerant
    assert not store.use_recovery(c)
    assert store.status()["recovery_codes_left"] == 7
    assert not store.use_recovery("nope-nope")


def test_disable_removes_the_secret(store):
    assert store.disable() and not store.enabled and not store.verify_code(T.totp(store.key, store.t))


# ---------------------------------------------------------------- CLI
def test_cli_enable_status_disable(tmp_path, monkeypatch):
    from shimpaku.api.cli_admin import admin_app
    from shimpaku.config import settings

    monkeypatch.setattr(settings, "data", tmp_path)
    r = CliRunner().invoke(admin_app, ["totp", "enable"])
    assert r.exit_code == 0, r.output
    assert "otpauth://totp/Shimpaku:operator?" in r.output
    assert "no QR library installed" in r.output or "█" in r.output or "▀" in r.output
    assert stat.S_IMODE(os.stat(tmp_path / "totp.json").st_mode) == 0o600
    codes = [ln.strip() for ln in r.output.splitlines() if ln.startswith("  ") and "-" in ln]
    assert len(codes) == 8
    assert CliRunner().invoke(admin_app, ["totp", "enable"]).exit_code == 2  # no silent replace
    assert "8 recovery codes left" in CliRunner().invoke(admin_app, ["totp", "status"]).output
    assert CliRunner().invoke(admin_app, ["totp", "disable"]).exit_code == 0
    assert "disabled" in CliRunner().invoke(admin_app, ["totp", "status"]).output


# ---------------------------------------------------------------- the login route
def login(c, **kw):
    return c.post("/api/login", json={"password": PW, **kw}, headers=csrf(c))


def test_public_mode_requires_totp_enabled(pv_world):
    w = pv_world
    w.totp.disable()
    c = cl(w.pv)
    r = login(c)
    assert r.status_code == 403 and "shimpaku admin totp enable" in r.json()["detail"]
    assert c.get("/api/whoami").json()["role"] == "visitor"


def test_public_mode_needs_the_code(pv_world):
    w = pv_world
    c = cl(w.pv)
    assert c.get("/api/whoami").json()["totp_required"] is True
    assert login(c).status_code == 401
    assert login(c, code="000000").status_code == 401
    assert login(c, code=otp(w)).status_code == 200
    assert c.get("/api/whoami").json()["role"] == "operator"


def test_wrong_password_with_a_good_code_fails(pv_world):
    w = pv_world
    c = cl(w.pv)
    r = c.post("/api/login", json={"password": "wrong", "code": otp(w)}, headers=csrf(c))
    assert r.status_code == 401 and r.json()["detail"] == "wrong password or code"


def test_code_reuse_on_the_route_is_refused(pv_world):
    w = pv_world
    code = otp(w)
    assert login(cl(w.pv), code=code).status_code == 200
    assert login(cl(w.pv), code=code).status_code == 401


def test_recovery_code_signs_in_once(pv_world):
    w = pv_world
    rc = w.totp.enable()
    w.totp_secret = rc["secret"]
    r = login(cl(w.pv), recovery_code=rc["recovery_codes"][0])
    assert r.status_code == 200 and r.json()["recovery_codes_left"] == 7
    assert login(cl(w.pv), recovery_code=rc["recovery_codes"][0]).status_code == 401


def test_totp_optional_off_public_but_enforced_when_enabled(world, tmp_path):  # noqa: F811
    off = T.TotpStore(tmp_path / "none.json")
    app = app_for(world, public=False)
    from shimpaku import service
    from shimpaku.api.app import create_app
    from shimpaku.api.ratelimit import RateLimiter

    plain = create_app(service, auth_mode="local", web_dist=world.tmp / "dist", leases=world.lm,
                       limiter=RateLimiter({"write": (1000, 1000)}), public_view=False, totp=off)
    c = cl(plain)
    assert c.get("/api/whoami").json()["totp_required"] is False
    assert login(c).status_code == 200
    c2 = cl(app)  # TOTP enabled, public view off: still enforced
    assert login(c2).status_code == 401
    assert login(c2, code=otp(world)).status_code == 200


def test_secret_never_in_api_responses(pv_world):
    w = pv_world
    c = cl(w.pv)
    assert login(c, code=otp(w)).status_code == 200
    blobs = [c.get(p).text for p in ("/api/whoami", "/api/settings", "/api/audit?limit=1000")]
    assert all(w.totp_secret not in b for b in blobs)
    assert json.loads(blobs[1])["totp_enabled"] is True


# ---------------------------------------------------------------- sessions and cookies
def test_public_session_cookie_is_not_persistent(pv_world):
    w = pv_world
    c = cl(w.pv)
    r = login(c, code=otp(w))
    sc = [h for h in r.headers.get_list("set-cookie") if h.startswith("shimpaku_session=")]
    assert sc and "max-age" not in sc[0].lower() and "expires" not in sc[0].lower()
    assert "httponly" in sc[0].lower()


def test_public_operator_idle_and_absolute_expiry(pv_world):
    w = pv_world
    now = [1_800_000_000.0]
    w.auth.session_clock = lambda: now[0]
    c = cl(w.pv)
    assert login(c, code=otp(w)).status_code == 200
    now[0] += 29 * 60
    assert c.get("/api/whoami").json()["role"] == "operator"
    now[0] += 31 * 60  # 31 min idle
    assert c.get("/api/whoami").json()["role"] == "visitor"

    c = cl(w.pv)
    assert login(c, code=otp(w)).status_code == 200
    for _ in range(9):  # active every 25 min: never idle, but 4 h is the cap
        now[0] += 25 * 60
        assert c.get("/api/whoami").json()["role"] == "operator"
    now[0] += 25 * 60  # 250 min > 240
    assert c.get("/api/whoami").json()["role"] == "visitor"


def test_operator_session_limits_are_configurable(pv_world, monkeypatch):
    from shimpaku.config import settings

    monkeypatch.setattr(settings, "operator_idle_min", 5)
    w = pv_world
    w.pv = app_for(w)
    now = [1_800_000_000.0]
    w.auth.session_clock = lambda: now[0]
    c = cl(w.pv)
    assert login(c, code=otp(w)).status_code == 200
    now[0] += 6 * 60
    assert c.get("/api/whoami").json()["role"] == "visitor"


def test_private_mode_keeps_the_long_sessions(world, tmp_path):  # noqa: F811
    now = [1_800_000_000.0]
    world.auth.session_clock = lambda: now[0]
    c = cl(app_for(world, public=False))
    assert login(c, code=otp(world)).status_code == 200
    now[0] += 11 * 3600
    assert c.get("/api/whoami").json()["user"] == "admin"


def test_logout_all_revokes_every_operator_session_not_testers(pv_world):
    w = pv_world
    a, b = cl(w.pv), cl(w.pv)
    assert login(a, code=otp(w)).status_code == 200
    assert login(b, code=otp(w)).status_code == 200
    tester_sid = w.auth.create_session("tester:x", role="tester", lease_id="L1")
    r = a.post("/api/logout-all", headers=csrf(a))
    assert r.status_code == 200 and r.json()["sessions"] == 2
    assert a.get("/api/whoami").json()["role"] == "visitor"
    assert b.get("/api/whoami").json()["role"] == "visitor"
    assert w.auth.session_record(tester_sid) is not None
    rows = [x for x in w.audit.query(limit=1000) if x.get("method") == "logout_all" or x.get("tool") == "logout_all"]
    assert rows


def test_logout_all_is_operator_only(pv_world):
    c = cl(pv_world.pv)
    assert c.post("/api/logout-all", headers=csrf(c)).status_code == 403


# ---------------------------------------------------------------- audit and rate limit
def test_login_audit_has_no_secrets(pv_world):
    w = pv_world
    rc = w.totp.enable()
    w.totp_secret = rc["secret"]
    c = cl(w.pv)
    bad_code = "123456"
    c.post("/api/login", json={"password": "hunter2-wrong-pw", "code": bad_code}, headers=csrf(c))
    login(cl(w.pv), code="654321")
    login(cl(w.pv), recovery_code="zzzzz-zzzzz")
    good = otp(w)
    assert login(cl(w.pv), code=good).status_code == 200
    rows = [r for r in w.audit.query(limit=1000) if "login" in json.dumps(r)]
    blob = json.dumps(rows) + (w.tmp / "audit.jsonl").read_text()
    for secret in ("hunter2-wrong-pw", PW, bad_code, "654321", good, "zzzzz-zzzzz", w.totp_secret):
        assert secret not in blob
    assert "bad_credentials" in blob and "bad_totp" in blob and "bad_recovery_code" in blob
    ok = [r for r in rows if "operator" in json.dumps(r) and '"ok"' in json.dumps(r)]
    assert ok, rows


def test_login_rate_limited_per_address(pv_world):
    w = pv_world
    c = cl(w.pv)
    codes = [login(c, code="000000").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_session_columns_migrate_on_an_old_db(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE session (session_id TEXT PRIMARY KEY, user TEXT NOT NULL, csrf TEXT NOT NULL,"
                      " created_at TEXT NOT NULL, expires_at TEXT NOT NULL);")
    con.close()
    st = AuthStore(db)
    sid = st.create_session("admin", idle_s=60, absolute_s=120)
    assert st.session_user(sid) == "admin"
    assert hashlib.sha256(sid.encode()).hexdigest() not in sid


@pytest.fixture
def pv_world(world):  # noqa: F811
    world.pv = app_for(world)
    return world
