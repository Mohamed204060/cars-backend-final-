"""
test_postgres_search_api_integration.py — اختبارات تكامل حقيقية لطبقة REST API
لخدمة البحث على PostgreSQL حي
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا
بعد؛ لا اتصال شبكة أو محرك PostgreSQL متاح في بيئة إعداد هذه الحزمة.

ملاحظة بنيوية: str.stores وstr.inventory_items لا REST API لهما بعد (خدمة
Store/Inventory ضمن الدفعة التالية)؛ يُنشآن هنا مباشرة عبر SQL خام، تمامًا
كما فعلنا مع pct.categories قبل اكتمال عقد PCT.
"""

import os
import uuid

import pytest
import psycopg2
import psycopg2.extras
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api import router as search_router
from search_repository import PostgresSearchRepository
from pct_api import router as pct_router
from pct_repository import PostgresPctRepository
from vct_api import router as vct_router
from vct_repository import PostgresVctRepository
from cmp_api import router as cmp_router
from cmp_repository import PostgresCmpRepository
from auth_api import router as auth_router
from auth_repository import PostgresAuthRepository
from session_repository import PostgresSessionRepository
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
    app.include_router(pct_router)
    app.include_router(vct_router)
    app.include_router(cmp_router)
    app.include_router(search_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.aud_repository = PostgresAudRepository(conn)
    app.state.pct_repository = PostgresPctRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)
    app.state.cmp_repository = PostgresCmpRepository(conn)
    app.state.search_repository = PostgresSearchRepository(conn)
    client = TestClient(app, base_url="https://testserver")
    return app, client, conn


def _register_and_login(client, conn, email: str, role: str = "admin") -> str:
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


def _make_approved_part_with_name(client, conn) -> str:
    cur = conn.cursor()
    cur.execute("INSERT INTO pct.categories DEFAULT VALUES RETURNING id")
    category_id = cur.fetchone()["id"]
    part_id = client.post("/api/v1/pct/parts", json={"category_id": category_id}).json()["id"]
    client.post(f"/api/v1/pct/parts/{part_id}/approve")
    client.post(f"/api/v1/pct/parts/{part_id}/names", json={"name_value": "فلتر هواء", "name_kind": "canonical"})
    return part_id


def _make_valid_trim(client) -> str:
    m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
    client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
    model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
    gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]
    return client.post(f"/api/v1/vct/generations/{gen_id}/trims",
                        json={"fuel_type_ref_id": str(uuid.uuid4()), "transmission_type_ref_id": str(uuid.uuid4())}).json()["id"]


def _create_active_store_and_item(conn, user_id: str, part_id: str, price_amount=None) -> str:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO str.stores (owner_user_ref_id, status) VALUES (%s, 'active') RETURNING id",
        (user_id,),
    )
    store_id = cur.fetchone()["id"]
    pricing_mode = "fixed_price" if price_amount is not None else "contact_for_price"
    cur.execute(
        "INSERT INTO str.inventory_items (business_code, store_id, catalog_part_ref_id, condition_ref_id, "
        "pricing_mode, price_amount, price_currency, quantity, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 5, 'active') RETURNING id",
        (f"IT-{uuid.uuid4().hex[:12]}", store_id, part_id, str(uuid.uuid4()), pricing_mode, price_amount,
         "SAR" if price_amount is not None else None),
    )
    return cur.fetchone()["id"]


class TestSearchFindsRealInventoryOnLivePostgres:

    def test_search_returns_created_item(self, app_and_client):
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        _create_active_store_and_item(conn, user_id, part_id, price_amount=250.0)

        resp = client.get("/api/v1/search/parts")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] >= 1
        assert any(r["part_name"] == "فلتر هواء" for r in resp.json()["results"])

    def test_search_filters_by_trim_via_real_compatibility_record(self, app_and_client):
        """يثبت أن البحث يستخدم فعليًا cmp.compatibility_records الحقيقية،
        لا محاكاة — هذا هو الاختبار الأهم في هذه الدفعة."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        matching_trim = _make_valid_trim(client)
        other_trim = _make_valid_trim(client)
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)

        client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": matching_trim})

        matching_resp = client.get("/api/v1/search/parts", params={"trim_ref_id": matching_trim})
        assert matching_resp.json()["pagination"]["total_items"] == 1

        other_resp = client.get("/api/v1/search/parts", params={"trim_ref_id": other_trim})
        assert other_resp.json()["pagination"]["total_items"] == 0

    def test_unpriced_item_shows_fallback_price_text(self, app_and_client):
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        _create_active_store_and_item(conn, user_id, part_id, price_amount=None)

        resp = client.get("/api/v1/search/parts")
        matches = [r for r in resp.json()["results"] if r["price_amount"] is None]
        assert any(r["price_display_text"] == "تواصل مع البائع للسعر" for r in matches)


class TestCR020FreeTextSearchOnLivePostgres:
    """
    CR-020 v1 على اتصال حي: يثبت المطابقة الفعلية عبر الأسماء الأربعة
    (canonical/local/english/synonym) والتطبيع العربي — لا يكتشفه InMemory
    (الذي يطابق part_name المعروض فقط).
    """

    def test_matches_synonym_name_not_just_canonical(self, app_and_client):
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)  # canonical = "فلتر هواء"
        client.post(f"/api/v1/pct/parts/{part_id}/names",
                    json={"name_value": "مصفاة هواء", "name_kind": "synonym"})
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)

        resp = client.get("/api/v1/search/parts", params={"q": "مصفاة هواء"})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        # part_name المعروض يبقى canonical دومًا؛ q طابق المرادف فقط
        assert results[0]["part_name"] == "فلتر هواء"

    def test_matches_english_name_case_and_prefix(self, app_and_client):
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        client.post(f"/api/v1/pct/parts/{part_id}/names",
                    json={"name_value": "Air Filter", "name_kind": "english"})
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)

        resp = client.get("/api/v1/search/parts", params={"q": "air"})
        assert resp.json()["pagination"]["total_items"] == 1

    def test_matches_local_name_with_alef_normalization(self, app_and_client):
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        client.post(f"/api/v1/pct/parts/{part_id}/names",
                    json={"name_value": "إطار احتياطي", "name_kind": "local"})
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)

        resp = client.get("/api/v1/search/parts", params={"q": "اطار"})
        assert resp.json()["pagination"]["total_items"] == 1

    def test_q_combined_with_other_filters(self, app_and_client):
        """q يعمل بالتوازي مع trim_ref_id (يبقى AND منطقيًا، لا استبدال)."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)  # "فلتر هواء"
        matching_trim = _make_valid_trim(client)
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)
        client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": matching_trim})

        resp = client.get("/api/v1/search/parts", params={"q": "فلتر", "trim_ref_id": matching_trim})
        assert resp.json()["pagination"]["total_items"] == 1

        other_trim = _make_valid_trim(client)
        resp2 = client.get("/api/v1/search/parts", params={"q": "فلتر", "trim_ref_id": other_trim})
        assert resp2.json()["pagination"]["total_items"] == 0

    def test_no_query_string_still_returns_all(self, app_and_client):
        """Regression: تفعيل q لا يكسر السلوك القائم عند غيابه."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)

        resp = client.get("/api/v1/search/parts")
        assert resp.json()["pagination"]["total_items"] >= 1


class TestBatch1SearchVctCmpIntegrationOnLivePostgres:
    """
    Approved VCT Design Baseline §18-19: دمج Search الفعلي مع VCT/CMP
    (General/Year-specific)، لا البحث النصي وحده. يثبت أن Read Path الجديد
    في search_repository.py يتحقق فعليًا من cmp.compatibility_records
    وvct.trim_model_years المُحدَّثين (030/029)، لا استعلامًا قديمًا مهجورًا.
    """

    def test_general_compatibility_matches_regardless_of_year(self, app_and_client):
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        trim_id = _make_valid_trim(client)
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)
        client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})

        no_year = client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id})
        assert no_year.json()["pagination"]["total_items"] == 1

        with_year = client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id, "year": 2019})
        assert with_year.json()["pagination"]["total_items"] == 1

    def test_year_specific_matches_only_without_year_filter_or_matching_year(self, app_and_client):
        """§18: بلا سنة → Year-specific لأي سنة تحت نفس Trim مرشَّحة. بسنة أخرى → غير مرشَّحة."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        trim_id = _make_valid_trim(client)
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)
        tmy_2019 = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019}).json()["id"]
        client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_2019})

        no_year = client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id})
        assert no_year.json()["pagination"]["total_items"] == 1

        matching_year = client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id, "year": 2019})
        assert matching_year.json()["pagination"]["total_items"] == 1

        other_year = client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id, "year": 2021})
        assert other_year.json()["pagination"]["total_items"] == 0

    def test_camry_se_facelift_scenario_matches_design_baseline_example(self, app_and_client):
        """§21: مثال Camry SE 2018-2020 متوافق، 2021 غير متوافق — نفس مثال Approved VCT Design Baseline حرفيًا."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        trim_id = _make_valid_trim(client)
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)
        for year in (2018, 2019, 2020):
            tmy_id = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": year}).json()["id"]
            client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_id})
        client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2021})  # سنة موجودة بلا توافق مسجَّل لها

        for year in (2018, 2019, 2020):
            resp = client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id, "year": year})
            assert resp.json()["pagination"]["total_items"] == 1, f"سنة {year} يجب أن تكون متوافقة"

        resp_2021 = client.get("/api/v1/search/parts", params={"trim_ref_id": trim_id, "year": 2021})
        assert resp_2021.json()["pagination"]["total_items"] == 0

    def test_q_plus_trim_plus_condition_combined(self, app_and_client):
        """يثبت q + Trim + Condition معًا (AND منطقي بين الفلاتر الثلاثة)."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)  # "فلتر هواء"
        trim_id = _make_valid_trim(client)
        client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO str.stores (owner_user_ref_id, status) VALUES (%s, 'active') RETURNING id", (user_id,)
        )
        store_id = cur.fetchone()["id"]
        condition_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO str.inventory_items (business_code, store_id, catalog_part_ref_id, condition_ref_id, "
            "pricing_mode, price_amount, price_currency, quantity, status) "
            "VALUES (%s, %s, %s, %s, 'fixed_price', 100.0, 'SAR', 5, 'active')",
            (f"IT-{uuid.uuid4().hex[:12]}", store_id, part_id, condition_id),
        )

        matching = client.get("/api/v1/search/parts", params={
            "q": "فلتر", "trim_ref_id": trim_id, "condition_ref_id": condition_id,
        })
        assert matching.json()["pagination"]["total_items"] == 1

        wrong_condition = client.get("/api/v1/search/parts", params={
            "q": "فلتر", "trim_ref_id": trim_id, "condition_ref_id": str(uuid.uuid4()),
        })
        assert wrong_condition.json()["pagination"]["total_items"] == 0

    def test_pagination_after_full_filter_stack(self, app_and_client):
        """Pagination بعد الفلترة الكاملة (q + trim + year)، لا قبلها."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)
        trim_id = _make_valid_trim(client)
        client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        for _ in range(5):
            _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)
        # عنصر آخر لا يطابق q — يجب ألا يُحتسَب ضمن total_items
        other_part_id = _make_approved_part_with_name(client, conn)
        cur = conn.cursor()
        cur.execute(f"UPDATE pct.localized_names SET name_value = 'قطعة أخرى تمامًا' "
                    f"WHERE catalog_part_id = %s AND name_kind = 'canonical'", (other_part_id,))
        _create_active_store_and_item(conn, user_id, other_part_id, price_amount=50.0)

        resp = client.get("/api/v1/search/parts", params={"q": "فلتر", "trim_ref_id": trim_id, "page": 1, "page_size": 2})
        body = resp.json()
        assert len(body["results"]) == 2
        assert body["pagination"]["total_items"] == 5
        assert body["pagination"]["page"] == 1

    def test_dedup_when_multiple_name_kinds_match_same_part(self, app_and_client):
        """§: قطعة واحدة بأسماء متعددة تطابق q (canonical + synonym) — يجب ألا تتكرر في النتائج (لا JOIN مضاعِف)."""
        app, client, conn = app_and_client
        user_id = _register_and_login(client, conn, f"seller{uuid.uuid4().hex[:6]}@example.com")
        part_id = _make_approved_part_with_name(client, conn)  # canonical = "فلتر هواء"
        client.post(f"/api/v1/pct/parts/{part_id}/names", json={"name_value": "فلتر هواء المحرك", "name_kind": "synonym"})
        _create_active_store_and_item(conn, user_id, part_id, price_amount=100.0)

        resp = client.get("/api/v1/search/parts", params={"q": "فلتر هواء"})
        results = resp.json()["results"]
        item_ids = [r["inventory_item_id"] for r in results]
        assert len(item_ids) == len(set(item_ids)), "لا يجوز تكرار نفس عنصر المخزون رغم تطابق q مع أكثر من اسم"
