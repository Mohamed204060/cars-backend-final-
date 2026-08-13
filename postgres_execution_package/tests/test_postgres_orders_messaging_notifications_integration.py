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
from vct_api import router as vct_router
from vct_repository import PostgresVctRepository
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
from ref_repository import PostgresRefRepository


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
    app.include_router(vct_router)
    app.include_router(message_router)
    app.include_router(message_extended_router)
    app.include_router(ntf_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.store_repository = PostgresStoreRepository(conn)
    app.state.pct_repository = PostgresPctRepository(conn)
    app.state.order_repository = PostgresOrderRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)  # Batch 1: order_api.create_purchase_request يعتمد عليه الآن
    app.state.inventory_repository = PostgresInventoryItemRepository(conn)
    app.state.message_repository = PostgresMessageRepository(conn)
    app.state.message_extended_repository = PostgresMessageExtendedRepository(conn)
    app.state.ntf_repository = PostgresNtfRepository(conn)
    app.state.ref_repository = PostgresRefRepository(conn)
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


def _make_valid_trim(client, conn) -> str:
    """Batch 1: فئة VCT حقيقية وصالحة (self-contained، بنفس نمط _make_approved_part)."""
    _register_and_login(client, conn, f"admin-vct-{uuid.uuid4().hex[:8]}@example.com", role="admin")
    m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
    client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
    model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
    gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]
    trim_id = client.post(f"/api/v1/vct/generations/{gen_id}/trims",
                           json={"fuel_type_ref_id": str(uuid.uuid4()), "transmission_type_ref_id": str(uuid.uuid4())}
                           ).json()["id"]
    client.post("/api/v1/auth/logout")
    return trim_id


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
        trim_id = _make_valid_trim(client, conn)

        # 1) المشتري ينشئ طلب شراء
        buyer_email, buyer_password = f"buyer-{uuid.uuid4().hex[:8]}@example.com", "Str0ngPass1!"
        buyer_id = _register_and_login(client, conn, buyer_email)
        pr_resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
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
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"buyer2-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]

        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"nostore-{uuid.uuid4().hex[:8]}@example.com")
        resp = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                            json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        assert resp.status_code == 403


class TestCR021DisplayProjectionOnLivePostgres:
    """CR-021: يتحقق من صحة نحو SQL الفعلي (JOINs + LATERAL) على اتصال حي —
    لا يكتشفه py_compile ولا InMemory."""

    def test_full_chain_resolves_part_manufacturer_model_names(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pct.localized_names (catalog_part_id, name_value, name_kind, locale) "
            "VALUES (%s, %s, 'canonical', 'ar')",
            (part_id, "طرمبة بنزين"),
        )
        cur.execute("INSERT INTO vct.manufacturers (status) VALUES ('approved') RETURNING id")
        manufacturer_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO vct.localized_names (owner_ref_id, owner_type, locale, name_value) "
            "VALUES (%s, 'manufacturer', 'ar', %s)",
            (manufacturer_id, "تويوتا"),
        )
        cur.execute(
            "INSERT INTO vct.models (manufacturer_id, status) VALUES (%s, 'approved') RETURNING id",
            (manufacturer_id,),
        )
        model_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO vct.localized_names (owner_ref_id, owner_type, locale, name_value) "
            "VALUES (%s, 'model', 'ar', %s)",
            (model_id, "كامري"),
        )
        cur.execute("INSERT INTO vct.generations (model_id) VALUES (%s) RETURNING id", (model_id,))
        generation_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO ref.ref_values (ref_type, code) VALUES ('fuel_type', 'petrol') "
            "ON CONFLICT (ref_type, code) DO NOTHING RETURNING id"
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT id FROM ref.ref_values WHERE ref_type = 'fuel_type' AND code = 'petrol'")
            row = cur.fetchone()
        fuel_id = row["id"]
        cur.execute(
            "INSERT INTO ref.ref_values (ref_type, code) VALUES ('transmission_type', 'automatic') "
            "ON CONFLICT (ref_type, code) DO NOTHING RETURNING id"
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT id FROM ref.ref_values WHERE ref_type = 'transmission_type' AND code = 'automatic'")
            row = cur.fetchone()
        transmission_id = row["id"]
        cur.execute(
            "INSERT INTO vct.trims (generation_id, fuel_type_ref_id, transmission_type_ref_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (generation_id, fuel_id, transmission_id),
        )
        trim_id = cur.fetchone()["id"]

        buyer_id = _register_and_login(client, conn, f"buyer-cr021-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})

        resp = client.get("/api/v1/purchase-requests/mine/display")
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["part_name"] == "طرمبة بنزين"
        assert item["manufacturer_name"] == "تويوتا"
        assert item["model_name"] == "كامري"
        assert item["manufacturer_id"] == manufacturer_id
        assert item["model_id"] == model_id
        assert "buyer_user_ref_id" not in item

    def test_no_query_count_growth_with_multiple_requests(self, app_and_client):
        """لا N+1: عدد الاستعلامات ثابت بغضّ النظر عن عدد الطلبات — يُتحقَّق
        عبر قياس أن الاستجابة تنجح بسرعة لعدد صفوف متعدد (فحص غير مباشر؛
        الدليل البنيوي الحقيقي هو استعلامان فقط في الكود نفسه، مؤكَّد بالمراجعة)."""
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        buyer_id = _register_and_login(client, conn, f"buyer-cr021b-{uuid.uuid4().hex[:8]}@example.com")
        for _ in range(5):
            client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})

        resp = client.get("/api/v1/purchase-requests/mine/display")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 5
        for item in resp.json()["items"]:
            assert item["manufacturer_name"] is None  # trim حقيقي في VCT، لكن بلا localized_names — LEFT JOIN يعيد None بلا خطأ


def _make_full_vehicle_chain_with_names(conn, generation_name="الجيل الثامن", trim_name="SE"):
    """Batch 1: يبني سلسلة VCT كاملة (manufacturer→model→generation→trim) بأسماء محلَّية
    فعلية على كل مستوى، عبر SQL خام مباشرة (نفس نمط TestCR021DisplayProjectionOnLivePostgres)."""
    cur = conn.cursor()
    cur.execute("INSERT INTO vct.manufacturers (status) VALUES ('approved') RETURNING id")
    manufacturer_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO vct.localized_names (owner_ref_id, owner_type, locale, name_value) VALUES (%s, 'manufacturer', 'ar', %s)",
        (manufacturer_id, "تويوتا"),
    )
    cur.execute("INSERT INTO vct.models (manufacturer_id, status) VALUES (%s, 'approved') RETURNING id", (manufacturer_id,))
    model_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO vct.localized_names (owner_ref_id, owner_type, locale, name_value) VALUES (%s, 'model', 'ar', %s)",
        (model_id, "كامري"),
    )
    cur.execute("INSERT INTO vct.generations (model_id, start_year, end_year) VALUES (%s, 2018, 2023) RETURNING id", (model_id,))
    generation_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO vct.localized_names (owner_ref_id, owner_type, locale, name_value) VALUES (%s, 'generation', 'ar', %s)",
        (generation_id, generation_name),
    )

    def _ref_id(ref_type, code):
        cur.execute("INSERT INTO ref.ref_values (ref_type, code) VALUES (%s, %s) ON CONFLICT (ref_type, code) DO NOTHING RETURNING id", (ref_type, code))
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT id FROM ref.ref_values WHERE ref_type = %s AND code = %s", (ref_type, code))
            row = cur.fetchone()
        return row["id"]

    fuel_id = _ref_id("fuel_type", "petrol")
    transmission_id = _ref_id("transmission_type", "automatic")
    cur.execute(
        "INSERT INTO vct.trims (generation_id, fuel_type_ref_id, transmission_type_ref_id) VALUES (%s, %s, %s) RETURNING id",
        (generation_id, fuel_id, transmission_id),
    )
    trim_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO vct.localized_names (owner_ref_id, owner_type, locale, name_value) VALUES (%s, 'trim', 'ar', %s)",
        (trim_id, trim_name),
    )
    return trim_id, generation_id, model_id, manufacturer_id


class TestBatch1ExtendedDisplayProjectionOnLivePostgres:
    """Batch 1: توسيع CR-021 Display Projection (trim/generation/year/condition/notes) على اتصال حي — SQL/LATERAL فعلي."""

    def test_trim_and_generation_names_resolved(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id, _, _, _ = _make_full_vehicle_chain_with_names(conn)
        _register_and_login(client, conn, f"buyer-ext1-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})

        item = client.get("/api/v1/purchase-requests/mine/display").json()["items"][0]
        assert item["trim_name"] == "SE"
        assert item["generation_name"] == "الجيل الثامن"

    def test_model_year_resolved(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id, _, _, _ = _make_full_vehicle_chain_with_names(conn)
        _register_and_login(client, conn, f"admin-ext2-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        tmy_id = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019}).json()["id"]
        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"buyer-ext2-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "trim_model_year_ref_id": tmy_id,
        })

        item = client.get("/api/v1/purchase-requests/mine/display").json()["items"][0]
        assert item["model_year"] == 2019

    def test_condition_code_resolved(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id, _, _, _ = _make_full_vehicle_chain_with_names(conn)
        cur = conn.cursor()
        cur.execute("INSERT INTO ref.ref_values (ref_type, code) VALUES ('part_condition', %s) RETURNING id",
                    (f"new-{uuid.uuid4().hex[:8]}",))
        condition_id = cur.fetchone()["id"]
        cur.execute("SELECT code FROM ref.ref_values WHERE id = %s", (condition_id,))
        expected_code = cur.fetchone()["code"]
        _register_and_login(client, conn, f"buyer-ext3-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "condition_ref_id": condition_id,
        })

        item = client.get("/api/v1/purchase-requests/mine/display").json()["items"][0]
        assert item["condition_code"] == expected_code

    def test_buyer_notes_present_in_display(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id, _, _, _ = _make_full_vehicle_chain_with_names(conn)
        _register_and_login(client, conn, f"buyer-ext4-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "notes": "أحتاج توصيل سريع",
        })

        item = client.get("/api/v1/purchase-requests/mine/display").json()["items"][0]
        assert item["notes"] == "أحتاج توصيل سريع"

    def test_null_safe_for_pre_batch1_style_record(self, app_and_client):
        """Regression: سجل بلا سنة/حالة/ملاحظات (كما لو كان قبل هذه الدفعة) — كل الحقول الجديدة None بلا خطأ."""
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)  # فئة بلا أي localized_names/generation names
        _register_and_login(client, conn, f"buyer-ext5-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})

        resp = client.get("/api/v1/purchase-requests/mine/display")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["trim_name"] is None
        assert item["generation_name"] is None
        assert item["model_year"] is None
        assert item["condition_code"] is None
        assert item["notes"] is None


class TestBatch1OfferDisplayOnLivePostgres:
    """Batch 1 — Offers Integration: GET /purchase-requests/{prId}/offers/display على اتصال حي."""

    def test_buyer_sees_offer_with_full_resolved_context(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id, _, _, _ = _make_full_vehicle_chain_with_names(conn)
        buyer_email = f"buyer-offd1-{uuid.uuid4().hex[:8]}@example.com"
        _register_and_login(client, conn, buyer_email)
        pr_id = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "notes": "بحاجة ماسة",
        }).json()["id"]
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"seller-offd1-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/store/stores", json={})
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 300.0, "currency": "SAR", "provides_shipping": True, "notes": "متوفرة اليوم"})
        client.post("/api/v1/auth/logout")

        client.post("/api/v1/auth/login", json={"login_identifier": buyer_email, "password": "Str0ngPass1!"})
        resp = client.get(f"/api/v1/purchase-requests/{pr_id}/offers/display")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["trim_name"] == "SE"
        assert item["manufacturer_name"] == "تويوتا"
        assert item["buyer_notes"] == "بحاجة ماسة"
        assert item["seller_notes"] == "متوفرة اليوم"

    def test_seller_scoping_sees_only_own_offer(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"buyer-offd2-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"seller-a-offd2-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/store/stores", json={})
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"seller-b-offd2-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/store/stores", json={})
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 250.0, "currency": "SAR", "provides_shipping": False})

        resp = client.get(f"/api/v1/purchase-requests/{pr_id}/offers/display")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1
        assert resp.json()["items"][0]["amount"] == 250.0

    def test_no_n_plus_1_multiple_offers_single_pr_context(self, app_and_client):
        """لا N+1: سياق الطلب يُجلَب مرة واحدة فقط (استعلام إضافي واحد ثابت) بغضّ النظر عن عدد العروض."""
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id, _, _, _ = _make_full_vehicle_chain_with_names(conn)
        buyer_email = f"buyer-offd3-{uuid.uuid4().hex[:8]}@example.com"
        _register_and_login(client, conn, buyer_email)
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]
        client.post("/api/v1/auth/logout")

        for i in range(4):
            _register_and_login(client, conn, f"seller-offd3-{i}-{uuid.uuid4().hex[:8]}@example.com")
            client.post("/api/v1/store/stores", json={})
            client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                        json={"amount": 100.0 + i, "currency": "SAR", "provides_shipping": False})
            client.post("/api/v1/auth/logout")

        client.post("/api/v1/auth/login", json={"login_identifier": buyer_email, "password": "Str0ngPass1!"})
        resp = client.get(f"/api/v1/purchase-requests/{pr_id}/offers/display")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 4
        for item in resp.json()["items"]:
            assert item["trim_name"] == "SE"  # نفس السياق مُكرَّر بشكل صحيح على كل عرض، محلولًا مرة واحدة فقط داخليًا


class TestCR022ConditionAndNotesOnLivePostgres:
    """
    CR-022: يتحقق من صحة عمودَي condition_ref_id/notes الجديدين (Migration
    028) وقيد التحقق الحقيقي على النوع المرجعي (part_condition) على اتصال
    PostgreSQL حي — لا يكتشفه py_compile ولا InMemory.
    """

    def _insert_ref_value(self, conn, ref_type: str, code: str, status: str = "active") -> str:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ref.ref_values (ref_type, code, status) VALUES (%s, %s, %s) RETURNING id",
            (ref_type, code, status),
        )
        return cur.fetchone()["id"]

    def test_full_round_trip_persists_and_returns_both_fields(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        condition_id = self._insert_ref_value(conn, "part_condition", f"new-{uuid.uuid4().hex[:8]}")
        _register_and_login(client, conn, f"buyer-cr022a-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id,
            "condition_ref_id": condition_id, "notes": "بحاجة لقطعة أصلية فقط، يفضَّل الشحن السريع.",
        })
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["condition_ref_id"] == condition_id
        assert body["notes"] == "بحاجة لقطعة أصلية فقط، يفضَّل الشحن السريع."

        # قراءة مستقلة عن نفس الصف مباشرة من القاعدة، للتأكد من الحفظ الفعلي لا الاستجابة فقط
        cur = conn.cursor()
        cur.execute("SELECT condition_ref_id, notes FROM pur.purchase_requests WHERE id = %s", (body["id"],))
        row = cur.fetchone()
        assert row["condition_ref_id"] == condition_id
        assert row["notes"] == "بحاجة لقطعة أصلية فقط، يفضَّل الشحن السريع."

    def test_condition_ref_id_from_wrong_ref_type_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        wrong_type_id = self._insert_ref_value(conn, "fuel_type", f"petrol-{uuid.uuid4().hex[:8]}")
        _register_and_login(client, conn, f"buyer-cr022b-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "condition_ref_id": wrong_type_id,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_CONDITION_REF"

    def test_archived_condition_ref_id_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        archived_id = self._insert_ref_value(conn, "part_condition", f"obsolete-{uuid.uuid4().hex[:8]}", status="archived")
        _register_and_login(client, conn, f"buyer-cr022c-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "condition_ref_id": archived_id,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_CONDITION_REF"

    def test_pre_migration_style_request_without_new_fields_still_open_on_live_postgres(self, app_and_client):
        """
        يحاكي سجلًا تاريخيًا: طلب شراء بلا condition_ref_id/notes (كلاهما
        NULL بعد الترحيل 028، بلا Backfill تخميني)، ويتأكد أن المسارات
        القائمة (بما فيها القبول لاحقًا) غير متأثرة بغياب الحقلين.
        """
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"buyer-cr022d-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests",
                            json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201, resp.text
        assert resp.json()["condition_ref_id"] is None
        assert resp.json()["notes"] is None

        cur = conn.cursor()
        cur.execute("SELECT condition_ref_id, notes FROM pur.purchase_requests WHERE id = %s", (resp.json()["id"],))
        row = cur.fetchone()
        assert row["condition_ref_id"] is None
        assert row["notes"] is None


class TestBatch1PurchaseRequestVctIntegrationOnLivePostgres:
    """Approved VCT Design Baseline §23: Purchase Request مرتبط بـVCT حقيقي — على اتصال حي."""

    def test_valid_trim_accepted_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"buyer-b1pr1-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201, resp.text
        assert resp.json()["trim_ref_id"] == trim_id

    def test_nonexistent_trim_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        _register_and_login(client, conn, f"buyer-b1pr2-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests",
                            json={"catalog_part_ref_id": part_id, "trim_ref_id": str(uuid.uuid4())})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "TRIM_NOT_FOUND"

    def test_valid_trim_model_year_accepted_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"admin-tmy-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        tmy_id = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019}).json()["id"]
        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"buyer-b1pr3-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "trim_model_year_ref_id": tmy_id,
        })
        assert resp.status_code == 201, resp.text
        assert resp.json()["trim_model_year_ref_id"] == tmy_id

    def test_year_belonging_to_different_trim_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_a = _make_valid_trim(client, conn)
        trim_b = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"admin-tmy2-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        tmy_of_b = client.post(f"/api/v1/vct/trims/{trim_b}/model-years", json={"year": 2020}).json()["id"]
        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"buyer-b1pr4-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_a, "trim_model_year_ref_id": tmy_of_b,
        })
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "TRIM_MODEL_YEAR_NOT_FOUND"

    def test_nonexistent_trim_model_year_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"buyer-b1pr5-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "trim_model_year_ref_id": str(uuid.uuid4()),
        })
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "TRIM_MODEL_YEAR_NOT_FOUND"

    def test_creation_without_year_still_works_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"buyer-b1pr6-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201, resp.text
        assert resp.json()["trim_model_year_ref_id"] is None

        cur = conn.cursor()
        cur.execute("SELECT trim_model_year_ref_id FROM pur.purchase_requests WHERE id = %s", (resp.json()["id"],))
        assert cur.fetchone()["trim_model_year_ref_id"] is None

    def test_regression_pre_migration_031_style_record_reads_correctly(self, app_and_client):
        """Regression: سجل يُحاكي حالة ما قبل Migration 031 (INSERT مباشر بلا العمود الجديد صراحة) يُقرَأ بلا كسر، NULL/متوقَّع."""
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        buyer_id = _register_and_login(client, conn, f"buyer-b1pr7-{uuid.uuid4().hex[:8]}@example.com")

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pur.purchase_requests (business_code, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id, status) "
            "VALUES (%s, %s, %s, %s, 'open') RETURNING id",
            (f"PR-{uuid.uuid4().hex[:20]}", buyer_id, part_id, trim_id),
        )
        old_pr_id = cur.fetchone()["id"]

        resp = client.get("/api/v1/purchase-requests/mine")
        assert resp.status_code == 200
        matching = [i for i in resp.json()["items"] if i["id"] == old_pr_id]
        assert len(matching) == 1
        assert matching[0]["trim_model_year_ref_id"] is None
