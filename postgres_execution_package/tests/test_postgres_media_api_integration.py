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


# ===========================================================================
# Batch 2 — Unit 2: Ownership الحقيقي + Signed/Public Access + Watermark —
# على PostgreSQL حي. Fixture مستقلة تمامًا عن app_and_client الأصلية (صفر
# تعديل عليها — صفر مخاطرة Regression على اختبارات Unit 1 الستة الناجحة
# فعليًا على GitHub، Run 31797308015).
# ===========================================================================

from order_repository import PostgresOrderRepository
from order_service import PurchaseRequest, Offer
from store_repository import PostgresStoreRepository
from store_service import Store
from inventory_item_repository import PostgresInventoryItemRepository
from inventory_item_service import InventoryItem
from cnt_repository import PostgresCntRepository
from media_authorization import build_media_ownership_checker, build_media_view_authorization_checker, build_public_visibility_checker


@pytest.fixture
def unit2_app_and_client(conn, tmp_path):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(media_router)
    app.state.auth_repository = PostgresAuthRepository(conn)
    app.state.session_repository = PostgresSessionRepository(conn)
    app.state.media_repository = PostgresMediaRepository(conn)
    app.state.storage_adapter = LocalStorageAdapter(base_dir=str(tmp_path / "media-test-storage-u2"))

    order_repo = PostgresOrderRepository(conn)
    store_repo = PostgresStoreRepository(conn)
    inventory_repo = PostgresInventoryItemRepository(conn)
    cnt_repo = PostgresCntRepository(conn)
    app.state.order_repository = order_repo
    app.state.store_repository = store_repo
    app.state.inventory_item_repository = inventory_repo
    app.state.cnt_repository = cnt_repo
    app.state.media_ownership_checker = build_media_ownership_checker(
        order_repo, store_repo, inventory_repo, auth_repo=app.state.auth_repository
    )
    app.state.media_view_authorization_checker = build_media_view_authorization_checker(order_repo, store_repo)
    app.state.media_public_visibility_checker = build_public_visibility_checker(cnt_repo)

    client = TestClient(app, base_url="https://testserver")
    return app, client, conn


def _insert_purchase_request(conn, buyer_user_ref_id: str) -> str:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pur.purchase_requests (business_code, buyer_user_ref_id, catalog_part_ref_id, trim_ref_id) "
        "VALUES (%s, %s, gen_random_uuid(), gen_random_uuid()) RETURNING id",
        (f"PR-{uuid.uuid4().hex[:20]}", buyer_user_ref_id),
    )
    return cur.fetchone()["id"]


def _insert_store(conn, owner_user_ref_id: str) -> str:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO str.stores (owner_user_ref_id, status) VALUES (%s, 'active') RETURNING id",
        (owner_user_ref_id,),
    )
    return cur.fetchone()["id"]


def _insert_offer(conn, pr_id: str, seller_store_ref_id: str) -> str:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pur.offers (business_code, purchase_request_id, seller_store_ref_id, amount, currency, provides_shipping) "
        "VALUES (%s, %s, %s, 100.0, 'SAR', false) RETURNING id",
        (f"OFR-{uuid.uuid4().hex[:20]}", pr_id, seller_store_ref_id),
    )
    return cur.fetchone()["id"]


def _insert_inventory_item(conn, store_id: str) -> str:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ref.ref_values (ref_type, code) VALUES ('part_condition', %s) RETURNING id",
        (f"new-{uuid.uuid4().hex[:8]}",),
    )
    condition_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO str.inventory_items (business_code, store_id, catalog_part_ref_id, condition_ref_id, "
        "pricing_mode, price_amount, price_currency, quantity, status) "
        "VALUES (%s, %s, gen_random_uuid(), %s, 'fixed_price', 100.0, 'SAR', 5, 'active') RETURNING id",
        (f"IT-{uuid.uuid4().hex[:12]}", store_id, condition_id),
    )
    return cur.fetchone()["id"]


class TestUnit2RealOwnershipOnLivePostgres:
    """§7: Ownership حقيقي عبر Repositories فعلية على PostgreSQL حي."""

    def test_buyer_can_bind_to_own_purchase_request(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        buyer_id = _register_and_login(client, conn, f"buyer-u2pg-1-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, buyer_id)
        conn.commit()  # درس Batch 1: تثبيت الإعداد قبل أي عملية تعتمد عليه
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        })
        assert resp.status_code == 201, resp.text

    def test_non_buyer_cannot_bind_to_others_purchase_request(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        real_buyer_id = _register_and_login(client, conn, f"real-buyer-u2pg-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, real_buyer_id)
        client.post("/api/v1/auth/logout")
        conn.commit()

        _register_and_login(client, conn, f"impersonator-u2pg-{uuid.uuid4().hex[:8]}@example.com")
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        })
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "BINDING_FORBIDDEN"

    def test_seller_can_bind_to_own_offer_via_store_ownership(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        seller_id = _register_and_login(client, conn, f"seller-u2pg-1-{uuid.uuid4().hex[:8]}@example.com")
        store_id = _insert_store(conn, seller_id)
        pr_id = _insert_purchase_request(conn, str(uuid.uuid4()))
        offer_id = _insert_offer(conn, pr_id, store_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "offer", "owner_ref_id": offer_id,
        })
        assert resp.status_code == 201, resp.text

    def test_seller_can_bind_to_own_inventory_item(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        seller_id = _register_and_login(client, conn, f"seller-u2pg-2-{uuid.uuid4().hex[:8]}@example.com")
        store_id = _insert_store(conn, seller_id)
        item_id = _insert_inventory_item(conn, store_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "inventory_item", "owner_ref_id": item_id,
        })
        assert resp.status_code == 201, resp.text


class TestUnit2ArchiveAuthorizationOnLivePostgres:
    """البند 4 من الطلب: إثبات صريح أن المالك الصحيح فقط يستطيع الأرشفة، وغير المخوَّل يُرفَض."""

    def test_owner_can_archive_own_purchase_request_attachment(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        buyer_id = _register_and_login(client, conn, f"buyer-arc-1-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, buyer_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        }).json()["id"]

        resp = client.post(f"/api/v1/media/attachments/{att_id}/archive")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "archived"

    def test_non_owner_cannot_archive_purchase_request_attachment(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        buyer_id = _register_and_login(client, conn, f"buyer-arc-2-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, buyer_id)
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        }).json()["id"]
        client.post("/api/v1/auth/logout")
        conn.commit()

        _register_and_login(client, conn, f"stranger-arc-2-{uuid.uuid4().hex[:8]}@example.com")
        resp = client.post(f"/api/v1/media/attachments/{att_id}/archive")
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "ARCHIVE_FORBIDDEN"

    def test_owner_can_archive_own_inventory_item_attachment(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        seller_id = _register_and_login(client, conn, f"seller-arc-3-{uuid.uuid4().hex[:8]}@example.com")
        store_id = _insert_store(conn, seller_id)
        item_id = _insert_inventory_item(conn, store_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "inventory_item", "owner_ref_id": item_id,
        }).json()["id"]

        resp = client.post(f"/api/v1/media/attachments/{att_id}/archive")
        assert resp.status_code == 200


class TestUnit2WatermarkOnLivePostgres:
    """§9: Watermark فعلي محفوظ على القرص عبر LocalStorageAdapter الحقيقي — لا InMemory."""

    def test_inventory_item_bind_persists_watermarked_display_on_disk(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        seller_id = _register_and_login(client, conn, f"seller-wmpg-1-{uuid.uuid4().hex[:8]}@example.com")
        store_id = _insert_store(conn, seller_id)
        item_id = _insert_inventory_item(conn, store_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(1000, 800), "image/jpeg")}).json()["id"]
        asset_before = app.state.media_repository.get_asset_by_id(asset_id)
        display_before = app.state.storage_adapter.read(asset_before.storage_key_display)

        client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "inventory_item", "owner_ref_id": item_id,
        })

        asset_after = app.state.media_repository.get_asset_by_id(asset_id)
        display_after = app.state.storage_adapter.read(asset_after.storage_key_display)
        assert display_after != display_before, "الملف الفعلي على القرص يجب أن يتغيَّر بعد Watermark"

        cur = conn.cursor()
        cur.execute("SELECT checksum, storage_key FROM media.assets WHERE id = %s", (asset_id,))
        row = cur.fetchone()
        assert row["storage_key"] is not None, "Master storage_key يجب ألا يتأثر إطلاقًا بتحديث Display/Thumbnail"

    def test_purchase_request_bind_leaves_display_unwatermarked_on_disk(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        buyer_id = _register_and_login(client, conn, f"buyer-wmpg-1-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, buyer_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        asset_before = app.state.media_repository.get_asset_by_id(asset_id)
        display_before = app.state.storage_adapter.read(asset_before.storage_key_display)

        client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        })

        asset_after = app.state.media_repository.get_asset_by_id(asset_id)
        display_after = app.state.storage_adapter.read(asset_after.storage_key_display)
        assert display_after == display_before, "PR: لا Watermark — الملف على القرص يبقى مطابقًا حرفيًا"


class TestUnit2AccessPolicyOnLivePostgres:
    """§9-10: مطابقة حرفية لسياسة الوصول — Public/Watermark لـInventory، Private/no-watermark لـPR/Offer، مصفوفة §10 الكاملة."""

    def test_inventory_item_access_public_and_watermarked_flag(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        seller_id = _register_and_login(client, conn, f"seller-accpg-1-{uuid.uuid4().hex[:8]}@example.com")
        store_id = _insert_store(conn, seller_id)
        item_id = _insert_inventory_item(conn, store_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "inventory_item", "owner_ref_id": item_id,
        }).json()["id"]
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"any-viewer-accpg-1-{uuid.uuid4().hex[:8]}@example.com")
        resp = client.get(f"/api/v1/media/attachments/{att_id}/access")
        assert resp.status_code == 200
        body = resp.json()
        assert body["public"] is True
        assert body["watermarked"] is True

    def test_purchase_request_access_private_not_watermarked(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        buyer_id = _register_and_login(client, conn, f"buyer-accpg-2-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, buyer_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        }).json()["id"]

        resp = client.get(f"/api/v1/media/attachments/{att_id}/access")
        assert resp.status_code == 200
        body = resp.json()
        assert body["public"] is False
        assert body["watermarked"] is False

    def test_offer_access_private_not_watermarked(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        seller_id = _register_and_login(client, conn, f"seller-accpg-3-{uuid.uuid4().hex[:8]}@example.com")
        store_id = _insert_store(conn, seller_id)
        pr_id = _insert_purchase_request(conn, str(uuid.uuid4()))
        offer_id = _insert_offer(conn, pr_id, store_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "offer", "owner_ref_id": offer_id,
        }).json()["id"]

        resp = client.get(f"/api/v1/media/attachments/{att_id}/access")
        assert resp.status_code == 200
        body = resp.json()
        assert body["public"] is False
        assert body["watermarked"] is False

    def test_seller_without_active_offer_cannot_access_pr_images(self, unit2_app_and_client):
        """البند 5: Seller لا يحصل على PR Signed Access لمجرد كونه Seller؛ يلزم Offer فعلي على نفس PR تحديدًا."""
        app, client, conn = unit2_app_and_client
        buyer_id = _register_and_login(client, conn, f"buyer-accpg-4-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, buyer_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        }).json()["id"]
        client.post("/api/v1/auth/logout")

        # بائع حقيقي (متجر حقيقي) لكن بلا أي Offer على هذا الـPR تحديدًا
        seller_id = _register_and_login(client, conn, f"seller-no-offer-accpg-4-{uuid.uuid4().hex[:8]}@example.com")
        _insert_store(conn, seller_id)
        conn.commit()

        resp = client.get(f"/api/v1/media/attachments/{att_id}/access")
        assert resp.status_code == 403, "مجرد كونه بائعًا حقيقيًا بلا Offer فعلي على هذا الطلب لا يكفي (§10)"

    def test_seller_with_active_offer_can_access_pr_images(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        buyer_id = _register_and_login(client, conn, f"buyer-accpg-5-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, buyer_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        }).json()["id"]
        client.post("/api/v1/auth/logout")

        seller_id = _register_and_login(client, conn, f"seller-with-offer-accpg-5-{uuid.uuid4().hex[:8]}@example.com")
        store_id = _insert_store(conn, seller_id)
        _insert_offer(conn, pr_id, store_id)
        conn.commit()

        resp = client.get(f"/api/v1/media/attachments/{att_id}/access")
        assert resp.status_code == 200, "بائع لديه Offer فعلي على هذا الطلب تحديدًا يجب أن يصل"

    def test_offer_access_depends_on_real_seller_store_ownership(self, unit2_app_and_client):
        """البند 5: Offer access يعتمد على ملكية seller_store_ref_id الفعلية، لا مجرد الدور."""
        app, client, conn = unit2_app_and_client
        real_seller_id = _register_and_login(client, conn, f"real-seller-accpg-6-{uuid.uuid4().hex[:8]}@example.com")
        store_id = _insert_store(conn, real_seller_id)
        pr_id = _insert_purchase_request(conn, str(uuid.uuid4()))
        offer_id = _insert_offer(conn, pr_id, store_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "offer", "owner_ref_id": offer_id,
        }).json()["id"]
        client.post("/api/v1/auth/logout")

        # بائع آخر حقيقي، بمتجر مختلف تمامًا — لا علاقة له بهذا العرض
        other_seller_id = _register_and_login(client, conn, f"other-seller-accpg-6-{uuid.uuid4().hex[:8]}@example.com")
        _insert_store(conn, other_seller_id)
        conn.commit()

        resp = client.get(f"/api/v1/media/attachments/{att_id}/access")
        assert resp.status_code == 403, "بائع آخر لا يملك seller_store_ref_id هذا العرض تحديدًا يُرفَض"

    def test_admin_bypasses_all_private_restrictions(self, unit2_app_and_client):
        app, client, conn = unit2_app_and_client
        buyer_id = _register_and_login(client, conn, f"buyer-accpg-7-{uuid.uuid4().hex[:8]}@example.com")
        pr_id = _insert_purchase_request(conn, buyer_id)
        conn.commit()
        asset_id = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()["id"]
        att_id = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr_id,
        }).json()["id"]
        client.post("/api/v1/auth/logout")

        _register_and_login(client, conn, f"admin-accpg-7-{uuid.uuid4().hex[:8]}@example.com", role="admin")
        resp = client.get(f"/api/v1/media/attachments/{att_id}/access")
        assert resp.status_code == 200, "Admin يجب أن يتجاوز كل قيود Private"
