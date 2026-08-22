"""
test_postgres_ana_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لـAnalytics Event Foundation على PostgreSQL حي
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا بعد.
يتحقق من نحو SQL الفعلي لـ033_ana_events.sql (WHERE/index usage) — لا يكتشفه
py_compile ولا InMemory.
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
from ana_api import router as ana_router
from ana_repository import PostgresAnaRepository
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
    app.include_router(ana_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.aud_repository = PostgresAudRepository(conn)
    app.state.ana_repository = PostgresAnaRepository(conn)
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


class TestAnalyticsEventsOnLivePostgres:

    def test_anonymous_event_roundtrip(self, app_and_client):
        app, client, conn = app_and_client
        resp = client.post("/api/v1/analytics/events", json={"event_type": "search_zero_results",
                                                               "metadata": {"query": "مصباح خلفي كامري"}})
        assert resp.status_code == 201, resp.text
        assert resp.json()["actor_ref_id"] is None

    def test_authenticated_event_sets_actor_and_visible_to_admin(self, app_and_client):
        """Corrective Pass: actor_ref_id أصبح فلترًا حقيقيًا في list_events (WHERE actor_ref_id = ...)
        — لم يعد Query Param متجاهَلًا بصمت من FastAPI؛ الاختبار السلبي أدناه يثبت ذلك."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com")
        resp = client.post("/api/v1/analytics/events", json={
            "event_type": "purchase_request_created", "context_type": "purchase_request", "context_ref_id": str(uuid.uuid4()),
        })
        assert resp.status_code == 201, resp.text
        assert resp.json()["actor_ref_id"] == user_id
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        listing = client.get("/api/v1/analytics/events", params={"actor_ref_id": user_id})
        assert listing.status_code == 200, listing.text
        assert listing.json()["pagination"]["total_items"] >= 1
        assert all(i["actor_ref_id"] == user_id for i in listing.json()["items"])

        other_actor_listing = client.get("/api/v1/analytics/events", params={"actor_ref_id": str(uuid.uuid4())})
        assert other_actor_listing.status_code == 200
        assert other_actor_listing.json()["pagination"]["total_items"] == 0

    def test_invalid_context_ref_id_rejected_on_live_postgres(self, app_and_client):
        """Corrective Pass: قيمة غير صالحة لعمود UUID يجب أن تُرفَض قبل الوصول لـPostgreSQL (400 لا 500)."""
        app, client, conn = app_and_client
        resp = client.post("/api/v1/analytics/events", json={"event_type": "offer_submitted", "context_ref_id": "not-a-uuid"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_REF_ID"

    def test_filter_by_event_type_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        marker = str(uuid.uuid4())
        client.post("/api/v1/analytics/events", json={"event_type": "offer_submitted", "context_ref_id": marker})
        client.post("/api/v1/analytics/events", json={"event_type": "offer_accepted", "context_ref_id": marker})

        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"event_type": "offer_accepted", "context_ref_id": marker})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pagination"]["total_items"] == 1
        assert body["items"][0]["event_type"] == "offer_accepted"

    def test_unknown_event_type_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        resp = client.post("/api/v1/analytics/events", json={"event_type": "definitely_not_in_the_catalog"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_EVENT_TYPE"

    def test_forbidden_for_non_admin_read_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        resp = client.get("/api/v1/analytics/events")
        assert resp.status_code == 403

    def test_pagination_bounds_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        assert client.get("/api/v1/analytics/events", params={"page": 0}).status_code == 422
        assert client.get("/api/v1/analytics/events", params={"page_size": 1000}).status_code == 422
