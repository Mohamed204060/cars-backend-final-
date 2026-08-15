"""
test_store_api.py — اختبارات وحدة لطبقة REST API لخدمة المتاجر
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from store_api import router as store_router
from store_repository import InMemoryStoreRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(store_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.store_repository = InMemoryStoreRepository()

    client = TestClient(app, base_url="https://testserver")
    return app, client


def _login_as(app, client, email: str, role: str = "individual_seller") -> str:
    repo = app.state.auth_repository
    user_id = repo.create_user()
    repo.set_user_role(user_id, role)
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password="Str0ngPass1!")
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    assert resp.status_code == 200
    return user_id


class TestCreateAndGetStore:

    def test_create_store_is_active_immediately(self, app_and_client):
        app, client = app_and_client
        user_id = _login_as(app, client, "seller@example.com")

        resp = client.post("/api/v1/store/stores", json={})
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"
        assert resp.json()["owner_user_ref_id"] == user_id

    def test_get_nonexistent_store_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "getter@example.com")
        resp = client.get("/api/v1/store/stores/ghost")
        assert resp.status_code == 404


class TestGetMyStore:
    """Unit 4+5 — فجوة حقيقية مكتشَفة: GET /stores/mine (اشتقاق متجر البائع الحالي من الجلسة)."""

    def test_returns_own_store(self, app_and_client):
        app, client = app_and_client
        user_id = _login_as(app, client, "mystore1@example.com")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        resp = client.get("/api/v1/store/stores/mine")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == store_id
        assert body["owner_user_ref_id"] == user_id

    def test_404_when_no_store_owned(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "mystore2@example.com")
        resp = client.get("/api/v1/store/stores/mine")
        assert resp.status_code == 404

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/store/stores/mine")
        assert resp.status_code == 401

    def test_mine_not_captured_as_store_id_route_collision(self, app_and_client):
        """يجب ألا يُطابِق GET /stores/{store_id} كلمة 'mine' — تحقُّق صريح من ترتيب التسجيل."""
        app, client = app_and_client
        user_id = _login_as(app, client, "mystore3@example.com")
        client.post("/api/v1/store/stores", json={})

        resp = client.get("/api/v1/store/stores/mine")
        assert resp.status_code == 200
        assert resp.json()["owner_user_ref_id"] == user_id

    def test_scoped_to_current_user_not_other_sellers(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "mystore4a@example.com")
        store_a_id = client.post("/api/v1/store/stores", json={}).json()["id"]
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "mystore4b@example.com")
        store_b_id = client.post("/api/v1/store/stores", json={}).json()["id"]
        resp = client.get("/api/v1/store/stores/mine")
        assert resp.status_code == 200
        assert resp.json()["id"] == store_b_id
        assert resp.json()["id"] != store_a_id


class TestStoreStatusChangeAuthorization:
    """REQ-STR-004: مدير النظام أو المشرف فقط — لا مالك المتجر نفسه."""

    def test_store_owner_cannot_change_own_status(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "owner@example.com", role="individual_seller")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        resp = client.post(f"/api/v1/store/stores/{store_id}/status", json={"new_status": "suspended"})
        assert resp.status_code == 403

    def test_moderator_can_suspend(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "owner2@example.com", role="individual_seller")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "mod@example.com", role="moderator")
        resp = client.post(f"/api/v1/store/stores/{store_id}/status", json={"new_status": "suspended"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "suspended"

    def test_admin_can_suspend(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "owner3@example.com", role="individual_seller")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.post(f"/api/v1/store/stores/{store_id}/status", json={"new_status": "suspended"})
        assert resp.status_code == 200

    def test_invalid_transition_returns_409(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "owner4@example.com", role="individual_seller")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "admin2@example.com", role="admin")
        client.post(f"/api/v1/store/stores/{store_id}/status", json={"new_status": "archived"})
        resp = client.post(f"/api/v1/store/stores/{store_id}/status", json={"new_status": "active"})
        assert resp.status_code == 409


class TestTransferOwnershipAuthorization:
    """REQ-STR-006: مدير النظام حصريًا — moderator لا يكفي."""

    def test_moderator_cannot_transfer_ownership(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "owner5@example.com", role="individual_seller")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "mod2@example.com", role="moderator")
        resp = client.post(f"/api/v1/store/stores/{store_id}/transfer-ownership",
                            json={"new_owner_user_ref_id": "someone-else"})
        assert resp.status_code == 403

    def test_admin_can_transfer_ownership(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "owner6@example.com", role="individual_seller")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "admin3@example.com", role="admin")
        resp = client.post(f"/api/v1/store/stores/{store_id}/transfer-ownership",
                            json={"new_owner_user_ref_id": "new-owner-id"})
        assert resp.status_code == 200
        assert resp.json()["owner_user_ref_id"] == "new-owner-id"


class TestCR017PublicReadConsistency:
    """CR-017: متجر status='active' قابل للقراءة العامة بحقول محدودة (بلا
    owner_user_ref_id). أي جلسة تبقى ترى الاستجابة الكاملة كما كانت
    تمامًا قبل CR-017 — بلا تغيير."""

    def test_active_store_public_response_excludes_owner(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller10@example.com")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/store/stores/{store_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert "owner_user_ref_id" not in body
        assert body["status"] == "active"

    def test_non_active_store_404_for_public(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller11@example.com", role="admin")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]
        client.post(f"/api/v1/store/stores/{store_id}/status", json={"new_status": "suspended"})
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/store/stores/{store_id}")
        assert resp.status_code == 404

    def test_authenticated_response_unchanged_includes_owner(self, app_and_client):
        """يحافظ على السلوك القديم حرفيًا: أي جلسة ترى الاستجابة الكاملة."""
        app, client = app_and_client
        _login_as(app, client, "seller12@example.com")
        store_id = client.post("/api/v1/store/stores", json={}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "randombuyer2@example.com", role="individual_buyer")
        resp = client.get(f"/api/v1/store/stores/{store_id}")
        assert resp.status_code == 200
        assert "owner_user_ref_id" in resp.json()
