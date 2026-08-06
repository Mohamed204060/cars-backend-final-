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
