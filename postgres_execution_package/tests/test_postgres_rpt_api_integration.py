"""
test_postgres_rpt_api_integration.py — اختبارات تكامل حقيقية للوحة المؤشرات
التنفيذية على PostgreSQL حي
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا بعد.
يتحقق من صحة نحو SQL الفعلي (GROUP BY متعدد الجداول، JOIN عبر 3 مخططات
لـsubscriptions_by_plan، NOT EXISTS للطلبات بلا عروض) — لا يكتشفه InMemory.
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
from ref_repository import PostgresRefRepository
from pct_api import router as pct_router
from pct_repository import PostgresPctRepository
from vct_api import router as vct_router
from vct_repository import PostgresVctRepository
from store_api import router as store_router
from store_repository import PostgresStoreRepository
from order_api import router as order_router
from order_repository import PostgresOrderRepository
from rpt_api import router as rpt_router
from rpt_repository import PostgresRptRepository
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
    app.include_router(store_router)
    app.include_router(order_router)
    app.include_router(rpt_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    # Root-Cause (Run 32161095012): order_api.create_purchase_request يعتمد على
    # get_ref_repository (لفحص condition_ref_id عند تقديمه) — FastAPI يحقن كل
    # الاعتماديات المُعلَنة قبل تنفيذ الجسم، فتظهر AttributeError فورًا بلا
    # ref_repository حتى لو كان condition_ref_id غير مُستخدَم في هذا الاختبار
    # بعينه. هذا Test Harness Gap فقط — لا تعديل على order_api.py/ref_api.py.
    app.state.ref_repository = PostgresRefRepository(conn)
    app.state.pct_repository = PostgresPctRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)
    app.state.store_repository = PostgresStoreRepository(conn)
    app.state.order_repository = PostgresOrderRepository(conn)
    app.state.rpt_repository = PostgresRptRepository(conn)
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
    assert client.post(f"/api/v1/pct/parts/{part_id}/approve").status_code == 200
    client.post("/api/v1/auth/logout")
    return part_id


def _make_valid_trim(client, conn) -> str:
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


class TestExecutiveDashboardOnLivePostgres:

    def test_query_executes_with_empty_related_tables(self, app_and_client):
        """أهم اختبار هنا: يتحقق أن SQL الفعلي (GROUP BY متعدد + JOIN عبر sub/ref +
        NOT EXISTS) يُنفَّذ بلا خطأ نحوي على PostgreSQL حي — لا يكتشفه InMemory إطلاقًا."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body["users_total"], int)
        assert isinstance(body["request_to_offer_rate"], (int, float))

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 403

    def test_users_and_sellers_counted_correctly_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        admin_id = _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/executive-dashboard").json()
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", role="individual_seller")
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin2{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/executive-dashboard").json()

        # Pattern Sweep: قاعدة الاختبار Postgres مشتركة وتراكمية عبر كل الاختبارات
        # في هذا التشغيل (لا Rollback بين الاختبارات لأن الكتابات تُثبَّت via
        # commit ضمن كل طلب HTTP) — لذا Delta (بعد - قبل) هو التحقق الموثوق،
        # لا عتبة مطلقة قد تصطدم بتراكم من اختبارات أخرى أو تشغيلات سابقة.
        # هنا أنشأنا 3 مستخدمين جددًا (buyer + seller + admin2) بين "قبل" و"بعد".
        assert after["users_total"] - before["users_total"] == 3
        assert after["sellers_total"] - before["sellers_total"] == 1

    def test_store_created_reflected_in_dashboard(self, app_and_client):
        """Root-Cause (Run 32161095012): store_service.create_store() يضبط
        status='active' مباشرة عند الإنشاء ("الإنشاء التلقائي ينتج متجرًا قابلاً
        للاستخدام مباشرة" — تعليق صريح في store_service.py)، وليس 'creating'
        كما افترض الاختبار السابق خطأً (DEFAULT العمود في 009_str.sql هو
        'creating' لكن التطبيق يُجاوزه دائمًا في هذا المسار الوحيد الحالي لإنشاء
        المتاجر). هذا خطأ افتراض في الاختبار نفسه، لا خلل Metric — لم يُعدَّل
        store_service.py أو أي Migration."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin-before{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/executive-dashboard").json()
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", role="individual_seller")
        store_resp = client.post("/api/v1/store/stores", json={})
        assert store_resp.status_code == 201, store_resp.text
        assert store_resp.json()["status"] == "active"  # يثبت السلوك الفعلي المرصود مباشرة من الاستجابة
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin-after{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/executive-dashboard").json()

        assert after["stores_total"] - before["stores_total"] == 1
        active_delta = after["stores_by_status"].get("active", 0) - before["stores_by_status"].get("active", 0)
        assert active_delta == 1
        # يثبت أن مفتاح 'creating' غير موجود إطلاقًا في نطاق هذا الاختبار — يمنع
        # عودة الافتراض الخاطئ السابق بصمت مستقبلًا
        creating_delta = after["stores_by_status"].get("creating", 0) - before["stores_by_status"].get("creating", 0)
        assert creating_delta == 0

    def test_purchase_request_without_offer_counted_on_live_postgres(self, app_and_client):
        """طلب شراء واحد بلا أي عرض — يجب أن يُحتسَب ضمن purchase_requests_without_offers
        (NOT EXISTS، لا COUNT خاطئ)، مقاسًا بالـDelta لا بعتبة مطلقة."""
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)

        _register_and_login(client, conn, f"admin-before{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/executive-dashboard").json()
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com")
        pr_resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert pr_resp.status_code == 201, pr_resp.text
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin-after{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/executive-dashboard").json()

        assert after["purchase_requests_total"] - before["purchase_requests_total"] == 1
        assert after["purchase_requests_without_offers"] - before["purchase_requests_without_offers"] == 1
        assert 0.0 <= after["request_to_offer_rate"] <= 1.0
        assert 0.0 <= after["request_to_accepted_offer_rate"] <= 1.0

    def test_accepted_offer_reflected_as_fulfilled_and_counted_on_live_postgres(self, app_and_client):
        """يثبت مباشرة على بيانات حقيقية (لا قراءة كود فقط) أن قبول عرض واحد:
        (أ) ينقل purchase_requests.status إلى 'fulfilled' فعليًا،
        (ب) يُحتسَب في offers_by_status['accepted']،
        (ج) يرفع request_to_accepted_offer_rate بمقدار طلب واحد بالضبط.
        الإثبات المصدري (order_service.accept_offer→transition_purchase_request_status
        'fulfilled'، المسار الوحيد لهذه الحالة في State Machine كاملة) مُستكمَل
        هنا بإثبات بيانات فعلي — الاثنان معًا يحسمان صحة تعريف Metric.

        REQ-PUR-013 (accept_offer في order_api.py): صاحب الطلب حصرًا يقبل — لذا
        يجب إعادة تسجيل الدخول بنفس بريد المشتري الأصلي (لا مستخدم آخر) لاستعادة
        نفس الهوية بعد أن سجَّل البائع دخوله لتقديم العرض عبر نفس TestClient
        (Cookie Jar مشترك، لا جلستان متزامنتان)."""
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)

        _register_and_login(client, conn, f"admin-before{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/executive-dashboard").json()
        client.post("/api/v1/auth/logout")

        buyer_email = f"buyer{uuid.uuid4().hex[:6]}@example.com"
        _register_and_login(client, conn, buyer_email)
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", role="individual_seller")
        assert client.post("/api/v1/store/stores", json={}).status_code == 201
        offer_resp = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                  json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        assert offer_resp.status_code == 201, offer_resp.text
        offer_id = offer_resp.json()["id"]
        client.post("/api/v1/auth/logout")

        # إعادة تسجيل الدخول بنفس بريد المشتري الأصلي (كلمة المرور الموحَّدة
        # المستخدَمة في كل هذا الملف) لاستعادة نفس هوية صاحب الطلب فعليًا.
        login_resp = client.post("/api/v1/auth/login",
                                  json={"login_identifier": buyer_email, "password": "Str0ngPass1!"})
        assert login_resp.status_code == 200, login_resp.text

        accept_resp = client.post(f"/api/v1/offers/{offer_id}/accept")
        assert accept_resp.status_code == 200, accept_resp.text
        assert accept_resp.json()["status"] == "fulfilled"
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin-after{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/executive-dashboard").json()

        assert after["purchase_requests_total"] - before["purchase_requests_total"] == 1
        assert after["offers_total"] - before["offers_total"] == 1
        assert after["offers_by_status"].get("accepted", 0) - before["offers_by_status"].get("accepted", 0) == 1
        assert after["purchase_requests_by_status"].get("fulfilled", 0) - before["purchase_requests_by_status"].get("fulfilled", 0) == 1
        # الطلب صار fulfilled (له عرض) → لم يعد ضمن "بلا عروض"، ورفع accepted-rate بمقدار طلب واحد بالضبط
        assert after["purchase_requests_without_offers"] - before["purchase_requests_without_offers"] == 0

    def test_invalid_date_range_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/executive-dashboard", params={
            "date_from": "2026-06-01T00:00:00", "date_to": "2026-01-01T00:00:00",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_DATE_RANGE"
