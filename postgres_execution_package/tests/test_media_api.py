"""
test_media_api.py — اختبارات وحدة لخدمة Media Foundation (InMemory، لا PostgreSQL)
المرجع: CarsMaint Media Foundation — Approved Baseline v1.0

يستخدم Pillow فعليًا لتوليد صور اختبار حقيقية (لا Placeholders نصية) —
يمرّ الرفع فعليًا عبر خط المعالجة الكامل (process_image) في كل اختبار.
"""

import io
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from media_api import router as media_router
from media_repository import InMemoryMediaRepository
from storage import InMemoryStorageAdapter


def _make_jpeg_bytes(width=800, height=600, color=(120, 60, 30)) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(media_router)
    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.media_repository = InMemoryMediaRepository()
    app.state.storage_adapter = InMemoryStorageAdapter()
    # افتراضي: يرفض كل ربط (Fail-closed) ما لم يُستبدَل صراحةً داخل الاختبار
    app.state.media_ownership_checker = lambda owner_type, owner_ref_id, uploader: False
    client = TestClient(app, base_url="https://testserver")
    return app, client


def _login_as(app, client, email: str, role: str = "individual_buyer") -> str:
    repo = app.state.auth_repository
    user_id = repo.create_user()
    repo.set_user_role(user_id, role)
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password="Str0ngPass1!")
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    assert resp.status_code == 200
    return user_id


class TestAssetUpload:
    """§4/§12: رفع ومعالجة فعلية عبر Pillow حقيقي."""

    def test_valid_jpeg_upload_succeeds_and_reaches_ready(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "uploader1@example.com")

        resp = client.post(
            "/api/v1/media/assets",
            files={"file": ("photo.jpg", _make_jpeg_bytes(2000, 1500), "image/jpeg")},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "ready"
        assert body["mime_type"] == "image/jpeg"
        assert body["width"] == 2000
        assert body["height"] == 1500

        # تحقق فعلي: 3 نسخ (master/display/thumbnail) مكتوبة فعليًا في StorageAdapter
        asset = app.state.media_repository.get_asset_by_id(body["id"])
        assert app.state.storage_adapter.exists(asset.storage_key)
        assert app.state.storage_adapter.exists(asset.storage_key_display)
        assert app.state.storage_adapter.exists(asset.storage_key_thumbnail)

    def test_upload_exceeding_size_limit_rejected_and_marked_failed(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "uploader2@example.com")
        # نبني ملفًا يتجاوز 10MB فعليًا (بيانات JPEG حقيقية كافية الحجم)
        huge = _make_jpeg_bytes(8000, 8000, color=(1, 2, 3))
        # قد لا يتجاوز JPEG المضغوط 10MB بسهولة بلون واحد مسطَّح؛ نضمن التجاوز
        # عبر ضجيج عشوائي يقاوم الضغط بدل الاعتماد على أبعاد وحدها
        import random
        noisy_img = Image.frombytes("RGB", (3000, 3000), bytes(random.randint(0, 255) for _ in range(3000 * 3000 * 3)))
        buf = io.BytesIO()
        noisy_img.save(buf, format="JPEG", quality=100)
        huge = buf.getvalue()
        if len(huge) <= 10 * 1024 * 1024:
            pytest.skip("تعذَّر توليد بيانات اختبار حقيقية تتجاوز 10MB في هذه البيئة")

        resp = client.post("/api/v1/media/assets", files={"file": ("big.jpg", huge, "image/jpeg")})
        assert resp.status_code == 413
        assert resp.json()["detail"]["error_code"] == "UPLOAD_TOO_LARGE"

    def test_non_image_upload_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "uploader3@example.com")
        resp = client.post(
            "/api/v1/media/assets",
            files={"file": ("notes.txt", b"this is definitely not an image file at all", "text/plain")},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error_code"] == "IMAGE_PROCESSING_FAILED"

    def test_dimensions_exceeding_8000px_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "uploader4@example.com")
        oversized = _make_jpeg_bytes(9000, 50)
        resp = client.post("/api/v1/media/assets", files={"file": ("wide.jpg", oversized, "image/jpeg")})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error_code"] == "IMAGE_PROCESSING_FAILED"

    def test_get_asset_by_id(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "uploader5@example.com")
        created = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _make_jpeg_bytes(), "image/jpeg")}).json()

        resp = client.get(f"/api/v1/media/assets/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_nonexistent_asset_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "uploader6@example.com")
        resp = client.get("/api/v1/media/assets/ghost-asset")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "ASSET_NOT_FOUND"


class TestAttachmentBinding:
    """§6/§7/§15: الربط، الحد، التفويض — عبر HTTP الكامل، بمُحقِّق تفويض مُستبدَل صراحةً لكل سيناريو."""

    def _upload_ready_asset(self, client) -> str:
        resp = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _make_jpeg_bytes(), "image/jpeg")})
        return resp.json()["id"]

    def test_bind_succeeds_with_permissive_checker(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer1@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True

        asset_id = self._upload_ready_asset(client)
        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": "pr-1",
        })
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["owner_type"] == "purchase_request"
        assert body["sort_order"] == 0
        assert body["status"] == "active"

    def test_bind_rejected_by_default_fail_closed_checker(self, app_and_client):
        """التأكد أن الافتراض الحقيقي (بلا استبدال) هو Fail-closed، لا نجاح صامت."""
        app, client = app_and_client
        _login_as(app, client, "buyer2@example.com")
        asset_id = self._upload_ready_asset(client)

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": "pr-2",
        })
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "BINDING_FORBIDDEN"

    def test_sixth_image_rejected_for_purchase_request(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer3@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True

        for i in range(5):
            asset_id = self._upload_ready_asset(client)
            resp = client.post("/api/v1/media/attachments", json={
                "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": "pr-limit-test",
            })
            assert resp.status_code == 201, f"الصورة {i+1} كان يجب أن تنجح"

        sixth_asset = self._upload_ready_asset(client)
        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": sixth_asset, "owner_type": "purchase_request", "owner_ref_id": "pr-limit-test",
        })
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "ATTACHMENT_LIMIT_EXCEEDED"

    def test_sixth_image_rejected_for_offer(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller1@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True

        for i in range(5):
            asset_id = self._upload_ready_asset(client)
            resp = client.post("/api/v1/media/attachments", json={
                "asset_ref_id": asset_id, "owner_type": "offer", "owner_ref_id": "offer-limit-test",
            })
            assert resp.status_code == 201

        sixth_asset = self._upload_ready_asset(client)
        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": sixth_asset, "owner_type": "offer", "owner_ref_id": "offer-limit-test",
        })
        assert resp.status_code == 409

    def test_inventory_item_has_no_hard_limit(self, app_and_client):
        """§6: Inventory Item — لا حد جديد معتمَد؛ 8 صور يجب أن تنجح كلها."""
        app, client = app_and_client
        _login_as(app, client, "seller2@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True

        for i in range(8):
            asset_id = self._upload_ready_asset(client)
            resp = client.post("/api/v1/media/attachments", json={
                "asset_ref_id": asset_id, "owner_type": "inventory_item", "owner_ref_id": "inv-no-limit",
            })
            assert resp.status_code == 201, f"الصورة {i+1} كان يجب أن تنجح (لا حد لـinventory_item)"

    def test_bind_before_ready_rejected(self, app_and_client):
        """§7/§14: لا Binding قبل status == ready — نحاكي أصلًا بحالة pending مباشرة عبر Repository."""
        app, client = app_and_client
        _login_as(app, client, "buyer4@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True

        from media_service import Asset
        pending_asset = app.state.media_repository.insert_asset(
            Asset(id="", original_file_name="x.jpg", uploaded_by_user_ref_id="someone", status="pending")
        )
        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": pending_asset.id, "owner_type": "purchase_request", "owner_ref_id": "pr-not-ready",
        })
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "ASSET_NOT_READY"

    def test_invalid_owner_type_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer5@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True
        asset_id = self._upload_ready_asset(client)

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "totally_invalid_type", "owner_ref_id": "x",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_OWNER_TYPE"

    def test_bind_nonexistent_asset_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer6@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": "ghost-asset", "owner_type": "purchase_request", "owner_ref_id": "pr-x",
        })
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "ASSET_NOT_FOUND"

    def test_list_attachments_for_owner_ordered_by_sort_order(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer7@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True

        for _ in range(3):
            asset_id = self._upload_ready_asset(client)
            client.post("/api/v1/media/attachments", json={
                "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": "pr-list-test",
            })

        resp = client.get("/api/v1/media/attachments", params={
            "owner_type": "purchase_request", "owner_ref_id": "pr-list-test",
        })
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 3
        assert [i["sort_order"] for i in items] == [0, 1, 2]

    def test_archive_attachment(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer8@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True
        asset_id = self._upload_ready_asset(client)
        created = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": "pr-archive-test",
        }).json()

        resp = client.post(f"/api/v1/media/attachments/{created['id']}/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

        # مؤرشف لا يظهر في قائمة active الافتراضية
        listing = client.get("/api/v1/media/attachments", params={
            "owner_type": "purchase_request", "owner_ref_id": "pr-archive-test",
        }).json()
        assert len(listing) == 0

    def test_archived_slot_frees_room_for_new_upload(self, app_and_client):
        """أرشفة صورة من الحد الأقصى (5) تُفسِح مجالًا فعليًا لصورة جديدة — تحقُّق سلوكي، لا افتراض."""
        app, client = app_and_client
        _login_as(app, client, "buyer9@example.com")
        app.state.media_ownership_checker = lambda ot, oid, uploader: True

        attachment_ids = []
        for _ in range(5):
            asset_id = self._upload_ready_asset(client)
            att = client.post("/api/v1/media/attachments", json={
                "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": "pr-free-slot",
            }).json()
            attachment_ids.append(att["id"])

        client.post(f"/api/v1/media/attachments/{attachment_ids[0]}/archive")

        new_asset_id = self._upload_ready_asset(client)
        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": new_asset_id, "owner_type": "purchase_request", "owner_ref_id": "pr-free-slot",
        })
        assert resp.status_code == 201, "بعد أرشفة صورة واحدة من 5، يجب أن يُقبَل ربط جديد"


# ===========================================================================
# Batch 2 — Unit 2: Ownership الحقيقي + Signed/Public Access + Watermark
# Fixture مستقلة تمامًا (لا تُعدِّل app_and_client الأصلية — صفر مخاطرة
# Regression على اختبارات Unit 1 السبعة عشر الناجحة فعليًا على GitHub).
# ===========================================================================

from order_repository import InMemoryOrderRepository
from order_service import PurchaseRequest, Offer
from store_repository import InMemoryStoreRepository
from store_service import Store
from inventory_item_repository import InMemoryInventoryItemRepository
from inventory_item_service import InventoryItem
from cnt_repository import InMemoryCntRepository
from cnt_service import create_article_via_repository, publish_article_via_repository
from media_authorization import build_media_ownership_checker, build_media_view_authorization_checker, build_public_visibility_checker


@pytest.fixture
def unit2_app_and_client():
    """
    نفس تركيب app_and_client الأصلية + Repositories حقيقية لـPR/Offer/
    Store/Inventory (InMemory، من Batch 1 بلا تعديل) + Checkers حقيقيان
    (media_authorization.py) بدل lambda الثابتة — يختبر مسار الإنتاج
    الفعلي حرفيًا، لا محاكاة مبسَّطة.
    """
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(media_router)
    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.media_repository = InMemoryMediaRepository()
    app.state.storage_adapter = InMemoryStorageAdapter()

    order_repo = InMemoryOrderRepository()
    store_repo = InMemoryStoreRepository()
    inventory_repo = InMemoryInventoryItemRepository()
    cnt_repo = InMemoryCntRepository()
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
    return app, client


def _create_published_article(app, author_ref_id: str) -> str:
    """مساعد اختبار: مقال منشور فعليًا عبر cnt_service مباشرة (بلا مرور
    عبر cnt_api — media tests لا تحتاج/لا تُضيف cnt_router هنا)."""
    article = create_article_via_repository(
        app.state.cnt_repository, author_ref_id=author_ref_id,
        title_ar="مقال اختبار", body_ar="محتوى",
    )
    publish_article_via_repository(app.state.cnt_repository, article.id)
    return article.id


def _upload_ready_asset(client) -> str:
    resp = client.post("/api/v1/media/assets", files={"file": ("p.jpg", _make_jpeg_bytes(), "image/jpeg")})
    return resp.json()["id"]


class TestUnit2RealOwnership:
    """§7: Ownership حقيقي عبر Repositories فعلية، لا Placeholder."""

    def test_buyer_can_bind_to_own_purchase_request(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        buyer_id = _login_as(app, client, "buyer-u2-1@example.com")
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        asset_id = _upload_ready_asset(client)

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        })
        assert resp.status_code == 201, resp.text

    def test_non_buyer_cannot_bind_to_others_purchase_request(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        real_buyer_id = "user-real-buyer"
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=real_buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        _login_as(app, client, "impersonator-u2@example.com")
        asset_id = _upload_ready_asset(client)

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        })
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "BINDING_FORBIDDEN"

    def test_seller_can_bind_to_own_offer(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        seller_id = _login_as(app, client, "seller-u2-1@example.com")
        store = app.state.store_repository.insert_store(Store(id="", owner_user_ref_id=seller_id, status="active"))
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id="some-buyer", catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        offer = app.state.order_repository.insert_offer(
            Offer(id="", purchase_request_id=pr.id, seller_store_ref_id=store.id,
                  amount=150.0, currency="SAR", provides_shipping=False)
        )
        asset_id = _upload_ready_asset(client)

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "offer", "owner_ref_id": offer.id,
        })
        assert resp.status_code == 201, resp.text

    def test_seller_can_bind_to_own_inventory_item(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        seller_id = _login_as(app, client, "seller-u2-2@example.com")
        store = app.state.store_repository.insert_store(Store(id="", owner_user_ref_id=seller_id, status="active"))
        item = app.state.inventory_item_repository.insert_item(
            InventoryItem(id="", store_id=store.id, catalog_part_ref_id="part-1", condition_ref_id="cond-1",
                          pricing_mode="fixed_price", quantity=1, price_amount=50.0, price_currency="SAR")
        )
        asset_id = _upload_ready_asset(client)

        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "inventory_item", "owner_ref_id": item.id,
        })
        assert resp.status_code == 201, resp.text

    def test_archive_requires_real_ownership(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        buyer_id = _login_as(app, client, "buyer-u2-2@example.com")
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        }).json()

        # نفس المستخدم (المالك الفعلي) يستطيع الأرشفة
        resp = client.post(f"/api/v1/media/attachments/{att['id']}/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    def test_archive_rejected_for_non_owner(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        real_buyer_id = "user-real-buyer-2"
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=real_buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        # نربط الصورة بصلاحية مؤقَّتة (uploader هو المالك الحقيقي فعليًا هنا لتبسيط الإعداد)
        # ثم نحاول الأرشفة من مستخدم مختلف تمامًا
        app.state.media_ownership_checker = lambda ot, oid, uploader: True  # تبسيط الإعداد فقط
        _login_as(app, client, "setup-user@example.com")
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        }).json()
        client.post("/api/v1/auth/logout")

        # نعيد التفويض الحقيقي، ونحاول الأرشفة من مستخدم عشوائي لا يملك الـPR
        app.state.media_ownership_checker = build_media_ownership_checker(
            app.state.order_repository, app.state.store_repository, app.state.inventory_item_repository,
            auth_repo=app.state.auth_repository,
        )
        _login_as(app, client, "random-archiver@example.com")
        resp = client.post(f"/api/v1/media/attachments/{att['id']}/archive")
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "ARCHIVE_FORBIDDEN"


class TestUnit2WatermarkOnBind:
    """§9: Watermark فعلي على Display/Thumbnail لـinventory_item حصرًا، عند Bind."""

    def test_inventory_item_bind_applies_watermark(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        seller_id = _login_as(app, client, "seller-wm-1@example.com")
        store = app.state.store_repository.insert_store(Store(id="", owner_user_ref_id=seller_id, status="active"))
        item = app.state.inventory_item_repository.insert_item(
            InventoryItem(id="", store_id=store.id, catalog_part_ref_id="part-1", condition_ref_id="cond-1",
                          pricing_mode="fixed_price", quantity=1, price_amount=50.0, price_currency="SAR")
        )
        asset_id = _upload_ready_asset(client)
        asset_before = app.state.media_repository.get_asset_by_id(asset_id)
        display_before = app.state.storage_adapter.read(asset_before.storage_key_display)

        client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "inventory_item", "owner_ref_id": item.id,
        })

        asset_after = app.state.media_repository.get_asset_by_id(asset_id)
        display_after = app.state.storage_adapter.read(asset_after.storage_key_display)
        assert display_after != display_before, "محتوى Display يجب أن يتغيَّر فعليًا بعد العلامة المائية"

    def test_purchase_request_bind_does_not_apply_watermark(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        buyer_id = _login_as(app, client, "buyer-wm-1@example.com")
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        asset_id = _upload_ready_asset(client)
        asset_before = app.state.media_repository.get_asset_by_id(asset_id)
        display_before = app.state.storage_adapter.read(asset_before.storage_key_display)

        client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        })

        asset_after = app.state.media_repository.get_asset_by_id(asset_id)
        display_after = app.state.storage_adapter.read(asset_after.storage_key_display)
        assert display_after == display_before, "PR: لا Watermark إطلاقًا — المحتوى يجب أن يبقى مطابقًا للأصل حرفيًا"

    def test_offer_bind_does_not_apply_watermark(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        seller_id = _login_as(app, client, "seller-wm-2@example.com")
        store = app.state.store_repository.insert_store(Store(id="", owner_user_ref_id=seller_id, status="active"))
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id="buyer-x", catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        offer = app.state.order_repository.insert_offer(
            Offer(id="", purchase_request_id=pr.id, seller_store_ref_id=store.id,
                  amount=80.0, currency="SAR", provides_shipping=False)
        )
        asset_id = _upload_ready_asset(client)
        asset_before = app.state.media_repository.get_asset_by_id(asset_id)
        thumb_before = app.state.storage_adapter.read(asset_before.storage_key_thumbnail)

        client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "offer", "owner_ref_id": offer.id,
        })

        asset_after = app.state.media_repository.get_asset_by_id(asset_id)
        thumb_after = app.state.storage_adapter.read(asset_after.storage_key_thumbnail)
        assert thumb_after == thumb_before, "Offer: لا Watermark إطلاقًا"


class TestUnit2AccessEndpoint:
    """§9-10: GET /media/attachments/{id}/access — Public لـinventory_item، Signed مع مصفوفة تفويض لـPR/Offer."""

    def test_inventory_item_access_is_public_no_authorization_needed(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        seller_id = _login_as(app, client, "seller-acc-1@example.com")
        store = app.state.store_repository.insert_store(Store(id="", owner_user_ref_id=seller_id, status="active"))
        item = app.state.inventory_item_repository.insert_item(
            InventoryItem(id="", store_id=store.id, catalog_part_ref_id="part-1", condition_ref_id="cond-1",
                          pricing_mode="fixed_price", quantity=1, price_amount=50.0, price_currency="SAR")
        )
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "inventory_item", "owner_ref_id": item.id,
        }).json()
        client.post("/api/v1/auth/logout")

        # أي مستخدم آخر تمامًا (لا علاقة له بالمتجر) يصل بلا مشكلة — Public
        _login_as(app, client, "totally-unrelated-viewer@example.com")
        resp = client.get(f"/api/v1/media/attachments/{att['id']}/access")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["public"] is True
        assert body["watermarked"] is True
        assert "/public/" in body["display_url"]

    def test_purchase_request_access_forbidden_for_random_user(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        buyer_id = _login_as(app, client, "buyer-acc-1@example.com")
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        }).json()
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "random-viewer-acc@example.com")
        resp = client.get(f"/api/v1/media/attachments/{att['id']}/access")
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "ACCESS_FORBIDDEN"

    def test_purchase_request_access_granted_for_buyer(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        buyer_email = "buyer-acc-2@example.com"
        buyer_id = _login_as(app, client, buyer_email)
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        }).json()

        resp = client.get(f"/api/v1/media/attachments/{att['id']}/access")
        assert resp.status_code == 200
        body = resp.json()
        assert body["public"] is False
        assert body["watermarked"] is False
        assert "/private/" in body["display_url"]

    def test_purchase_request_access_granted_for_seller_with_active_offer(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        buyer_id = "user-buyer-acc-3"
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        app.state.media_ownership_checker = lambda ot, oid, uploader: True  # تبسيط رفع صورة المشتري نفسه
        _login_as(app, client, "buyer-setup-acc3@example.com")
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        }).json()
        client.post("/api/v1/auth/logout")

        seller_id = _login_as(app, client, "seller-acc-3@example.com")
        store = app.state.store_repository.insert_store(Store(id="", owner_user_ref_id=seller_id, status="active"))
        app.state.order_repository.insert_offer(
            Offer(id="", purchase_request_id=pr.id, seller_store_ref_id=store.id,
                  amount=90.0, currency="SAR", provides_shipping=False)
        )

        resp = client.get(f"/api/v1/media/attachments/{att['id']}/access")
        assert resp.status_code == 200, "بائع لديه Offer فعلي على هذا الطلب يجب أن يصل لصوره"

    def test_admin_can_access_any_private_attachment(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        buyer_id = "user-buyer-acc-4"
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        app.state.media_ownership_checker = lambda ot, oid, uploader: True
        _login_as(app, client, "buyer-setup-acc4@example.com")
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        }).json()
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "admin-acc-4@example.com", role="admin")
        resp = client.get(f"/api/v1/media/attachments/{att['id']}/access")
        assert resp.status_code == 200, "Admin يجب أن يصل دائمًا بغضّ النظر عن الملكية"

    def test_access_nonexistent_attachment_404(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        _login_as(app, client, "viewer-404@example.com")
        resp = client.get("/api/v1/media/attachments/ghost-attachment/access")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "ATTACHMENT_NOT_FOUND"


class TestArticleOwnerTypeCms:
    """CMS Featured Image (Master Handoff §8): owner_type='article' —
    أي news_editor يملك صلاحية الربط (لا نموذج ملكية فردية للمقالات، نفس
    _ensure_news_editor في cnt_api.py حرفيًا). لا نستخدم عمود DB منفصل على
    cnt.articles — media.attachments هو الـSSOT الوحيد، كما في
    purchase_request/offer/inventory_item."""

    def test_news_editor_can_bind_featured_image(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        _login_as(app, client, "editor-cms-1@example.com", role="news_editor")
        asset_id = _upload_ready_asset(client)
        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "article", "owner_ref_id": "article-fake-1",
        })
        assert resp.status_code == 201

    def test_non_editor_cannot_bind_featured_image(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        _login_as(app, client, "buyer-cms-1@example.com", role="individual_buyer")
        asset_id = _upload_ready_asset(client)
        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "article", "owner_ref_id": "article-fake-2",
        })
        assert resp.status_code == 403

    def test_article_attachment_limit_is_one(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        _login_as(app, client, "editor-cms-2@example.com", role="news_editor")
        first_asset = _upload_ready_asset(client)
        client.post("/api/v1/media/attachments", json={
            "asset_ref_id": first_asset, "owner_type": "article", "owner_ref_id": "article-fake-3",
        })
        second_asset = _upload_ready_asset(client)
        resp = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": second_asset, "owner_type": "article", "owner_ref_id": "article-fake-3",
        })
        assert resp.status_code == 409, "صورة بارزة واحدة فقط لكل مقال (§6 CMS)"

    def test_article_attachment_is_public_no_watermark(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        _login_as(app, client, "editor-cms-3@example.com", role="news_editor")
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "article", "owner_ref_id": "article-fake-4",
        }).json()
        client.post("/api/v1/auth/logout")

        # عام (أي مستخدم مسجَّل يراه، بلا فحص ملكية إضافي — نفس دلالة
        # inventory_item الحالية حرفيًا)، وبلا Watermark (محتوى تحريري لا
        # قطعة سوقية). لا جلسة إطلاقًا يبقى 401 هنا (سلوك Endpoint القائم
        # لكل الأنواع، لم نغيّره لـarticle تحديدًا).
        _login_as(app, client, "random-reader@example.com", role="individual_buyer")
        resp = client.get(f"/api/v1/media/attachments/{att['id']}/access")
        assert resp.status_code == 200
        assert resp.json()["public"] is True
        assert resp.json()["watermarked"] is False

    def test_missing_auth_repo_fails_closed_and_warns(self):
        """إثبات مباشر لسلوك media_authorization.py نفسه (لا عبر FastAPI):
        استدعاء build_media_ownership_checker بلا auth_repo (القيمة
        الافتراضية None) يرفض article دائمًا (Fail-closed) + يُصدِر
        RuntimeWarning صريحًا — لا فشل صامت لو نسي تركيب حقيقي تمرير
        auth_repo. هذا الاختبار يثبت آلية التحذير نفسها، لا Wiring تطبيق
        فعلي (لا يوجد main.py/app.py في هذا المستودع لإثبات ذلك عبره —
        موثَّق صراحة في media_authorization.py)."""
        import warnings
        from media_authorization import build_media_ownership_checker

        checker = build_media_ownership_checker(order_repo=None, store_repo=None, inventory_repo=None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = checker("article", "article-x", "uploader-x")

        assert result is False
        assert any(issubclass(w.category, RuntimeWarning) for w in caught)
        assert any("auth_repo" in str(w.message) for w in caught)


class TestPublicMediaPath:
    """Corrective — Featured Image public path: /media/public/* بلا جلسة
    إطلاقًا. يغطي أربع سيناريوهات مطلوبة صراحة."""

    def test_anonymous_can_access_published_article_image(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        editor_id = _login_as(app, client, "pub-editor-1@example.com", role="news_editor")
        article_id = _create_published_article(app, editor_id)
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "article", "owner_ref_id": article_id,
        }).json()
        client.post("/api/v1/auth/logout")

        # بلا أي جلسة إطلاقًا — لا login هنا نهائيًا
        list_resp = client.get(
            "/api/v1/media/public/attachments",
            params={"owner_type": "article", "owner_ref_id": article_id},
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        access_resp = client.get(f"/api/v1/media/public/attachments/{att['id']}/access")
        assert access_resp.status_code == 200
        assert access_resp.json()["public"] is True
        assert access_resp.json()["watermarked"] is False

    def test_archived_attachment_not_accessible_via_old_id_after_replacement(self, unit2_app_and_client):
        """Root Cause fix: مقال Published لا يعني أن كل Attachment تاريخي
        مربوط به صالح — أرشفة/استبدال الصورة البارزة يجب أن تُسقِط الوصول
        العام بالـID القديم فورًا، رغم بقاء المقال منشورًا طوال الوقت."""
        app, client = unit2_app_and_client
        editor_id = _login_as(app, client, "pub-editor-replace@example.com", role="news_editor")
        article_id = _create_published_article(app, editor_id)

        # الصورة الأولى — تُربَط وتُتاح للعامة فعليًا أولًا
        first_asset_id = _upload_ready_asset(client)
        first_att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": first_asset_id, "owner_type": "article", "owner_ref_id": article_id,
        }).json()

        pre_archive_check = client.get(f"/api/v1/media/public/attachments/{first_att['id']}/access")
        assert pre_archive_check.status_code == 200, "Sanity: الصورة الأولى متاحة للعامة قبل الأرشفة"

        # نؤرشف الأولى (نفس مسار /attachments/{id}/archive الموجود، بلا تعديل عليه)
        archive_resp = client.post(f"/api/v1/media/attachments/{first_att['id']}/archive")
        assert archive_resp.status_code == 200
        assert archive_resp.json()["status"] == "archived"

        # نربط صورة بديلة (المقال ما زال Published طوال الوقت — بلا تغيير حالته إطلاقًا)
        second_asset_id = _upload_ready_asset(client)
        second_att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": second_asset_id, "owner_type": "article", "owner_ref_id": article_id,
        }).json()
        client.post("/api/v1/auth/logout")

        # الـID القديم: 404 رغم أن المقال لا يزال منشورًا وrecord الـAttachment موجود فعليًا في القاعدة
        old_id_resp = client.get(f"/api/v1/media/public/attachments/{first_att['id']}/access")
        assert old_id_resp.status_code == 404
        assert old_id_resp.json()["detail"]["error_code"] == "ATTACHMENT_NOT_FOUND"

        # الـID الجديد: يعمل بشكل طبيعي
        new_id_resp = client.get(f"/api/v1/media/public/attachments/{second_att['id']}/access")
        assert new_id_resp.status_code == 200

        # قائمة المرفقات العامة تعرض النشط فقط (1)، لا الاثنين
        list_resp = client.get(
            "/api/v1/media/public/attachments",
            params={"owner_type": "article", "owner_ref_id": article_id},
        )
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["id"] == second_att["id"]
        app, client = unit2_app_and_client
        buyer_id = _login_as(app, client, "pub-buyer-1@example.com")
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "purchase_request", "owner_ref_id": pr.id,
        }).json()
        client.post("/api/v1/auth/logout")

        list_resp = client.get(
            "/api/v1/media/public/attachments",
            params={"owner_type": "purchase_request", "owner_ref_id": pr.id},
        )
        assert list_resp.status_code == 200
        assert list_resp.json() == [], "لا كشف Metadata حتى (قائمة فارغة، لا 403 يفصح عن الوجود)"

        access_resp = client.get(f"/api/v1/media/public/attachments/{att['id']}/access")
        assert access_resp.status_code == 404, "المسار العام لا يكشف مرفقات Private إطلاقًا، حتى بمعرّف صحيح"
        assert access_resp.json()["detail"]["error_code"] == "ATTACHMENT_NOT_FOUND"

    def test_anonymous_cannot_use_public_path_for_private_offer_media(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        seller_id = _login_as(app, client, "pub-seller-1@example.com")
        store = app.state.store_repository.insert_store(Store(id="", owner_user_ref_id=seller_id, status="active"))
        pr = app.state.order_repository.insert_purchase_request(
            PurchaseRequest(id="", buyer_user_ref_id="some-buyer", catalog_part_ref_id="part-1", trim_ref_id="trim-1")
        )
        offer = app.state.order_repository.insert_offer(
            Offer(id="", purchase_request_id=pr.id, seller_store_ref_id=store.id,
                  amount=100.0, currency="SAR", provides_shipping=False)
        )
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "offer", "owner_ref_id": offer.id,
        }).json()
        client.post("/api/v1/auth/logout")

        access_resp = client.get(f"/api/v1/media/public/attachments/{att['id']}/access")
        assert access_resp.status_code == 404

    def test_draft_article_image_not_publicly_exposed(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        editor_id = _login_as(app, client, "pub-editor-2@example.com", role="news_editor")
        # مقال Draft — بلا publish_article_via_repository هنا عمدًا
        from cnt_service import create_article_via_repository as _create
        article = _create(app.state.cnt_repository, author_ref_id=editor_id, title_ar="مسودة", body_ar="نص")
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "article", "owner_ref_id": article.id,
        }).json()
        client.post("/api/v1/auth/logout")

        list_resp = client.get(
            "/api/v1/media/public/attachments",
            params={"owner_type": "article", "owner_ref_id": article.id},
        )
        assert list_resp.json() == [], "مقال Draft: لا صورة تُكشَف للعامة رغم أن Attachment نفسه active"

        access_resp = client.get(f"/api/v1/media/public/attachments/{att['id']}/access")
        assert access_resp.status_code == 404

    def test_archived_article_image_not_publicly_exposed(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        editor_id = _login_as(app, client, "pub-editor-3@example.com", role="news_editor")
        article_id = _create_published_article(app, editor_id)
        asset_id = _upload_ready_asset(client)
        att = client.post("/api/v1/media/attachments", json={
            "asset_ref_id": asset_id, "owner_type": "article", "owner_ref_id": article_id,
        }).json()
        from cnt_service import archive_article_via_repository
        archive_article_via_repository(app.state.cnt_repository, article_id)
        client.post("/api/v1/auth/logout")

        access_resp = client.get(f"/api/v1/media/public/attachments/{att['id']}/access")
        assert access_resp.status_code == 404, "أُرشِف المقال بعد النشر — الصورة يجب ألا تبقى مكشوفة للعامة"

    def test_article_without_image_public_list_is_empty_not_error(self, unit2_app_and_client):
        app, client = unit2_app_and_client
        editor_id = _login_as(app, client, "pub-editor-4@example.com", role="news_editor")
        article_id = _create_published_article(app, editor_id)
        client.post("/api/v1/auth/logout")

        resp = client.get(
            "/api/v1/media/public/attachments",
            params={"owner_type": "article", "owner_ref_id": article_id},
        )
        assert resp.status_code == 200
        assert resp.json() == [], "مقال منشور بلا صورة بارزة: قائمة فارغة سليمة، لا خطأ"

    def test_nonexistent_owner_type_returns_empty_not_error(self, unit2_app_and_client):
        _, client = unit2_app_and_client
        resp = client.get(
            "/api/v1/media/public/attachments",
            params={"owner_type": "inventory_item", "owner_ref_id": "whatever"},
        )
        assert resp.status_code == 200
        assert resp.json() == [], "inventory_item خارج نطاق المسار العام حاليًا عمدًا (لا 'كل Media عامة')"
