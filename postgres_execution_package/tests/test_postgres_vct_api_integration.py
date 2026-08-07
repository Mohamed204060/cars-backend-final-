"""
test_postgres_vct_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لخدمة VCT على PostgreSQL حي (VCT Contract Extension)
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
from vct_api import router as vct_router
from vct_repository import PostgresVctRepository
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
    app.include_router(vct_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)
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


class TestManufacturerLifecycleOnLivePostgres:

    def test_propose_and_approve_manufacturer(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_regular_buyer_cannot_approve_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        assert resp.status_code == 403


class TestFullHierarchyOnLivePostgres:

    def test_full_manufacturer_to_trim_chain(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"chain{uuid.uuid4().hex[:6]}@example.com", role="super_admin")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
        client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
        gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]

        trim_resp = client.post(
            f"/api/v1/vct/generations/{gen_id}/trims",
            json={"fuel_type_ref_id": str(uuid.uuid4()), "transmission_type_ref_id": str(uuid.uuid4())},
        )
        assert trim_resp.status_code == 201

        get_resp = client.get(f"/api/v1/vct/trims/{trim_resp.json()['id']}")
        assert get_resp.status_code == 200

    def test_model_under_unapproved_manufacturer_returns_409_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"unapproved{uuid.uuid4().hex[:6]}@example.com")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]  # لم يُعتمَد
        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/models")
        assert resp.status_code == 409
