"""
test_session_service.py — اختبارات وحدة خالصة لمنطق الجلسات (لا اعتماديات خارجية)
المرجع: CR-013؛ REQ-SEC-004، REQ-SEC-005
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from session_service import (
    Session,
    SessionExpiredError,
    SessionInvalidError,
    SessionRevokedError,
    build_revocation,
    compute_expiry,
    ensure_session_valid,
    generate_session_token,
    hash_token,
    is_expired,
    is_revoked,
)


def make_session(**overrides) -> Session:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="s1", user_id="u1", token_hash="deadbeef",
        created_at=now, last_active_at=now, expires_at=now + timedelta(minutes=30),
        revoked_at=None, revoked_reason=None,
    )
    defaults.update(overrides)
    return Session(**defaults)


class TestTokenGenerationAndHashing:

    def test_generated_tokens_are_unique_and_high_entropy(self):
        tokens = {generate_session_token() for _ in range(1000)}
        assert len(tokens) == 1000  # لا تصادم عبر 1000 توليد

    def test_hash_is_deterministic_sha256_hex(self):
        token = "sample-token-value"
        expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert hash_token(token) == expected
        assert len(hash_token(token)) == 64  # يطابق VARCHAR(64) في Migration 023

    def test_raw_token_never_equals_its_hash(self):
        token = generate_session_token()
        assert hash_token(token) != token


class TestExpiryComputation:

    def test_compute_expiry_adds_idle_timeout(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_expiry(now, idle_timeout_seconds=1800)
        assert result == now + timedelta(seconds=1800)

    def test_is_expired_true_after_expiry(self):
        session = make_session(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        assert is_expired(session, datetime.now(timezone.utc)) is True

    def test_is_expired_false_before_expiry(self):
        session = make_session(expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
        assert is_expired(session, datetime.now(timezone.utc)) is False


class TestRevocation:

    def test_is_revoked_false_by_default(self):
        assert is_revoked(make_session()) is False

    def test_is_revoked_true_after_revocation(self):
        session = make_session(revoked_at=datetime.now(timezone.utc), revoked_reason="logout")
        assert is_revoked(session) is True

    def test_build_revocation_accepts_known_reasons(self):
        for reason in ["logout", "idle_timeout", "admin_ban", "admin_revoke"]:
            assert build_revocation(reason) == reason

    def test_build_revocation_rejects_unknown_reason(self):
        with pytest.raises(ValueError):
            build_revocation("something_made_up")


class TestEnsureSessionValid:
    """REQ-SEC-004/005: البوابة الوحيدة التي يستدعيها auth_api.py قبل أي إجراء محمي."""

    def test_none_session_raises_invalid(self):
        with pytest.raises(SessionInvalidError):
            ensure_session_valid(None, datetime.now(timezone.utc))

    def test_revoked_session_raises_revoked_even_if_not_expired(self):
        session = make_session(
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            revoked_at=datetime.now(timezone.utc), revoked_reason="admin_ban",
        )
        with pytest.raises(SessionRevokedError):
            ensure_session_valid(session, datetime.now(timezone.utc))

    def test_expired_session_raises_expired(self):
        session = make_session(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        with pytest.raises(SessionExpiredError):
            ensure_session_valid(session, datetime.now(timezone.utc))

    def test_valid_session_returns_itself(self):
        session = make_session(expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
        result = ensure_session_valid(session, datetime.now(timezone.utc))
        assert result is session

    def test_revocation_checked_before_expiry_when_both_true(self):
        """أولوية سبب الرفض: Revoked قبل Expired عند تحقق الحالتين معًا (REQ-SEC-005
        أولوية أمنية أعلى: حظر حساب يجب أن يُبلَّغ كـ'مُبطَلة' لا 'منتهية' لتمييز التدقيق)."""
        session = make_session(
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            revoked_at=datetime.now(timezone.utc), revoked_reason="admin_ban",
        )
        with pytest.raises(SessionRevokedError):
            ensure_session_valid(session, datetime.now(timezone.utc))
