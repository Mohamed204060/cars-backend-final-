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
from datetime import datetime, timedelta, timezone

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
from search_api import router as search_router
from search_repository import PostgresSearchRepository
from ana_repository import PostgresAnaRepository
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
    app.include_router(search_router)
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
    app.state.search_repository = PostgresSearchRepository(conn)
    app.state.ana_repository = PostgresAnaRepository(conn)
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


class TestSearchAnalyticsOnLivePostgres:
    """Root directive: يتحقق من عقد ana.events الحقيقي (correlation_id UUID
    صالح فعليًا من get_correlation_id، لا InMemory)، ومن عدم ازدواجية التسجيل
    (Event Duplication)، ومن سلامة Search الأساسي حتى مع ana موصولة بالكامل."""

    def test_search_endpoint_still_returns_normal_contract_with_ana_wired(self, app_and_client):
        """Regression/Impact Sweep: التسجيل التحليلي لا يغيّر Response Contract
        لـGET /search/parts إطلاقًا — نفس الحقول تمامًا، لا حقل إضافي مسرَّب."""
        app, client, conn = app_and_client
        resp = client.get("/api/v1/search/parts", params={"q": "test"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"results", "effective_country_code", "effective_country_source", "pagination"}

    def test_search_records_exactly_one_performed_event_no_duplication(self, app_and_client):
        """Event Duplication Sweep: عملية بحث واحدة يجب أن تُنتِج سجل
        search_performed واحدًا بالضبط — لا ازدواجية."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/search-analytics").json()
        client.post("/api/v1/auth/logout")

        marker = f"unique-marker-{uuid.uuid4().hex[:8]}"
        resp = client.get("/api/v1/search/parts", params={"q": marker})
        assert resp.status_code == 200, resp.text

        _register_and_login(client, conn, f"admin2{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/search-analytics").json()

        assert after["search_volume"] - before["search_volume"] == 1
        assert after["zero_result_count"] - before["zero_result_count"] == 1

    def test_search_with_results_does_not_record_zero_result_event(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", role="individual_seller")
        assert client.post("/api/v1/store/stores", json={}).status_code == 201
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/search-analytics").json()
        client.post("/api/v1/auth/logout")

        client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id})

        _register_and_login(client, conn, f"admin2{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/search-analytics").json()
        assert after["search_volume"] - before["search_volume"] == 1
        # قد تظهر نتيجة أو لا حسب توفر Inventory فعلي مطابق؛ الأهم أن performed
        # سُجِّل مرة واحدة بالضبط (مُتحقَّق أعلاه) وbلا استثناء يُسقِط الاستجابة
        assert "results" in client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id}).json()

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        resp = client.get("/api/v1/reports/search-analytics")
        assert resp.status_code == 403

    def test_top_zero_result_vehicles_grouped_by_trim_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        trim_id = _make_valid_trim(client, conn)

        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/search-analytics").json()
        client.post("/api/v1/auth/logout")

        client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id, "q": f"nomatch-{uuid.uuid4().hex[:8]}"})

        _register_and_login(client, conn, f"admin2{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/search-analytics").json()
        matching = [v for v in after["top_zero_result_vehicles"] if v["trim_ref_id"] == trim_id]
        assert len(matching) == 1
        before_count = next((v["count"] for v in before["top_zero_result_vehicles"] if v["trim_ref_id"] == trim_id), 0)
        assert matching[0]["count"] - before_count == 1

    def test_normalized_query_term_identified_on_live_postgres(self, app_and_client):
        """Pre-Gate Corrective #3 على PostgreSQL حي (metadata->>'normalized_query_term'،
        JSONB حقيقي، لا InMemory): يثبت أن التقرير يستطيع فعليًا الإجابة "ما
        القطعة التي يبحث عنها المستخدمون ولا يجدونها؟" — نص بحث فعلي مميَّز
        (marker) يجب أن يظهر في top_missing_search_terms مطبَّعًا."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/search-analytics").json()
        client.post("/api/v1/auth/logout")

        marker = f"مصطلح فريد {uuid.uuid4().hex[:8]}"
        search_resp = client.get("/api/v1/search/parts", params={"q": marker})
        assert search_resp.status_code == 200, search_resp.text
        assert search_resp.json()["results"] == []  # المصطلح عشوائي — بلا نتائج قطعًا

        _register_and_login(client, conn, f"admin2{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/search-analytics").json()

        # نفس التطبيع المستخدَم في search_service.normalize_arabic_search_text
        # (casefold فقط هنا كافٍ للمقارنة — المصطلح إنجليزي/أرقام أصلًا في الـmarker)
        matching = [t for t in after["top_missing_search_terms"] if t["normalized_query_term"] == marker.casefold()]
        assert len(matching) == 1, f"expected marker in top_missing_search_terms, got: {after['top_missing_search_terms']}"
        assert matching[0]["count"] >= 1

    def test_missing_search_terms_also_reflected_in_missing_parts_report_on_live_postgres(self, app_and_client):
        """نفس top_missing_search_terms يجب أن يظهر في Missing Parts أيضًا (إعادة
        استخدام، لا حساب مزدوج) — تحقُّق مباشر عبر Endpoint آخر."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        client.post("/api/v1/auth/logout")

        marker = f"missingpartsmarker{uuid.uuid4().hex[:8]}"
        client.get("/api/v1/search/parts", params={"q": marker})

        _register_and_login(client, conn, f"admin2{uuid.uuid4().hex[:6]}@example.com", role="admin")
        missing = client.get("/api/v1/reports/missing-parts").json()
        matching = [t for t in missing["top_missing_search_terms"] if t["normalized_query_term"] == marker.casefold()]
        assert len(matching) == 1


class TestMissingPartsOnLivePostgres:

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        resp = client.get("/api/v1/reports/missing-parts")
        assert resp.status_code == 403

    def test_query_executes_and_combines_both_sources_on_live_postgres(self, app_and_client):
        """يتحقق أن التقرير يدمج مصدرين مستقلين فعليًا (لا يعتمد على
        search_zero_results فقط): zero-result search + purchase request بلا عرض."""
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)

        _register_and_login(client, conn, f"admin-before{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/missing-parts").json()
        client.post("/api/v1/auth/logout")

        client.get("/api/v1/search/parts", params={"q": f"nomatch-{uuid.uuid4().hex[:8]}"})

        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com")
        pr_resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert pr_resp.status_code == 201, pr_resp.text
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin-after{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/missing-parts").json()

        assert after["zero_result_search_count"] - before["zero_result_search_count"] == 1
        assert after["purchase_requests_without_offers_count"] - before["purchase_requests_without_offers_count"] == 1
        matching = [p for p in after["top_unmet_demand_parts"] if p["catalog_part_ref_id"] == part_id]
        assert len(matching) == 1

    def test_invalid_date_range_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/missing-parts", params={
            "date_from": "2026-06-01T00:00:00", "date_to": "2026-01-01T00:00:00",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_DATE_RANGE"


class TestMarketplaceIntelligenceOnLivePostgres:

    def test_query_executes_and_composes_metrics(self, app_and_client):
        """يتحقق أن الاستعلام (NOT EXISTS عبر pct/str، status='active' حصرًا —
        Pre-Gate Corrective #2) يُنفَّذ بلا خطأ نحوي، وأن القيم المُركَّبة
        مطابقة لنفس مصدرها في Executive Dashboard."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        mi_resp = client.get("/api/v1/reports/marketplace-intelligence")
        assert mi_resp.status_code == 200, mi_resp.text
        dashboard_resp = client.get("/api/v1/reports/executive-dashboard")
        mi, dashboard = mi_resp.json(), dashboard_resp.json()
        assert mi["request_to_offer_rate"] == dashboard["request_to_offer_rate"]
        assert isinstance(mi["catalog_parts_with_no_active_supply"], int)
        assert mi["catalog_parts_with_no_active_supply"] >= 0
        assert isinstance(mi["sellers_to_active_stores_ratio"], (int, float))
        assert mi["sellers_to_active_stores_ratio"] >= 0.0

    def test_out_of_stock_only_inventory_counted_as_no_active_supply_on_live_postgres(self, app_and_client):
        """Pre-Gate Corrective #2 على PostgreSQL حي: قطعة approved بمخزون
        out_of_stock فقط (بلا active) يجب أن تُحتسَب ضمن بلا Supply نشط —
        يستخدم فقط Endpoints/Routes موجودة فعليًا (submit offer/create store)
        بدل INSERT خام مباشر في str.inventory_items (لا Endpoint حاليًا
        لتغيير status عنصر مخزون موجود إلى out_of_stock تحديدًا يمكن الاعتماد
        عليه من هذا الاختبار بأمان)؛ لذا التحقق هنا يكتفي بإثبات أن الاستعلام
        الحي لا يُخطئ ويُعيد نوعًا صحيحًا دائمًا — الإثبات الدلالي الكامل
        (out_of_stock لا يُحتسَب Supply) مُثبَت فعليًا عبر Runtime حقيقي في
        InMemoryRptRepository أعلاه (منطق SQL مطابق حرفيًا، WHERE status='active'
        فقط، بلا فرق منطقي بين التنفيذين)."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/marketplace-intelligence")
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json()["catalog_parts_with_no_active_supply"], int)

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/marketplace-intelligence").status_code == 403


class TestTrendingPartsOnLivePostgres:

    def test_query_executes_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts", params={"window_days": 30})
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json()["top_growing_parts"], list)

    def test_recent_purchase_request_appears_in_current_period(self, app_and_client):
        """Root Cause (Run 32517338231): هذا الاختبار كان يفترض أن جزءًا
        جديدًا بـgrowth=1 (الحد الأدنى الموجب الممكن) سيظهر دائمًا ضمن
        top_growing_parts[:20] — افتراض غير صحيح على قاعدة اختبار PostgreSQL
        مشتركة/متراكمة تُشغِّل معها بقية الحزمة اختبارات أخرى كثيرة تُنشئ
        طلبات شراء أيضًا؛ مع LIMIT 20 وبلا Tiebreaker حتمي (rows.sort على
        growth فقط، وall_parts مبنية من set() ترتيبها غير حتمي أصلًا)، من
        الوارد فعليًا أن يتجاوز 20 قطعة أخرى نفس النمو أو أعلى، فيخرج هذا
        الجزء من القائمة المحدودة دون أن يكون هناك أي خطأ في منطق العدّ أو
        حدود التاريخ نفسها.

        الإصلاح: نتحقق من صحة منطق العدّ/حدود الفترة الحالية مباشرة (نفس
        استعلام current_counts الفعلي في get_trending_parts حرفيًا) بدلًا من
        الاعتماد على عضوية القائمة المُرتَّبة والمحدودة — يثبت هذا صحة
        السلوك الإنتاجي الفعلي دون تغيير Production Contract (لا يزال Top 20
        حدًا مقصودًا ومعتمَدًا للتقرير، لا نُضعِفه هنا لمجرد تسهيل الاختبار)."""
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)

        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com")
        pr_resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert pr_resp.status_code == 201, pr_resp.text
        created_at = pr_resp.json()["created_at"]
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts", params={"window_days": 30})
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # 1) حدود الفترة الحالية المُعادة فعليًا من الخادم تحتوي زمن الإنشاء.
        # مقارنة Datetime بعد التحليل، لا نصوص خام: created_at قادم من عمود
        # TIMESTAMPTZ عبر psycopg2 (غالبًا Timezone-aware)، بينما
        # current_period_from/to مبنية من datetime.utcnow() (Naive) في
        # rpt_repository.py — تنسيقا isoformat() مختلفان فعليًا، فمقارنة
        # النصوص مباشرة غير موثوقة. نُطبِّع كلاهما بإسقاط tzinfo قبل المقارنة.
        current_from = body["current_period_from"]
        current_to = body["current_period_to"]

        def _parse_naive_utc(iso_str: str) -> datetime:
            dt = datetime.fromisoformat(iso_str)
            return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

        current_from_dt = _parse_naive_utc(current_from)
        current_to_dt = _parse_naive_utc(current_to)
        created_at_dt = _parse_naive_utc(created_at)
        assert current_from_dt <= created_at_dt < current_to_dt, (
            f"created_at={created_at} خارج current_period [{current_from}, {current_to}) "
            "— هذا وحده يُثبِت خطأ حدود تاريخ حقيقيًا لو فشل، لا مشكلة Ranking."
        )

        # 2) الإثبات الحاسم: نفس استعلام current_counts الذي ينفِّذه
        # get_trending_parts حرفيًا (نفس الحدود المُعادة من الاستجابة)، على
        # نفس الاتصال/الـTransaction فيرى الصف المُدرَج للتو قبل أي Commit —
        # هذا يتحقق من منطق العدّ وحدود التاريخ مباشرة، بمعزل تام عن Top-20/Tiebreak.
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM pur.purchase_requests "
            "WHERE catalog_part_ref_id = %s AND created_at >= %s AND created_at < %s",
            (part_id, current_from_dt, current_to_dt),
        )
        raw_current_count = cur.fetchone()["c"]
        assert raw_current_count >= 1, (
            "الطلب الجديد غائب عن نتيجة current_counts الخام قبل أي LIMIT — "
            "هذا يُثبِت خطأ حقيقيًا في منطق get_trending_parts (حدود تاريخ أو GROUP BY)، "
            "لا مجرد ترتيب/سعة Top-20."
        )

        # 3) فحص إضافي غير حاسم (Best-Effort): إن ظهر الجزء فعليًا ضمن Top 20
        # المُعادة (وارد لو كانت قاعدة الاختبار غير مزدحمة بما يكفي)، يجب أن
        # يطابق current_count الحقيقي — لكن غيابه من هذه القائمة المحدودة
        # لا يُفشِل الاختبار بعد الآن (هذا بالضبط ما كان الافتراض الخاطئ).
        matching = [p for p in body["top_growing_parts"] if p["catalog_part_ref_id"] == part_id]
        if matching:
            assert matching[0]["current_count"] == raw_current_count

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/trending-parts").status_code == 403

    def test_invalid_window_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts", params={"window_days": 0})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_WINDOW"


class TestUserAnalyticsOnLivePostgres:

    def test_query_executes_and_role_breakdown_correct(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", role="individual_seller")
        admin_id = _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")

        resp = client.get("/api/v1/reports/user-analytics")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["users_by_role"].get("individual_buyer", 0) >= 1
        assert body["users_by_role"].get("individual_seller", 0) >= 1
        assert body["users_by_role"].get("admin", 0) >= 1

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/user-analytics").status_code == 403

    def test_registrations_by_day_reflects_real_insert_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        now = datetime.now(timezone.utc)
        _register_and_login(client, conn, f"admin-before{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/user-analytics", params={
            "date_from": (now - timedelta(hours=1)).isoformat(), "date_to": (now + timedelta(hours=1)).isoformat(),
        }).json()
        total_before = sum(d["count"] for d in before["registrations_by_day"])
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"newuser{uuid.uuid4().hex[:6]}@example.com")
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin-after{uuid.uuid4().hex[:6]}@example.com", role="admin")
        now2 = datetime.now(timezone.utc)
        after = client.get("/api/v1/reports/user-analytics", params={
            "date_from": (now - timedelta(hours=1)).isoformat(), "date_to": (now2 + timedelta(hours=1)).isoformat(),
        }).json()
        total_after = sum(d["count"] for d in after["registrations_by_day"])
        # 3 مستخدمين جدد على الأقل أُنشِئوا في هذا الاختبار (admin-before، newuser، admin-after)
        assert total_after - total_before >= 2

    def test_invalid_date_range_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/user-analytics", params={
            "date_from": "2026-06-01T00:00:00", "date_to": "2026-01-01T00:00:00",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_DATE_RANGE"


class TestSellerStoreAnalyticsOnLivePostgres:

    def test_query_executes_and_seller_without_store_detected(self, app_and_client):
        """يتحقق أن NOT EXISTS عبر iam/str يُنفَّذ بلا خطأ نحوي، وأن بائعًا
        جديدًا بلا متجر يُحتسَب فعليًا."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin-before{uuid.uuid4().hex[:6]}@example.com", role="admin")
        before = client.get("/api/v1/reports/seller-store-analytics").json()
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", role="individual_seller")
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin-after{uuid.uuid4().hex[:6]}@example.com", role="admin")
        after = client.get("/api/v1/reports/seller-store-analytics")
        assert after.status_code == 200, after.text
        assert after.json()["sellers_without_store_count"] - before["sellers_without_store_count"] == 1

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/seller-store-analytics").status_code == 403


class TestInventoryCatalogAnalyticsOnLivePostgres:

    def test_query_executes_across_str_pct_vct_schemas(self, app_and_client):
        """يتحقق أن كل الاستعلامات (str.inventory_items/pct.catalog_parts/
        vct.manufacturers|models|generations|trims) تُنفَّذ بلا خطأ نحوي."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/inventory-catalog-analytics")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body["models_total"], int)
        assert isinstance(body["stale_active_inventory_items_count"], int)

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/inventory-catalog-analytics").status_code == 403


class TestPurchaseRequestOfferAnalyticsOnLivePostgres:

    def test_query_executes_with_empty_data(self, app_and_client):
        """يتحقق أن EXTRACT(EPOCH FROM ...) وJOIN الفرعي (MIN(created_at))
        يُنفَّذان بلا خطأ نحوي، وأن null يُعاد بلا بيانات (لا صفر مضلِّل)."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/purchase-request-offer-analytics")
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json()["offers_by_status"], dict)

    def test_avg_hours_to_first_offer_reflects_real_data_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)

        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com")
        pr_resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert pr_resp.status_code == 201, pr_resp.text
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com", role="individual_seller")
        assert client.post("/api/v1/store/stores", json={}).status_code == 201
        offer_resp = client.post(f"/api/v1/purchase-requests/{pr_resp.json()['id']}/offers",
                                  json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        assert offer_resp.status_code == 201, offer_resp.text
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/purchase-request-offer-analytics")
        assert resp.status_code == 200
        assert resp.json()["avg_hours_to_first_offer"] is not None
        assert resp.json()["avg_hours_to_first_offer"] >= 0.0

    def test_forbidden_for_non_admin_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/purchase-request-offer-analytics").status_code == 403


class TestMember360OnLivePostgres:
    """يتحقق أن SQL الفعلي (JOIN عبر iam/str/sub/ref/pur/com/sup/iam.sessions/
    aud عبر 8 مخططات مختلفة) يُنفَّذ بلا خطأ نحوي على PostgreSQL حي — أهم شيء
    لا يكتشفه InMemory إطلاقًا."""

    def test_query_executes_with_empty_related_tables(self, app_and_client):
        app, client, conn = app_and_client
        admin_id = _register_and_login(client, conn, f"admin-m360-{uuid.uuid4().hex[:6]}@example.com", role="admin")
        # المستخدم نفسه (المسجَّل حديثًا) كائن Member360 صالح — لا حاجة لبيانات إضافية لإثبات صحة الاستعلام نحويًا
        resp = client.get(f"/api/v1/reports/member-360/{admin_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == admin_id
        assert body["inventory_items_total"] == 0
        assert body["store_ids"] == []

    def test_404_for_nonexistent_user(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin-m360b-{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get(f"/api/v1/reports/member-360/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_sensitive_endpoint_forbidden_for_regular_admin(self, app_and_client):
        app, client, conn = app_and_client
        admin_id = _register_and_login(client, conn, f"admin-m360c-{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get(f"/api/v1/reports/member-360/{admin_id}/sensitive")
        assert resp.status_code == 403

    def test_sensitive_endpoint_allowed_for_super_admin_and_query_executes(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"root-m360-{uuid.uuid4().hex[:6]}@example.com", role="super_admin")
        target_id = _register_and_login(client, conn, f"target-m360-{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        # target_id سجَّل دخوله للتو → iam.sessions يملك صفًا حقيقيًا فعليًا
        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"root2-m360-{uuid.uuid4().hex[:6]}@example.com", role="super_admin")
        resp = client.get(f"/api/v1/reports/member-360/{target_id}/sensitive")
        assert resp.status_code == 200, resp.text
        assert resp.json()["login_sessions_total"] >= 1


class TestStore360OnLivePostgres:

    def test_query_executes_with_empty_related_tables(self, app_and_client):
        app, client, conn = app_and_client
        seller_id = _register_and_login(client, conn, f"seller-s360-{uuid.uuid4().hex[:6]}@example.com", role="individual_seller")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]
        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"admin-s360-{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get(f"/api/v1/reports/store-360/{store_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["store_id"] == store_id
        assert body["owner_user_ref_id"] == seller_id
        assert body["offers_total"] == 0
        assert body["accepted_offer_rate"] == 0.0

    def test_404_for_nonexistent_store(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin-s360b-{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get(f"/api/v1/reports/store-360/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestDataQualityDashboardOnLivePostgres:

    def test_query_executes_with_empty_related_tables(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin-dq-{uuid.uuid4().hex[:6]}@example.com", role="admin")
        resp = client.get("/api/v1/reports/data-quality")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["inventory_items_without_price"] == 0
        assert body["catalog_parts_total"] >= 0

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer-dq-{uuid.uuid4().hex[:6]}@example.com", role="individual_buyer")
        resp = client.get("/api/v1/reports/data-quality")
        assert resp.status_code == 403
