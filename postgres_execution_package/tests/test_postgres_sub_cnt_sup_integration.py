"""
test_postgres_sub_cnt_sup_integration.py — اختبارات تكامل حقيقية لخدمات
الاشتراكات (SUB) وإدارة المحتوى (CNT) والدعم الفني (SUP) على PostgreSQL حي.
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
from sub_api import router as sub_router
from sub_repository import PostgresSubRepository
from cnt_api import router as cnt_router
from cnt_repository import PostgresCntRepository
from sup_api import router as sup_router
from sup_repository import PostgresSupRepository
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
    app.include_router(sub_router)
    app.include_router(cnt_router)
    app.include_router(sup_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.sub_repository = PostgresSubRepository(conn)
    app.state.cnt_repository = PostgresCntRepository(conn)
    app.state.sup_repository = PostgresSupRepository(conn)
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


class TestSubscriptionsOnLivePostgres:

    def test_full_subscribe_and_change_plan_flow(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        plan_id = client.post("/api/v1/subscriptions/plans", json={"plan_type_ref_id": str(uuid.uuid4())}).json()["id"]

        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"seller-{uuid.uuid4().hex[:8]}@example.com")
        sub_resp = client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 30})
        assert sub_resp.status_code == 201

        cur = conn.cursor()
        cur.execute("SELECT status FROM sub.seller_subscriptions WHERE id = %s", (sub_resp.json()["id"],))
        assert cur.fetchone()["status"] == "active"

    def test_regular_user_cannot_create_plan_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer-{uuid.uuid4().hex[:8]}@example.com")
        resp = client.post("/api/v1/subscriptions/plans", json={"plan_type_ref_id": "x"})
        assert resp.status_code == 403


class TestContentOnLivePostgres:

    def test_editor_creates_and_publishes_article(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"editor-{uuid.uuid4().hex[:8]}@example.com", role="news_editor")

        article_id = client.post("/api/v1/content/articles", json={"title": "خبر", "body": "تفاصيل"}).json()["id"]
        publish_resp = client.post(f"/api/v1/content/articles/{article_id}/publish")
        assert publish_resp.status_code == 200

        cur = conn.cursor()
        cur.execute("SELECT status FROM cnt.articles WHERE id = %s", (article_id,))
        assert cur.fetchone()["status"] == "published"


class TestSupportOnLivePostgres:

    def test_full_ticket_lifecycle(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"requester-{uuid.uuid4().hex[:8]}@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة تقنية"}).json()["id"]

        client.post("/api/v1/auth/logout")
        mod_id = _register_and_login(client, conn, f"mod-{uuid.uuid4().hex[:8]}@example.com", role="support_moderator")
        assign_resp = client.post(f"/api/v1/support/tickets/{ticket_id}/assign", json={"moderator_ref_id": mod_id})
        assert assign_resp.status_code == 200
        assert assign_resp.json()["status"] == "in_progress"

        reply_resp = client.post(f"/api/v1/support/tickets/{ticket_id}/replies", json={"body": "جاري الفحص"})
        assert reply_resp.status_code == 201

        resolve_resp = client.post(f"/api/v1/support/tickets/{ticket_id}/resolve")
        assert resolve_resp.status_code == 200

        close_resp = client.post(f"/api/v1/support/tickets/{ticket_id}/close")
        assert close_resp.status_code == 200

        cur = conn.cursor()
        cur.execute("SELECT status, reopen_window_expires_at FROM sup.tickets WHERE id = %s", (ticket_id,))
        row = cur.fetchone()
        assert row["status"] == "closed"
        assert row["reopen_window_expires_at"] is not None

        cur.execute("SELECT count(*) AS c FROM sup.replies WHERE ticket_id = %s", (ticket_id,))
        assert cur.fetchone()["c"] == 1
