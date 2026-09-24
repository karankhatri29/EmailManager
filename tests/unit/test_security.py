from unittest.mock import patch

import pytest

from app.core import security
from app.core.security import LoginRateLimiter, decrypt, encrypt, hash_password, verify_password


def test_password_hash_verifies_and_is_salted():
    h1, h2 = hash_password("secret-pass"), hash_password("secret-pass")
    assert h1 != h2 and "secret-pass" not in h1
    assert verify_password("secret-pass", h1) is True
    assert verify_password("wrong", h1) is False


def test_verify_password_for_unknown_user_is_false():
    assert verify_password("anything", None) is False
    assert verify_password("not-a-real-password", None) is False  # even the dummy's own password


def test_verify_password_rejects_garbage_hash():
    assert verify_password("x", "not-a-hash") is False


def test_encrypt_roundtrip_and_ciphertext_differs():
    token = encrypt('{"refresh_token": "abc"}')
    assert "refresh_token" not in token
    assert decrypt(token) == '{"refresh_token": "abc"}'
    assert encrypt("same") != encrypt("same")  # random IV


def test_encrypt_requires_a_key():
    with patch.object(security, "get_settings") as settings:
        settings.return_value.encryption_key = ""
        with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
            encrypt("x")


def test_calendar_tokens_are_unique_and_long():
    tokens = {security.new_calendar_token() for _ in range(50)}
    assert len(tokens) == 50 and all(len(t) >= 30 for t in tokens)


def test_rate_limiter_blocks_after_max_failures_and_resets():
    limiter = LoginRateLimiter(max_failures=3, window_seconds=60)
    for _ in range(2):
        limiter.record_failure("a@x.com")
    assert limiter.is_blocked("a@x.com") is False
    limiter.record_failure("a@x.com")
    assert limiter.is_blocked("a@x.com") is True
    assert limiter.is_blocked("other@x.com") is False  # per key

    limiter.reset("a@x.com")
    assert limiter.is_blocked("a@x.com") is False


def test_rate_limiter_forgets_old_failures():
    limiter = LoginRateLimiter(max_failures=1, window_seconds=0.05)
    limiter.record_failure("a")
    assert limiter.is_blocked("a") is True
    import time

    time.sleep(0.1)
    assert limiter.is_blocked("a") is False
