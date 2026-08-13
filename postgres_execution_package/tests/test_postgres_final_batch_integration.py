"""
test_postgres_final_batch_integration.py — اختبارات تكامل حقيقية للدفعة
الأخيرة (TRM + Scheduler + Reference Data) على PostgreSQL حي.
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا بعد.
"""

import io
import os
import uuid
from datetime import datetime, timedelta, timezone

import openpyxl
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
from trm_api import router as trm_router
from trm_repository import PostgresTrmRepository
from scheduler_api import router as scheduler_router
from scheduler_repository import PostgresSchedulerRepository
from ref_api import router as ref_router
from ref_repository import PostgresRefRepository
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
    app.include_router(vct_router)
    app.include_router(trm_router)
    app.include_router(scheduler_router)
    app.include_router(ref_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.store_repository = PostgresStoreRepository(conn)
    app.state.pct_repository = PostgresPctRepository(conn)
    app.state.order_repository = PostgresOrderRepository(conn)
    app.state.vct_repository = PostgresVctRepository(conn)  # Batch 1: order_api.create_purchase_request يعتمد عليه الآن
    app.state.trm_repository = PostgresTrmRepository(conn)
    app.state.scheduler_repository = PostgresSchedulerRepository(conn)
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
    assert client.post(f"/api/v1/pct/parts/{part_id}/approve").status_code == 200
    client.post("/api/v1/auth/logout")
    return part_id


def _make_valid_trim(client, conn) -> str:
    """Batch 1: فئة VCT حقيقية وصالحة (self-contained، بنفس نمط _make_approved_part —
    جلسة admin داخلية مستقلة، بلا تأثير على جلسة المُستدعي المحيطة)."""
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


class TestTrmRatingOnLivePostgres:

    def test_eligible_buyer_rates_after_fulfillment(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)

        buyer_email, buyer_password = f"buyer-{uuid.uuid4().hex[:8]}@example.com", "Str0ngPass1!"
        _register_and_login(client, conn, buyer_email)
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]

        client.post("/api/v1/auth/logout")
        _register_and_login(client, conn, f"seller-{uuid.uuid4().hex[:8]}@example.com")
        client.post("/api/v1/store/stores", json={})
        offer_id = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                json={"amount": 150.0, "currency": "SAR", "provides_shipping": False}).json()["id"]

        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"login_identifier": buyer_email, "password": buyer_password})
        assert client.post(f"/api/v1/offers/{offer_id}/accept").status_code == 200

        rating_resp = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": str(uuid.uuid4()),
            "source_purchase_request_ref_id": pr_id, "score": 5,
        })
        assert rating_resp.status_code == 201, rating_resp.text

        cur = conn.cursor()
        cur.execute("SELECT score FROM trm.ratings WHERE id = %s", (rating_resp.json()["id"],))
        assert cur.fetchone()["score"] == 5

    def test_unfulfilled_pr_rating_rejected_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        part_id = _make_approved_part(client, conn)
        trim_id = _make_valid_trim(client, conn)
        _register_and_login(client, conn, f"buyer2-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]
        # لم يُقبَل أي عرض بعد — الطلب لا يزال open

        resp = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": str(uuid.uuid4()),
            "source_purchase_request_ref_id": pr_id, "score": 5,
        })
        assert resp.status_code == 403


class TestSchedulerOnLivePostgres:

    def test_admin_creates_and_cancels_job(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"admin-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        create_resp = client.post("/api/v1/admin/scheduled-jobs", json={
            "job_type": "pur_expiration_check", "target_ref_id": str(uuid.uuid4()), "scheduled_at": scheduled_at,
        })
        assert create_resp.status_code == 201
        job_id = create_resp.json()["id"]

        cancel_resp = client.post(f"/api/v1/admin/scheduled-jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        cur = conn.cursor()
        cur.execute("SELECT status FROM sys.scheduled_jobs WHERE id = %s", (job_id,))
        assert cur.fetchone()["status"] == "cancelled"

    def test_regular_user_forbidden_on_live_postgres(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer3-{uuid.uuid4().hex[:8]}@example.com")
        resp = client.post("/api/v1/admin/scheduled-jobs", json={
            "job_type": "x", "target_ref_id": "y", "scheduled_at": datetime.now(timezone.utc).isoformat(),
        })
        assert resp.status_code == 403


class TestReferenceDataOnLivePostgres:

    def test_admin_creates_value_and_real_bulk_import(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"refadmin-{uuid.uuid4().hex[:8]}@example.com", role="admin")

        unique_code = f"XX{uuid.uuid4().hex[:6]}"
        create_resp = client.post("/api/v1/reference-data", json={"ref_type": "language", "code": unique_code})
        assert create_resp.status_code == 201

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["code"])
        ws.append([unique_code])  # موجودة بالفعل -> updated
        new_code = f"YY{uuid.uuid4().hex[:6]}"
        ws.append([new_code])  # جديدة -> new
        buf = io.BytesIO()
        wb.save(buf)

        preview_resp = client.post(
            "/api/v1/reference-data/language/bulk-import/preview",
            files={"file": ("langs.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert preview_resp.status_code == 200, preview_resp.text
        body = preview_resp.json()
        assert body["updated_count"] == 1
        assert body["new_count"] == 1

        cur = conn.cursor()
        cur.execute("SELECT status FROM ref.bulk_import_jobs WHERE id = %s", (body["job_id"],))
        assert cur.fetchone()["status"] == "preview_ready"
        cur.execute("SELECT count(*) AS c FROM ref.bulk_import_job_rows WHERE job_id = %s", (body["job_id"],))
        assert cur.fetchone()["c"] == 2

    def test_regular_user_cannot_create_ref_value(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer4-{uuid.uuid4().hex[:8]}@example.com")
        resp = client.post("/api/v1/reference-data", json={"ref_type": "country", "code": "ZZ"})
        assert resp.status_code == 403
