"""
test_postgres_orders_messaging_notifications_integration.py — اختبارات
تكامل حقيقية عبر الدفعة الثالثة (Orders + Messaging + Notifications) على
PostgreSQL حي، شاملة سيناريو العمل الفعلي الكامل بين الخدمات الثلاث:
طلب شراء → عرض سعر → رسالة تواصل بشأنه → إشعار للمشتري.
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
from pct_api import router as pct_router
from pct_repository import PostgresPctRepository
from order_api import router as order_router
from order_repository import PostgresOrderRepository
from inventory_item_api import router as inventory_router
from inventory_item_repository import PostgresInventoryItemRepository
from message_api import router as message_router
from message_repository import PostgresMessageRepository
from message_extended_api import router as message_extended_router
from message_extended_repository import PostgresMessageExtendedRepository
from ntf_api import router as ntf_router
from ntf_repository import PostgresNtfRepository
from ntf_service import NotificationCenterEntry
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
    app.include_router(store_router)
    app.include_router(pct_router)
    app.include_router(order_router)
    app.include_router(message_router)
    app.include_router(message_extended_router)
    app.include_router(ntf_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.store_repository = PostgresStoreRepository(conn)
    app.state.pct_repository = PostgresPctRepository(conn)
    app.state.order_repository = PostgresOrderRepository(conn)
    app.state.inventory_repository = PostgresInventoryItemRepository(conn)
    app.state.message_repository = PostgresMessageRepository(conn)
    app.state.message_extended_repository = PostgresMessageExtendedRepository(conn)
    app.state.ntf_repository = PostgresNtfRepository(conn)
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


def _make_approved_part(client, conn) -> str:
    cur = conn.cursor()
    cur.execute("INSERT INTO pct.categories DEFAULT VALUES RETURNING id")
    category_id = cur.fetchone()["id"]
    _register_and_login(client, conn, f"admin-setup-{uuid.uuid4().hex[:8]}@example.com", role="admin")
    part_id = client.post("/api/v1/pct/parts", json={"category_id": category_id}).json()["id"]
    approve_resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
    assert approve_resp.status_code == 200, approve_resp.text
    client.post("/api/v1/auth/logout")
    return part_id


def _make_recipient(conn, user_ref_id: str) -> str:
    """يبني سلسلة الاعتماديات الحقيقية الكاملة لسجل Recipient صالح
    (campaign → delivery → recipient) عبر SQL مباشر، تفاديًا لاستخدام
    recipient_id عشوائي لا يقابله صف فعلي في ntf.recipients (FK حقيقي)."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type) "
        "VALUES (%s, %s, %s, 'static') RETURNING id",
        (user_ref_id, "إشعار اختباري", "نص الإشعار"),
    )
    campaign_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO ntf.deliveries (campaign_id, campaign_version_snapshot, correlation_id) "
        "VALUES (%s, 1, %s) RETURNING id",
        (campaign_id, str(uuid.uuid4())),
    )
    delivery_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO ntf.recipients (delivery_id, user_ref_id, channel_provider_code) "
        "VALUES (%s, %s, 'email') RETURNING id",
        (delivery_id, user_ref_id),
    )
    return cur.fetchone()["id"]


class TestFullOrderToMessageToNotificationScenario:
    """السيناريو الكامل الذي طلبه مالك المشروع صراحةً: تكامل حقيقي بين
    الخدمات الثلاث، لا اختبار كل خدمة بمعزل عن الأخرى."""

    def test_full_scenario(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)

        # 1) المشتري ينشئ طلب شراء
        buyer_email, buyer_password = f"buyer-{uuid.uuid4().hex[:8]}@example.com", "Str0ngPass1!"
        buyer_id = _register_and_login(client, conn, buyer_email)
        pr_resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": str(uuid.uuid4())})
        assert pr_resp.status_code == 201
        pr_id = pr_resp.json()["id"]
        assert pr_resp.json()["status"] == "open"

        # 2) البائع يقدِّم عرض سعر — يجب أن ينتقل الطلب تلقائيًا لحالة under_review
        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"seller-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/store/stores", json={})
        offer_resp = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                  json={"amount": 350.0, "currency": "SAR", "provides_shipping": True})
        assert offer_resp.status_code == 201
        offer_id = offer_resp.json()["id"]

        get_pr_resp = client.get(f"/api/v1/purchase-requests/{pr_id}")
        assert get_pr_resp.json()["status"] == "under_review"  # REQ-PUR-006

        # 3) البائع يرسل رسالة بشأن العرض ضمن نفس سياق طلب الشراء
        message_resp = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": pr_id, "body": "أقدر أشحن خلال يومين",
        })
        assert message_resp.status_code == 201
        conversation_id = message_resp.json()["conversation_id"]

        # 4) إشعار للمشتري (يُزرَع هنا؛ لا Endpoint إنشاء حملات ضمن هذا الـIncrement)
        recipient_id = _make_recipient(conn, buyer_id)
        notification_entry = app.state.ntf_repository.insert_notification_center_entry(
            NotificationCenterEntry(id="", recipient_id=recipient_id, user_ref_id=buyer_id)
        )

        # 5) المشتري يعود، يقرأ الرسالة، يعلِّم الإشعار كمقروء، ثم يقبل العرض
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"login_identifier": buyer_email, "password": buyer_password})

        messages_resp = client.get(f"/api/v1/conversations/{conversation_id}/messages")
        assert messages_resp.status_code == 200
        assert len(messages_resp.json()) == 1

        read_resp = client.post(f"/api/v1/notifications/{notification_entry.id}/read")
        assert read_resp.status_code == 200
        assert read_resp.json()["is_read"] is True

        accept_resp = client.post(f"/api/v1/offers/{offer_id}/accept")
        assert accept_resp.status_code == 200
        assert accept_resp.json()["status"] == "fulfilled"  # REQ-PUR-013

        # التحقق النهائي المباشر من قاعدة البيانات نفسها، لا الطبقة العلوية فقط
        cur = conn.cursor()
        cur.execute("SELECT status FROM pur.purchase_requests WHERE id = %s", (pr_id,))
        assert cur.fetchone()["status"] == "fulfilled"
        cur.execute("SELECT status FROM pur.offers WHERE id = %s", (offer_id,))
        assert cur.fetchone()["status"] == "accepted"
        cur.execute("SELECT count(*) AS c FROM com.messages WHERE conversation_id = %s", (conversation_id,))
        assert cur.fetchone()["c"] == 1
        cur.execute("SELECT is_read FROM ntf.notification_center_entries WHERE id = %s", (notification_entry.id,))
        assert cur.fetchone()["is_read"] is True


class TestOfferSubmissionRequiresActiveStore:

    def test_seller_without_store_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        _register_and_login(client, conn, f"buyer2-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": str(uuid.uuid4())}).json()["id"]

        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"nostore-{uuid.uuid4().hex[:8]}@example.com")
        resp = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                            json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        assert resp.status_code == 403
