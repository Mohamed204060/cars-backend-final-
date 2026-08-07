"""
test_postgres_pct_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لخدمة PCT على PostgreSQL حي (PCT Contract Extension)
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا
بعد؛ لا اتصال شبكة أو محرك PostgreSQL متاح في بيئة إعداد هذه الحزمة.

المتطلبات: pip install -r requirements.txt
    export TEST_DATABASE_URL=postgresql://user:pass@host:5432/carparts_test
    pytest test_postgres_pct_api_integration.py -v
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
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.pct_repository = PostgresPctRepository(conn)
    client = TestClient(app, base_url="https://testserver")
    return app, client, conn


def _create_category(conn) -> str:
    cur = conn.cursor()
    cur.execute("INSERT INTO pct.categories DEFAULT VALUES RETURNING id")
    return cur.fetchone()["id"]


def _register_and_login(client, conn, email: str, password: str, role: str = "individual_buyer") -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO iam.users (business_code, primary_role, account_type, status) "
        "VALUES (%s, %s, 'individual', 'active') RETURNING id",
        (f"USR-{uuid.uuid4().hex[:12]}", role),
    )
    user_id = cur.fetchone()["id"]
    cur.execute(
        "SELECT ip.id FROM iam.identity_providers ip WHERE ip.code = 'email_password'"
    )
    from credential_service import hash_password
    cur.execute(
        "INSERT INTO iam.user_identities (user_id, provider_type_id, external_identifier, credential_secret_hash, verified_at, is_primary) "
        "SELECT %s, ip.id, %s, %s, now(), true FROM iam.identity_providers ip WHERE ip.code = 'email_password'",
        (user_id, email, hash_password(password)),
    )
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": password})
    assert resp.status_code == 200, resp.text


class TestProposeAndGetPartOnLivePostgres:

    def test_propose_part_with_real_category_fk(self, app_and_client):
        app, client, conn = app_and_client
        category_id = _create_category(conn)
        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", "Str0ngPass1!")

        resp = client.post("/api/v1/pct/parts", json={"category_id": category_id})
        assert resp.status_code == 201
        part_id = resp.json()["id"]

        get_resp = client.get(f"/api/v1/pct/parts/{part_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "proposed"

    def test_propose_part_with_nonexistent_category_returns_404(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", "Str0ngPass1!")

        resp = client.post("/api/v1/pct/parts", json={"category_id": str(uuid.uuid4())})
        assert resp.status_code == 404


class TestApprovalAuthorizationOnLivePostgres:
    """REQ-PCT-002 على قاعدة بيانات حية: يتحقق من role الفعلي في iam.users."""

    def test_regular_buyer_cannot_approve(self, app_and_client):
        app, client, conn = app_and_client
        category_id = _create_category(conn)
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", "Str0ngPass1!", role="individual_buyer")

        part_id = client.post("/api/v1/pct/parts", json={"category_id": category_id}).json()["id"]
        resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
        assert resp.status_code == 403

    def test_admin_can_approve(self, app_and_client):
        app, client, conn = app_and_client
        category_id = _create_category(conn)
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", "Str0ngPass1!", role="admin")

        part_id = client.post("/api/v1/pct/parts", json={"category_id": category_id}).json()["id"]
        resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"


class TestOemNumberUniquenessOnLivePostgres:
    """يثبت uq_oem_numbers_manufacturer_number الفعلي، لا محاكاة في الذاكرة."""

    def test_duplicate_oem_number_rejected_by_db(self, app_and_client):
        app, client, conn = app_and_client
        category_id = _create_category(conn)
        _register_and_login(client, conn, f"oemer{uuid.uuid4().hex[:6]}@example.com", "Str0ngPass1!")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": category_id}).json()["id"]

        manufacturer_ref_id = str(uuid.uuid4())
        first = client.post(f"/api/v1/pct/parts/{part_id}/oem-numbers",
                             json={"manufacturer_ref_id": manufacturer_ref_id, "oem_number": "OEM-LIVE-1"})
        assert first.status_code == 201

        second = client.post(f"/api/v1/pct/parts/{part_id}/oem-numbers",
                              json={"manufacturer_ref_id": manufacturer_ref_id, "oem_number": "OEM-LIVE-1"})
        assert second.status_code == 409
