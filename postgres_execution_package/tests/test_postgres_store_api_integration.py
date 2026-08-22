"""
test_postgres_store_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لخدمة المتاجر على PostgreSQL حي
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا بعد.
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
from store_api import router as store_router
from store_repository import PostgresStoreRepository
from credential_service import hash_password
from aud_repository import PostgresAudRepository


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
    app.include_router(store_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.aud_repository = PostgresAudRepository(conn)
    app.state.store_repository = PostgresStoreRepository(conn)
    client = TestClient(app, base_url="https://testserver")
    return app, client, conn


def _register_and_login(client, conn, email: str, role: str = "individual_seller") -> str:
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
    return user_id


class TestStoreLifecycleOnLivePostgres:

    def test_create_store_then_moderator_suspends(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"mod{uuid.uuid4().hex[:6]}@example.com", role="moderator")

        resp = client.post(f"/api/v1/store/stores/{store_id}/status", json={"new_status": "suspended"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "suspended"

    def test_owner_cannot_change_own_status_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        resp = client.post(f"/api/v1/store/stores/{store_id}/status", json={"new_status": "suspended"})
        assert resp.status_code == 403

    def test_admin_transfers_ownership(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        client.post("/api/v1/auth/logout")
        new_owner_id = _register_and_login(client, conn, f"neww{uuid.uuid4().hex[:6]}@example.com")
        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")

        resp = client.post(f"/api/v1/store/stores/{store_id}/transfer-ownership",
                            json={"new_owner_user_ref_id": new_owner_id})
        assert resp.status_code == 200
        assert resp.json()["owner_user_ref_id"] == new_owner_id


class TestGetMyStoreOnLivePostgres:
    """Unit 4+5 — فجوة حقيقية مكتشَفة: GET /stores/mine على Postgres حي (idx_stores_owner)."""

    def test_returns_own_store(self, app_and_client):
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"mystore{uuid.uuid4().hex[:6]}@example.com")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        resp = client.get("/api/v1/store/stores/mine")
        assert resp.status_code == 200
        assert resp.json()["id"] == store_id
        assert resp.json()["owner_user_ref_id"] == user_id

    def test_404_when_no_store_owned(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"mystore{uuid.uuid4().hex[:6]}@example.com")
        resp = client.get("/api/v1/store/stores/mine")
        assert resp.status_code == 404
