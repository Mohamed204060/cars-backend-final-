"""
test_postgres_auth_sessions_integration.py — اختبارات تكامل حقيقية لجلسات
Auth على PostgreSQL حي (Migration 023، CR-013)
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا
بعد؛ لا اتصال شبكة أو محرك PostgreSQL متاح في بيئة إعداد هذه الحزمة.
لا يجوز اعتبار أي اختبار هنا "Passed" حتى يُنفَّذ فعليًا على اتصال حي.

بنفس اتفاقيات tests/test_postgres_integration.py تمامًا (conn fixture،
DATABASE_URL، ROLLBACK تلقائي)؛ ملف منفصل عمدًا لتفادي أي مساس بملف
الاختبارات المعتمَد سابقًا ضمن CR-012.

المتطلبات لتشغيل هذا الملف:
    pip install psycopg2-binary pytest
    export TEST_DATABASE_URL=postgresql://user:pass@host:5432/carparts_test
    (بعد تطبيق كل الترحيلات حتى 023 ضمنًا عبر scripts/setup_test_database.sh)
    pytest test_postgres_auth_sessions_integration.py -v
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import psycopg2
import psycopg2.extras

from session_repository import PostgresSessionRepository
from session_service import compute_expiry, generate_session_token, hash_token


DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/carparts_test")


@pytest.fixture
def conn():
    connection = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    yield connection
    connection.rollback()
    connection.close()


def _create_test_user(cur) -> str:
    cur.execute(
        "INSERT INTO iam.users (business_code, primary_role, account_type, status) "
        "VALUES (%s, 'individual_buyer', 'individual', 'active') RETURNING id",
        (f"USR-{uuid.uuid4().hex[:12]}",),
    )
    return cur.fetchone()["id"]


class TestSessionCreationAndLookup:

    def test_create_and_fetch_active_session_via_real_repository(self, conn):
        cur = conn.cursor()
        user_id = _create_test_user(cur)

        repo = PostgresSessionRepository(conn)
        raw_token = generate_session_token()
        token_hash = hash_token(raw_token)
        expires_at = compute_expiry(datetime.now(timezone.utc), 1800)

        created = repo.create_session(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        assert created.id is not None

        fetched = repo.get_active_session_by_token_hash(token_hash)
        assert fetched is not None
        assert fetched.user_id == user_id
        assert fetched.revoked_at is None

    def test_unknown_token_hash_returns_none(self, conn):
        repo = PostgresSessionRepository(conn)
        assert repo.get_active_session_by_token_hash("0" * 64) is None


class TestSessionUniqueConstraintOnLivePostgres:
    """يثبت أن uq_sessions_token_hash مُطبَّق فعليًا على محرك PostgreSQL."""

    def test_duplicate_token_hash_rejected_by_db(self, conn):
        cur = conn.cursor()
        user_id = _create_test_user(cur)
        token_hash = hash_token(generate_session_token())
        expires_at = compute_expiry(datetime.now(timezone.utc), 1800)

        cur.execute(
            "INSERT INTO iam.sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
            (user_id, token_hash, expires_at),
        )
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO iam.sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                (user_id, token_hash, expires_at),
            )


class TestSessionRevocation:
    """REQ-SEC-005: إبطال فوري، فعليًا على قاعدة بيانات حية."""

    def test_revoke_session_sets_revoked_at_and_reason(self, conn):
        cur = conn.cursor()
        user_id = _create_test_user(cur)
        repo = PostgresSessionRepository(conn)
        session = repo.create_session(
            user_id=user_id, token_hash=hash_token(generate_session_token()),
            expires_at=compute_expiry(datetime.now(timezone.utc), 1800),
        )

        repo.revoke_session(session.id, "logout")

        # بعد الإبطال، لم يعد يُعتبَر "نشطًا" — يعتمد على idx_sessions_active_lookup
        assert repo.get_active_session_by_token_hash(session.token_hash) is None

        cur.execute("SELECT revoked_at, revoked_reason FROM iam.sessions WHERE id = %s", (session.id,))
        row = cur.fetchone()
        assert row["revoked_at"] is not None
        assert row["revoked_reason"] == "logout"

    def test_revoke_all_sessions_for_user_on_ban(self, conn):
        """REQ-SEC-005: حظر الحساب يُلغي كل جلساته النشطة فورًا، لا واحدة فقط."""
        cur = conn.cursor()
        user_id = _create_test_user(cur)
        repo = PostgresSessionRepository(conn)

        s1 = repo.create_session(user_id, hash_token(generate_session_token()), compute_expiry(datetime.now(timezone.utc), 1800))
        s2 = repo.create_session(user_id, hash_token(generate_session_token()), compute_expiry(datetime.now(timezone.utc), 1800))

        revoked_count = repo.revoke_all_sessions_for_user(user_id, "admin_ban")
        assert revoked_count == 2

        assert repo.get_active_session_by_token_hash(s1.token_hash) is None
        assert repo.get_active_session_by_token_hash(s2.token_hash) is None

    def test_revoked_reason_check_constraint_enforced_by_db(self, conn):
        """يثبت أن chk_sessions_revoked_reason مُطبَّق فعليًا، لا في طبقة التطبيق فقط."""
        cur = conn.cursor()
        user_id = _create_test_user(cur)
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO iam.sessions (user_id, token_hash, expires_at, revoked_at, revoked_reason) "
                "VALUES (%s, %s, now() + interval '30 minutes', now(), 'not_a_real_reason')",
                (user_id, hash_token(generate_session_token())),
            )


class TestTouchSessionSlidingWindow:
    """REQ-SEC-004: تحديث انتهاء الصلاحية فعليًا عند كل استخدام."""

    def test_touch_extends_expiry(self, conn):
        cur = conn.cursor()
        user_id = _create_test_user(cur)
        repo = PostgresSessionRepository(conn)
        original_expiry = compute_expiry(datetime.now(timezone.utc), 60)  # دقيقة واحدة فقط
        session = repo.create_session(user_id, hash_token(generate_session_token()), original_expiry)

        new_expiry = compute_expiry(datetime.now(timezone.utc), 1800)
        repo.touch_session(session.id, new_expiry)

        fetched = repo.get_active_session_by_token_hash(session.token_hash)
        assert fetched.expires_at > original_expiry
