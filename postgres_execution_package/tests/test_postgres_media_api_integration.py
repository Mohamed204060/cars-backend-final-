"""
test_postgres_media_api_integration.py — اختبارات تكامل حقيقية لخدمة Media
Foundation على PostgreSQL حي (Batch 2 — Unit 1)
=====================================================================
الحالة: Ready for PostgreSQL Execution — لم يُشغَّل أي اختبار هنا فعليًا
بعد؛ لا اتصال شبكة أو محرك PostgreSQL متاح في بيئة إعداد هذه الحزمة.

درس مُطبَّق من Batch 1 (Transaction Visibility، انظر
test_postgres_vct_api_integration.py): أي بيانات إعداد تُنشَأ عبر `conn`
المشترَكة يجب تثبيتها صراحةً (conn.commit()) قبل فتح أي اتصال منفصل
(conn_a/conn_b) يحتاج رؤيتها — سلوك PostgreSQL القياسي (READ COMMITTED)،
لا افتراضًا. مُطبَّق من أول سطر هنا، لا مُكتشَفًا لاحقًا بعد فشل CI.
"""

import io
import os
import threading
import uuid

import pytest
import psycopg2
import psycopg2.extras
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from auth_api import router as auth_router
from auth_repository import PostgresAuthRepository
from session_repository import PostgresSessionRepository
from media_api import router as media_router
from media_repository import PostgresMediaRepository
from storage import LocalStorageAdapter
from credential_service import hash_password


DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/carparts_test")


@pytest.fixture
def conn():
    connection = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    yield connection
    connection.rollback()
    connection.close()


@pytest.fixture
def app_and_client(conn, tmp_path):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(media_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.media_repository = PostgresMediaRepository(conn)
    app.state.storage_adapter = LocalStorageAdapter(base_dir=str(tmp_path / "media-test-storage"))
    app.state.media_ownership_checker = lambda owner_type, owner_ref_id, uploader: True  # صلاحية دائمة True في هذه الحزمة (Unit 1 فقط — Unit 2 يستبدلها بفحص حقيقي)
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


def _jpeg_bytes(w=800, h=600) -> bytes:
    img = Image.new("RGB", (w, h), color=(50, 100, 150))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class TestAssetLifecycleOnLivePostgres:

    def test_upload_persists_correctly_in_media_assets(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"up1-{uuid.uuid4().hex[:8]}@example.com")

        resp = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(1200, 900), "image/jpeg")})
        assert resp.status_code == 201, resp.text
        asset_id = resp.json()["id"]

        cur = conn.cursor()
        cur.execute(
            "SELECT status, mime_type, width, height, storage_key, checksum "
            "FROM media.assets WHERE id = %s", (asset_id,),
        )
        row = cur.fetchone()
        assert row["status"] == "ready"
        assert row["mime_type"] == "image/jpeg"
        assert row["width"] == 1200 and row["height"] == 900
        assert row["storage_key"] is not None
        assert len(row["checksum"]) == 64

    def test_status_check_constraint_enforced_by_db(self, app_and_client):
        """§3: chk_media_assets_status يجب أن يرفض حالة غير معروفة على مستوى DB نفسه، لا الكود فقط."""
        app, client, conn = app_and_client
        cur = conn.cursor()
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO media.assets (original_file_name, uploaded_by_user_ref_id, status) "
                "VALUES ('x.jpg', gen_random_uuid(), 'not_a_real_status')"
            )


class TestAttachmentBindingOnLivePostgres:

    def test_bind_persists_and_unique_asset_constraint_enforced(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer1-{uuid.uuid4().hex[:8]}@example.com")
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": str(uuid.uuid4()),
        })
        assert resp.status_code == 201, resp.text

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM media.attachments WHERE asset_ref_id = %s", (asset_id,))
        assert cur.fetchone()["c"] == 1

        # §5: UNIQUE(asset_ref_id) — محاولة ربط نفس الـAsset لهدف آخر يجب أن ترتطم بقيد DB
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO media.attachments (asset_ref_id, owner_type, owner_ref_id, sort_order) "
                "VALUES (%s, 'offer', gen_random_uuid(), 0)", (asset_id,),
            )

    def test_asset_ref_id_fk_restrict_enforced(self, app_and_client):
        """§5: asset_ref_id FK حقيقي ON DELETE RESTRICT — حذف Asset مربوط فعليًا يجب أن يُرفَض من DB."""
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer2-{uuid.uuid4().hex[:8]}@example.com")
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": str(uuid.uuid4()),
        })

        cur = conn.cursor()
        with pytest.raises(Exception):
            cur.execute("DELETE FROM media.assets WHERE id = %s", (asset_id,))

    def test_owner_type_check_constraint_enforced_by_db(self, app_and_client):
        app, client, conn = app_and_client
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO media.assets (original_file_name, uploaded_by_user_ref_id, status) "
            "VALUES ('x.jpg', gen_random_uuid(), 'ready') RETURNING id"
        )
        asset_id = cur.fetchone()["id"]
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO media.attachments (asset_ref_id, owner_type, owner_ref_id, sort_order) "
                "VALUES (%s, 'not_a_real_owner_type', gen_random_uuid(), 0)", (asset_id,),
            )


class TestFiveImageLimitConcurrencyOnLivePostgres:
    """
    §6/§15: يثبت أن Advisory Transaction Lock (namespace='media-binding')
    يمنع تجاوز حد 5 صور فعليًا تحت تزامن حقيقي — اتصالان منفصلان تمامًا،
    لا Thread وهمي على نفس الاتصال. نفس منهجية Concurrency المعتمَدة في
    Batch 1 (VCT/CMP) حرفيًا، شاملة درس Transaction Visibility.
    """

    def test_concurrent_sixth_and_seventh_bind_attempts_exactly_one_may_fill_last_slot(self, app_and_client):
        app, client, conn = app_and_client
        _register_and_login(client, conn, f"buyer3-{uuid.uuid4().hex[:8]}@example.com")
        owner_ref_id = str(uuid.uuid4())

        # 4 صور أولى بالتسلسل العادي (بلا تزامن) — تترك فتحة واحدة أخيرة (الخامسة)
        for _ in range(4):
            asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
            resp = client.post("/api/v1/media/attachments", json={
                "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": owner_ref_id,
            })
            assert resp.status_code == 201

        asset_a_id = client.post("/api/v1/media/assets", files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        asset_b_id = client.post("/api/v1/media/assets", files={"file": ("b.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]

        # تثبيت صريح لكل بيانات الإعداد (4 مرفقات + Asset-هما الخامس/السادس المتنافسان)
        # على conn قبل فتح أي اتصال منفصل — درس Transaction Visibility من Batch 1.
        conn.commit()

        conn_a = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn_b = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        repo_a = PostgresMediaRepository(conn_a)
        repo_b = PostgresMediaRepository(conn_b)

        from media_service import Asset, AttachmentLimitExceededError

        asset_a = repo_a.get_asset_by_id(asset_a_id)
        asset_b = repo_b.get_asset_by_id(asset_b_id)

        results = {}
        start_barrier = threading.Barrier(2)
        always_true_checker = lambda ot, oid, uploader: True

        def bind_a():
            start_barrier.wait()
            try:
                repo_a.insert_attachment_with_lock(asset_a, "purchase_request", owner_ref_id, always_true_checker)
                results["a"] = ("success", None)
            except AttachmentLimitExceededError as e:
                results["a"] = ("limit_exceeded", str(e))
            except Exception as e:  # noqa: BLE001
                results["a"] = (f"unexpected:{type(e).__name__}", str(e))

        def bind_b():
            start_barrier.wait()
            try:
                repo_b.insert_attachment_with_lock(asset_b, "purchase_request", owner_ref_id, always_true_checker)
                results["b"] = ("success", None)
            except AttachmentLimitExceededError as e:
                results["b"] = ("limit_exceeded", str(e))
            except Exception as e:  # noqa: BLE001
                results["b"] = (f"unexpected:{type(e).__name__}", str(e))

        thread_a = threading.Thread(target=bind_a)
        thread_b = threading.Thread(target=bind_b)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        assert not thread_a.is_alive(), "thread_a لم ينتهِ خلال المهلة — احتمال Deadlock حقيقي على القفل."
        assert not thread_b.is_alive(), "thread_b لم ينتهِ خلال المهلة — احتمال Deadlock حقيقي على القفل."
        assert "a" in results and "b" in results, f"نتائج ناقصة: {results}"

        a_outcome, a_detail = results["a"]
        b_outcome, b_detail = results["b"]
        assert a_outcome in ("success", "limit_exceeded"), f"a: استثناء غير متوقَّع {a_outcome} — {a_detail}"
        assert b_outcome in ("success", "limit_exceeded"), f"b: استثناء غير متوقَّع {b_outcome} — {b_detail}"

        outcomes = sorted([a_outcome, b_outcome])
        assert outcomes == ["limit_exceeded", "success"], (
            f"يجب أن ينجح واحد بالضبط (يملأ الفتحة الخامسة) ويُرفَض الآخر بتجاوز الحد، "
            f"لا الاثنان معًا ولا كلاهما فشل: a={a_outcome} ({a_detail}), b={b_outcome} ({b_detail})"
        )

        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM media.attachments WHERE owner_type = 'purchase_request' "
            "AND owner_ref_id = %s AND status = 'active'", (owner_ref_id,),
        )
        assert cur.fetchone()["c"] == 5, "يجب أن يستقر العدد النهائي على 5 بالضبط، لا 4 ولا 6"

        conn_a.close()
        conn_b.close()
