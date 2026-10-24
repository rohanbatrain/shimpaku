"""Operator second factor: TOTP (RFC 6238 over RFC 4226 HOTP) and one-time recovery codes.

Stdlib only (hmac, hashlib, base64, struct). SHA-1, 6 digits, 30 s steps, a window of one step
either side. A code is accepted once: the last accepted time step is stored, and any code at or
before it is refused (replay of the same code within its window, or an older one).

State lives in ``<data>/totp.json`` (mode 0600, like ``admin.hash``): the base32 secret, the last
accepted step, and argon2id hashes of the unused recovery codes. The secret is never logged and
never returned by the API; ``repr`` of the store shows no secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import threading
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

STEP_S = 30
DIGITS = 6
WINDOW = 1
ISSUER = "Shimpaku"
ACCOUNT = "operator"
RECOVERY_CODES = 8

_ph = PasswordHasher()


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    """RFC 4226 section 5.3: HMAC, dynamic truncation, modulo 10^digits."""
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    off = mac[-1] & 0x0F
    bin_code = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    return str(bin_code % (10 ** digits)).zfill(digits)


def totp(key: bytes, t: float, digits: int = DIGITS, step: int = STEP_S, digest=hashlib.sha1) -> str:
    """RFC 6238: HOTP with counter floor(t / step) (T0 = 0)."""
    return hotp(key, int(t // step), digits, digest)


def b32_secret(key: bytes) -> str:
    return base64.b32encode(key).decode().rstrip("=")


def b32_key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def otpauth_uri(secret: str) -> str:
    from urllib.parse import quote

    return (f"otpauth://totp/{quote(ISSUER)}:{quote(ACCOUNT)}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_S}")


def _norm_recovery(code: str) -> str:
    return code.strip().lower().replace("-", "").replace(" ", "")


class TotpStore:
    """The operator's TOTP state file. Thread-safe within a process."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.clock = time.time  # tests replace it
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"TotpStore({str(self.path)!r})"

    # ---- file -------------------------------------------------------------
    def _read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    # ---- admin ------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        st = self._read()
        return bool(st and st.get("secret"))

    def enable(self) -> dict:
        """New secret and recovery codes (replacing any old ones). Returns them once."""
        secret = b32_secret(secrets.token_bytes(20))
        codes = [f"{secrets.token_hex(5)[:5]}-{secrets.token_hex(5)[:5]}" for _ in range(RECOVERY_CODES)]
        with self._lock:
            self._write({"secret": secret, "last_step": None,
                         "recovery": [_ph.hash(_norm_recovery(c)) for c in codes],
                         "enabled_at": int(time.time())})
        return {"secret": secret, "uri": otpauth_uri(secret), "recovery_codes": codes}

    def disable(self) -> bool:
        with self._lock:
            try:
                self.path.unlink()
                return True
            except FileNotFoundError:
                return False

    def status(self) -> dict:
        st = self._read()
        if not (st and st.get("secret")):
            return {"enabled": False}
        return {"enabled": True, "recovery_codes_left": len(st.get("recovery") or []),
                "enabled_at": st.get("enabled_at")}

    # ---- verification -----------------------------------------------------
    def verify_code(self, code: str) -> bool:
        """A current 6-digit code, at most one step off, never one already used."""
        code = (code or "").strip().replace(" ", "")
        if not (len(code) == DIGITS and code.isdigit()):
            return False
        with self._lock:
            st = self._read()
            if not (st and st.get("secret")):
                return False
            key = b32_key(st["secret"])
            now_step = int(self.clock() // STEP_S)
            last = st.get("last_step")
            hit = None
            for step in range(now_step - WINDOW, now_step + WINDOW + 1):
                if hmac.compare_digest(hotp(key, step), code):
                    hit = step
            if hit is None or (last is not None and hit <= last):
                return False
            st["last_step"] = hit
            self._write(st)
            return True

    def use_recovery(self, code: str) -> bool:
        """A recovery code, consumed on success (single use)."""
        norm = _norm_recovery(code or "")
        if not norm or len(norm) > 64:
            return False
        with self._lock:
            st = self._read()
            if not (st and st.get("secret")):
                return False
            left = list(st.get("recovery") or [])
            for i, h in enumerate(left):
                try:
                    if _ph.verify(h, norm):
                        del left[i]
                        st["recovery"] = left
                        self._write(st)
                        return True
                except (VerifyMismatchError, VerificationError, InvalidHashError):
                    continue
            return False


def totp_file(data_dir: Path) -> Path:
    return data_dir / "totp.json"


def terminal_qr(text: str) -> str | None:
    """A terminal QR code if a QR library is installed (qrcode or segno), else None."""
    try:
        import segno  # type: ignore

        import io
        buf = io.StringIO()
        segno.make(text, error="m").terminal(out=buf, compact=True)
        return buf.getvalue()
    except ImportError:
        pass
    try:
        import io

        import qrcode  # type: ignore

        q = qrcode.QRCode(border=1)
        q.add_data(text)
        buf = io.StringIO()
        q.print_ascii(out=buf, invert=True)
        return buf.getvalue()
    except ImportError:
        return None
