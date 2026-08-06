"""
test_postgres_auth_credentials_integration.py — اختبارات تكامل حقيقية لتخزين
والتحقق من بيانات اعتماد كلمة المرور على PostgreSQL حي (تعديل CR-013 v2)
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا
بعد؛ لا اتصال شبكة أو محرك PostgreSQL متاح في بيئة إعداد هذه الحزمة.

المتطلبات لتشغيل هذا الملف:
    pip install psycopg2-binary pytest
    export TEST_DATABASE_URL=postgresql://user:pass@host:5432/carparts_test
    pytest test_postgres_auth_credentials_integration.py -v
"""

import os
import uuid

import pytest
import psycopg2
import psycopg2.extras

from auth_repository import PostgresAuthRepository
from auth_service import UserIdentity


DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/carparts_test")


@pytest.fixture
def conn():
    connection = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    yield connection
    connection.rollback()
    connection.close()


def _create_active_user(cur, status: str = "active") -> str:
    cur.execute(
        "INSERT INTO iam.users (business_code, primary_role, account_type, status) "
        "VALUES (%s, 'individual_buyer', 'individual', %s) RETURNING id",
        (f"USR-{uuid.uuid4().hex[:12]}", status),
    )
    return cur.fetchone()["id"]


class TestPasswordCredentialStorage:

    def test_insert_identity_with_password_stores_hash_not_raw(self, conn):
        cur = conn.cursor()
        user_id = _create_active_user(cur)
        repo = PostgresAuthRepository(conn)

        email = f"user{uuid.uuid4().hex[:8]}@example.com"
        identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                                 external_identifier=email, is_verified=True, is_primary=True)
        saved = repo.insert_identity(identity, raw_password="CorrectHorseBattery1!")
        assert saved.id is not None

        cur.execute("SELECT credential_secret_hash FROM iam.user_identities WHERE id = %s", (saved.id,))
        stored = cur.fetchone()["credential_secret_hash"]
        assert stored is not None
        assert "CorrectHorseBattery1!" not in stored
        assert stored.startswith("pbkdf2_sha256$")


class TestPasswordVerificationOnLivePostgres:

    def test_correct_password_verifies_via_real_repository(self, conn):
        cur = conn.cursor()
        user_id = _create_active_user(cur)
        repo = PostgresAuthRepository(conn)
        email = f"user{uuid.uuid4().hex[:8]}@example.com"
        identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                                 external_identifier=email, is_verified=True, is_primary=True)
        repo.insert_identity(identity, raw_password="CorrectHorseBattery1!")

        result = repo.find_identity_and_verify_password("email_password", email, "CorrectHorseBattery1!")
        assert result is not None
        assert result.user_id == user_id

    def test_wrong_password_rejected_via_real_repository(self, conn):
        cur = conn.cursor()
        user_id = _create_active_user(cur)
        repo = PostgresAuthRepository(conn)
        email = f"user{uuid.uuid4().hex[:8]}@example.com"
        identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                                 external_identifier=email, is_verified=True, is_primary=True)
        repo.insert_identity(identity, raw_password="CorrectHorseBattery1!")

        assert repo.find_identity_and_verify_password("email_password", email, "WrongPassword") is None

    def test_suspended_account_rejected_even_with_correct_password(self, conn):
        cur = conn.cursor()
        user_id = _create_active_user(cur, status="suspended")
        repo = PostgresAuthRepository(conn)
        email = f"user{uuid.uuid4().hex[:8]}@example.com"
        identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                                 external_identifier=email, is_verified=True, is_primary=True)
        repo.insert_identity(identity, raw_password="CorrectHorseBattery1!")

        assert repo.find_identity_and_verify_password("email_password", email, "CorrectHorseBattery1!") is None

    def test_nonexistent_identifier_and_wrong_password_are_indistinguishable(self, conn):
        """يثبت على قاعدة بيانات حية أن القيمة المُعادة (None) واحدة سواء
        كان السبب 'الحساب غير موجود' أو 'كلمة مرور خاطئة' — لا كشف ممكن."""
        cur = conn.cursor()
        user_id = _create_active_user(cur)
        repo = PostgresAuthRepository(conn)
        email = f"user{uuid.uuid4().hex[:8]}@example.com"
        identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                                 external_identifier=email, is_verified=True, is_primary=True)
        repo.insert_identity(identity, raw_password="CorrectHorseBattery1!")

        unknown = repo.find_identity_and_verify_password("email_password", "ghost-never-registered@example.com", "anything")
        wrong = repo.find_identity_and_verify_password("email_password", email, "WrongPassword")
        assert unknown is None
        assert wrong is None

    def test_oauth_identity_without_password_never_verifies(self, conn):
        cur = conn.cursor()
        user_id = _create_active_user(cur)
        repo = PostgresAuthRepository(conn)
        sub = f"google-sub-{uuid.uuid4().hex}"
        identity = UserIdentity(id="", user_id=user_id, provider_code="google",
                                 external_identifier=sub, is_verified=True, is_primary=True)
        repo.insert_identity(identity)  # بلا raw_password

        assert repo.find_identity_and_verify_password("google", sub, "any-guess") is None
