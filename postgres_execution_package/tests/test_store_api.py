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
