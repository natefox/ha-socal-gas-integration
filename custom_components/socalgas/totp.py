"""TOTP codes for SoCal Gas 2-step verification.

SoCalGas My Account can require a code from an authenticator app at login.
The integration stores the app's setup key (base32, or a whole otpauth://
URI) and computes the codes itself (RFC 6238: SHA-1, 6 digits, 30 s).

The login runs inside Browserless, which can't hash without a secure
context, so HA hands it a short table of upcoming codes keyed by time step
and the page picks the one that is current when it types.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct
import time
from urllib.parse import parse_qs, urlparse

STEP = 30
DIGITS = 6


class InvalidTotpSecret(ValueError):
    """The setup key isn't valid base32."""


def normalize_secret(secret: str) -> str:
    """Return the bare base32 key from a pasted key or otpauth:// URI."""
    secret = (secret or "").strip()
    if secret.lower().startswith("otpauth://"):
        secret = parse_qs(urlparse(secret).query).get("secret", [""])[0]
    secret = re.sub(r"[\s-]", "", secret).upper().rstrip("=")
    if not secret:
        raise InvalidTotpSecret("empty setup key")
    try:
        base64.b32decode(secret + "=" * (-len(secret) % 8))
    except (binascii.Error, ValueError) as err:
        raise InvalidTotpSecret("setup key is not base32") from err
    return secret


def code_at(secret: str, counter: int) -> str:
    """The code for one 30-second time step."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def upcoming_codes(secret: str, now: float | None = None,
                   before: int = 1, after: int = 12) -> dict[str, str]:
    """{time step: code} from `before` steps ago to `after` steps ahead.

    12 steps ahead covers the 6 minutes a login can take, so the page
    always finds the code that is current when it reaches the code prompt.
    """
    counter = int((time.time() if now is None else now) // STEP)
    return {str(c): code_at(secret, c)
            for c in range(counter - before, counter + after + 1)}
