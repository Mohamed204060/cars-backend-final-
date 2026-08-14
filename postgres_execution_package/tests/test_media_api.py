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
