"""
test_pct_api.py — اختبارات وحدة لطبقة REST API لخدمة PCT
تستخدم InMemoryPctRepository وInMemoryAuthRepository (لا اتصال قاعدة بيانات).
المرجع: PCT Contract Extension & Implementation Plan
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from pct_api import router as pct_router
from pct_repository import InMemoryPctRepository
from aud_repository import InMemoryAudRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(pct_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.aud_repository = InMemoryAudRepository()
    app.state.pct_repository = InMemoryPctRepository()

    client = TestClient(app, base_url="https://testserver")
    return app, client


def _login_as(app, client, email: str, password: str, role: str = "individual_buyer") -> str:
    repo = app.state.auth_repository
    user_id = repo.create_user()
    repo.set_user_role(user_id, role)
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password=password)
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": password})
    assert resp.status_code == 200
    return user_id


class TestProposePart:

    def test_propose_part_success(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller@example.com", "Str0ngPass1!")
        resp = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"})
        assert resp.status_code == 201
        assert resp.json()["status"] == "proposed"
        assert resp.json()["category_id"] == "cat-1"

    def test_propose_part_requires_session(self, app_and_client):
        _, client = app_and_client
        resp = client.post("/api/v1/pct/parts", json={"category_id": "any"})
        assert resp.status_code == 401


class TestApprovePartAuthorization:
    """REQ-PCT-002: مدير النظام فقط — الاختبار الأهم في هذا الامتداد."""

    def test_regular_user_forbidden_from_approving(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", "Str0ngPass1!", role="individual_buyer")

        propose_resp = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"})
        assert propose_resp.status_code == 201
        part_id = propose_resp.json()["id"]

        approve_resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
        assert approve_resp.status_code == 403
        assert approve_resp.json()["detail"]["error_code"] == "FORBIDDEN"

    def test_seller_role_also_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller2@example.com", "Str0ngPass1!", role="business_seller")
        propose_resp = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"})
        part_id = propose_resp.json()["id"]

        resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
        assert resp.status_code == 403

    def test_admin_role_can_approve(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", "Str0ngPass1!", role="admin")
        propose_resp = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"})
        part_id = propose_resp.json()["id"]

        resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_super_admin_role_can_approve(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "superadmin@example.com", "Str0ngPass1!", role="super_admin")
        propose_resp = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"})
        part_id = propose_resp.json()["id"]

        resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
        assert resp.status_code == 200

    def test_approve_nonexistent_part_returns_404_even_for_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", "Str0ngPass1!", role="admin")
        resp = client.post("/api/v1/pct/parts/does-not-exist/approve")
        assert resp.status_code == 404

    def test_double_approve_returns_409(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin3@example.com", "Str0ngPass1!", role="admin")
        propose_resp = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"})
        part_id = propose_resp.json()["id"]

        first = client.post(f"/api/v1/pct/parts/{part_id}/approve")
        assert first.status_code == 200
        second = client.post(f"/api/v1/pct/parts/{part_id}/approve")
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "INVALID_STATUS_TRANSITION"


class TestLocalizedNames:

    def test_add_name_success(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "namer@example.com", "Str0ngPass1!")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]

        resp = client.post(f"/api/v1/pct/parts/{part_id}/names",
                            json={"name_value": "فلتر زيت", "name_kind": "canonical"})
        assert resp.status_code == 201
        assert resp.json()["name_value"] == "فلتر زيت"

    def test_add_name_unknown_kind_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "namer2@example.com", "Str0ngPass1!")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]

        resp = client.post(f"/api/v1/pct/parts/{part_id}/names",
                            json={"name_value": "x", "name_kind": "not_a_real_kind"})
        assert resp.status_code == 400

    def test_add_name_to_nonexistent_part_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "namer3@example.com", "Str0ngPass1!")
        resp = client.post("/api/v1/pct/parts/ghost/names", json={"name_value": "x", "name_kind": "canonical"})
        assert resp.status_code == 404


class TestOemNumbers:

    def test_add_oem_number_success(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "oemer@example.com", "Str0ngPass1!")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]

        resp = client.post(f"/api/v1/pct/parts/{part_id}/oem-numbers",
                            json={"manufacturer_ref_id": "mfr-1", "oem_number": "OEM-12345"})
        assert resp.status_code == 201

    def test_duplicate_oem_number_same_manufacturer_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "oemer2@example.com", "Str0ngPass1!")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]

        first = client.post(f"/api/v1/pct/parts/{part_id}/oem-numbers",
                             json={"manufacturer_ref_id": "mfr-1", "oem_number": "OEM-DUP"})
        assert first.status_code == 201
        second = client.post(f"/api/v1/pct/parts/{part_id}/oem-numbers",
                              json={"manufacturer_ref_id": "mfr-1", "oem_number": "OEM-DUP"})
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "DUPLICATE_OEM_NUMBER"


class TestGetPart:

    def test_get_existing_part(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "getter@example.com", "Str0ngPass1!")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]

        resp = client.get(f"/api/v1/pct/parts/{part_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "proposed"

    def test_get_nonexistent_part_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "getter2@example.com", "Str0ngPass1!")
        resp = client.get("/api/v1/pct/parts/ghost")
        assert resp.status_code == 404


class TestCR017PublicReadConsistency:
    """CR-017: قطعة كتالوج معتمدة (approved) قابلة للقراءة العامة. غير ذلك
    يبقى بلا تغيير عن سلوكه السابق (يتطلب أي جلسة صالحة)."""

    def test_approved_part_readable_without_session(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin10@example.com", "Str0ngPass1!", role="admin")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]
        client.post(f"/api/v1/pct/parts/{part_id}/approve")
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/pct/parts/{part_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_proposed_part_still_requires_session_unchanged(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin11@example.com", "Str0ngPass1!", role="admin")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/pct/parts/{part_id}")
        assert resp.status_code == 401
