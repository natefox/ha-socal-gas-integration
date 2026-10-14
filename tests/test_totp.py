"""Tests for TOTP code generation (2-step verification)."""
import pytest

from custom_components.socalgas.totp import (
    InvalidTotpSecret,
    code_at,
    normalize_secret,
    upcoming_codes,
)

# RFC 6238 appendix B, SHA-1 key "12345678901234567890" (last 6 of 8 digits)
RFC_KEY = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize("t,code", [
    (59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037"),
])
def test_rfc6238_vectors(t, code):
    assert code_at(RFC_KEY, t // 30) == code


def test_normalize_accepts_spaces_lowercase_and_uri():
    assert normalize_secret("gezd gnbv-gy3t qojq gezd gnbv gy3t qojq") == RFC_KEY
    uri = "otpauth://totp/SoCalGas:me?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=SoCalGas"
    assert normalize_secret(uri) == RFC_KEY


@pytest.mark.parametrize("bad", ["", "   ", "not base32!", "otpauth://totp/x?issuer=y"])
def test_normalize_rejects_junk(bad):
    with pytest.raises(InvalidTotpSecret):
        normalize_secret(bad)


def test_upcoming_codes_cover_the_login_window():
    codes = upcoming_codes(RFC_KEY, now=59)
    assert codes["1"] == "287082"
    assert sorted(int(k) for k in codes) == list(range(0, 14))
