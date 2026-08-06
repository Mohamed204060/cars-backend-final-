"""
test_postgres_cmp_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لخدمة CMP على PostgreSQL حي (CMP Contract Extension)
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا
بعد؛ لا اتصال شبكة أو محرك PostgreSQL متاح في بيئة إعداد هذه الحزمة.
"""

import os
import uuid

import pytest
import psycopg2
import psycopg2.extras
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import PostgresAuthRepository
from session_repository import PostgresSessionRepository
from pct_api import router as pct_router
from pct_repository import PostgresPctRepository
from vct_api import router as vct_router
from vct_repository import PostgresVctRepository
from cmp_api import router as cmp_router
from cmp_repository import PostgresCmpRepository
from credential_service import hash_password


DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/carparts_test")


@pytest.fixture
def conn():
    connection = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    yield connection
    connection.rollback()
    connection.close()


@pytest.fixture
def app_and_client(conn):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(pct_router)
    app.include_router(vct_router)
    app.include_router(cmp_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.pct_repository = PostgresPctRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)
    app.state.cmp_repository = PostgresCmpRepository(conn)
    client = TestClient(app, base_url="https://testserver")
    return app, client, conn


def _register_and_login(client, conn, email: str, role: str = "individual_buyer") -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO iam.users (business_code, primary_role, account_type, status) "
        "VALUES (%s, %s, 'individual', 'active') RETURNING id",
        (f"USR-{uuid.uuid4().hex[:12]}", role),
    )
    user_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO iam.user_identities (user_id, provider_type_id, external_identifier, credential_secret_hash, verified_at, is_primary) "
        "SELECT %s, ip.id, %s, %s, now(), true FROM iam.identity_providers ip WHERE ip.code = 'email_password'",
        (user_id, email, hash_password("Str0ngPass1!")),
    )
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    assert resp.status_code == 200, resp.text


def _make_approved_part(client, conn) -> str:
    cur = conn.cursor()
    cur.execute("INSERT INTO pct.categories DEFAULT VALUES RETURNING id")
    category_id = cur.fetchone()["id"]
    part_id = client.post("/api/v1/pct/parts", json={"category_id": category_id}).json()["id"]
    client.post(f"/api/v1/pct/parts/{part_id}/approve")
    return part_id


def _make_valid_trim(client) -> str:
    m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
    client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
    model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
    gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]
    trim_resp = client.post(f"/api/v1/vct/generations/{gen_id}/trims",
                             json={"fuel_type_ref_id": str(uuid.uuid4()), "transmission_type_ref_id": str(uuid.uuid4())})
    return trim_resp.json()["id"]


class TestCreateRecordOnLivePostgres:

    def test_admin_creates_record_for_approved_part_and_valid_trim(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)

        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    def test_regular_buyer_forbidden_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        # 1-3: تجهيز البيانات بصلاحية admin (اعتماد الشركة المصنّعة واعتماد
        # القطعة كلاهما يتطلب admin/super_admin؛ لا يمكن تجهيزهما بحساب buyer)
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)

        # 4: إنهاء جلسة admin صراحة قبل التبديل
        client.post("/api/v1/auth/logout")

        # 5: تسجيل الدخول بحساب عادي منفصل
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")

        # 6-7: محاولة إنشاء سجل CMP بحساب buyer، بالبيانات الجاهزة مسبقًا فقط
        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 403


class TestDuplicateConstraintOnLivePostgres:
    """يثبت uq_compatibility_part_trim الفعلي، لا محاكاة في الذاكرة."""

    def test_duplicate_pair_rejected_by_db(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)

        first = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert first.status_code == 201
        second = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert second.status_code == 409


class TestArchiveOnLivePostgres:

    def test_archive_then_get_shows_archived_status(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client)
        record_id = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]

        archive_resp = client.post(f"/api/v1/cmp/records/{record_id}/archive")
        assert archive_resp.status_code == 200

        get_resp = client.get(f"/api/v1/cmp/records/{record_id}")
        assert get_resp.json()["status"] == "archived"
