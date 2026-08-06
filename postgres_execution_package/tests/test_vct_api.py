"""
test_vct_api.py — اختبارات وحدة لطبقة REST API لخدمة VCT
تستخدم InMemoryVctRepository وInMemoryAuthRepository (لا اتصال قاعدة بيانات).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from vct_api import router as vct_router
from vct_repository import InMemoryVctRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(vct_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.vct_repository = InMemoryVctRepository()

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


class TestManufacturerLifecycle:

    def test_propose_and_get_manufacturer(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "proposer@example.com")

        resp = client.post("/api/v1/vct/manufacturers")
        assert resp.status_code == 201
        assert resp.json()["status"] == "proposed"
        m_id = resp.json()["id"]

        get_resp = client.get(f"/api/v1/vct/manufacturers/{m_id}")
        assert get_resp.status_code == 200

    def test_get_nonexistent_manufacturer_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "getter@example.com")
        resp = client.get("/api/v1/vct/manufacturers/ghost")
        assert resp.status_code == 404


class TestApproveManufacturerAuthorization:
    """REQ-VCT-002: مدير النظام فقط."""

    def test_regular_user_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]

        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "FORBIDDEN"

    def test_admin_can_approve(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]

        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_double_approve_returns_409(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", role="admin")
        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]

        first = client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        assert first.status_code == 200
        second = client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        assert second.status_code == 409


class TestModelRequiresApprovedManufacturer:
    """REQ-VCT-003: لا موديل تحت شركة مصنّعة غير معتمَدة."""

    def test_model_rejected_under_unapproved_manufacturer(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "modeler@example.com")
        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]  # لا يزال proposed

        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/models")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "MANUFACTURER_NOT_APPROVED"

    def test_model_accepted_under_approved_manufacturer(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "modeler2@example.com", role="admin")
        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
        client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")

        resp = client.post(f"/api/v1/vct/manufacturers/{m_id}/models")
        assert resp.status_code == 201
        assert resp.json()["manufacturer_id"] == m_id

    def test_model_under_nonexistent_manufacturer_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "modeler3@example.com")
        resp = client.post("/api/v1/vct/manufacturers/ghost/models")
        assert resp.status_code == 404


class TestFullHierarchyChain:
    """manufacturer -> model -> generation -> trim، السلسلة الكاملة التي تحتاجها CMP لاحقًا."""

    def test_full_chain_creates_valid_trim(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "chain@example.com", role="admin")

        m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
        client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
        model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
        gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]

        trim_resp = client.post(f"/api/v1/vct/generations/{gen_id}/trims",
                                 json={"fuel_type_ref_id": "fuel-1", "transmission_type_ref_id": "trans-1"})
        assert trim_resp.status_code == 201
        trim_id = trim_resp.json()["id"]

        get_trim_resp = client.get(f"/api/v1/vct/trims/{trim_id}")
        assert get_trim_resp.status_code == 200
        assert get_trim_resp.json()["fuel_type_ref_id"] == "fuel-1"

    def test_generation_under_nonexistent_model_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "gen@example.com")
        resp = client.post("/api/v1/vct/models/ghost/generations")
        assert resp.status_code == 404

    def test_trim_under_nonexistent_generation_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "trim@example.com")
        resp = client.post("/api/v1/vct/generations/ghost/trims",
                            json={"fuel_type_ref_id": "f", "transmission_type_ref_id": "t"})
        assert resp.status_code == 404

    def test_get_nonexistent_trim_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "trimget@example.com")
        resp = client.get("/api/v1/vct/trims/ghost")
        assert resp.status_code == 404
