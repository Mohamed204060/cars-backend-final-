"""
test_postgres_aud_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لخدمة AUD (سجل التدقيق) على PostgreSQL حي
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا بعد.
aud.events نفسه من 004_aud.sql (مغلَق، بلا تعديل هنا) — هذا أول اختبار حي
لطبقة Repository/Service/API الجديدة فوقه (Batch 3A Slice 1).
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
from aud_api import router as aud_router
from aud_repository import PostgresAudRepository
from aud_service import record_audit_event_via_repository
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
    app.include_router(aud_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.aud_repository = PostgresAudRepository(conn)
    client = TestClient(app, base_url="https://testserver")
    return app, client, conn


def _register_and_login(client, conn, email: str, role: str = "individual_buyer") -> str:
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


class TestAuditEventsOnLivePostgres:

    def test_insert_and_list_roundtrip(self, app_and_client):
        app, client, conn = app_and_client
        actor_id = _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        record_audit_event_via_repository(
            app.state.aud_repository, log_type="security", event_name="identity_added",
            actor_ref_id=actor_id, metadata={"provider_code": "email_password"},
        )
        resp = client.get("/api/v1/audit/events", params={"actor_ref_id": actor_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pagination"]["total_items"] >= 1
        assert body["items"][0]["event_name"] == "identity_added"

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 403

    def test_filter_by_log_type_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        actor_id = _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        record_audit_event_via_repository(
            app.state.aud_repository, log_type="administrative", event_name="store_suspended",
            actor_ref_id=actor_id, reason="اختبار تكامل حي",
        )
        resp = client.get("/api/v1/audit/events", params={"log_type": "administrative", "actor_ref_id": actor_id})
        assert resp.status_code == 200
        matching = [i for i in resp.json()["items"] if i["event_name"] == "store_suspended"]
        assert len(matching) == 1
        assert matching[0]["reason"] == "اختبار تكامل حي"

    def test_invalid_actor_ref_id_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"actor_ref_id": "not-a-uuid"})
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "INVALID_REF_ID"

    def test_pagination_bounds_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        assert client.get("/api/v1/audit/events", params={"page": 0}).status_code == 422
        assert client.get("/api/v1/audit/events", params={"page_size": 1000}).status_code == 422
