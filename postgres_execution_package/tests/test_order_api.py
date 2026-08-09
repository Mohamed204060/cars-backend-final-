"""
test_order_api.py — اختبارات وحدة لطبقة REST API لخدمة الطلبات (PUR)
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
from pct_api import router as pct_router
from pct_repository import InMemoryPctRepository
from order_api import router as order_router
from order_repository import InMemoryOrderRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(store_router)
    app.include_router(pct_router)
    app.include_router(order_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.store_repository = InMemoryStoreRepository()
    app.state.pct_repository = InMemoryPctRepository()
    app.state.order_repository = InMemoryOrderRepository()

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


def _make_approved_part(app, client) -> str:
    """جلسة admin مستقلة، بنفس نمط الإصلاح المعتمَد في Store+Inventory."""
    _login_as(app, client, "admin-setup@example.com", role="admin")
    part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]
    resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
    assert resp.status_code == 200, resp.text
    client.post("/api/v1/auth/logout")
    return part_id


def _make_own_store(client) -> str:
    return client.post("/api/v1/store/stores", json={}).json()["id"]


class TestCreatePurchaseRequest:

    def test_create_pr_success(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer1@example.com")

        resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "open"
        assert body["business_code"].startswith("PR-")

    def test_create_pr_unapproved_part_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer2@example.com")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]

        resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"})
        assert resp.status_code == 409


class TestCancelPurchaseRequest:
    """REQ-PUR-009: المشتري صاحب الطلب حصرًا."""

    def test_non_owner_cannot_cancel(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer3@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "stranger@example.com")
        resp = client.post(f"/api/v1/purchase-requests/{pr_id}/cancel")
        assert resp.status_code == 403

    def test_owner_can_cancel(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer4@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        resp = client.post(f"/api/v1/purchase-requests/{pr_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


class TestSubmitOffer:
    """REQ-PUR-011: seller_store_ref_id يُشتَق من الجلسة، لا من الطلب."""

    def test_seller_without_store_cannot_submit(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer5@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller-nostore@example.com")
        resp = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                            json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        assert resp.status_code == 403

    def test_seller_with_store_submits_successfully(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer6@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller1@example.com")
        _make_own_store(client)
        resp = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                            json={"amount": 100.0, "currency": "SAR", "provides_shipping": True})
        assert resp.status_code == 201
        assert resp.json()["business_code"].startswith("OF-")

    def test_duplicate_active_offer_rejected(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer7@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller2@example.com")
        _make_own_store(client)
        first = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                             json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        assert first.status_code == 201
        second = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                              json={"amount": 200.0, "currency": "SAR", "provides_shipping": False})
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "DUPLICATE_ACTIVE_OFFER"

    def test_closed_pr_rejects_new_offer(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer8@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]
        client.post(f"/api/v1/purchase-requests/{pr_id}/cancel")

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller3@example.com")
        _make_own_store(client)
        resp = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                            json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "PURCHASE_REQUEST_CLOSED"


class TestAcceptOffer:
    """REQ-PUR-013/014: المشتري صاحب الطلب حصرًا؛ رفض بقية العروض تلقائيًا."""

    def test_non_owner_cannot_accept(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer9@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller4@example.com")
        _make_own_store(client)
        offer_id = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                json={"amount": 100.0, "currency": "SAR", "provides_shipping": False}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "stranger2@example.com")
        resp = client.post(f"/api/v1/offers/{offer_id}/accept")
        assert resp.status_code == 403

    def test_owner_accepts_and_pr_fulfilled(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        buyer_email, buyer_password = "buyer10@example.com", "Str0ngPass1!"
        _login_as(app, client, buyer_email)
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller5@example.com")
        _make_own_store(client)
        offer_id = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                json={"amount": 100.0, "currency": "SAR", "provides_shipping": False}).json()["id"]

        # العودة لنفس حساب المشتري بالضبط (نفس البريد وكلمة المرور => نفس user_id)
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"login_identifier": buyer_email, "password": buyer_password})

        resp = client.post(f"/api/v1/offers/{offer_id}/accept")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "fulfilled"

    def test_accepted_offer_rejects_other_offers(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        buyer_email, buyer_password = "buyer11@example.com", "Str0ngPass1!"
        _login_as(app, client, buyer_email)
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller6@example.com")
        _make_own_store(client)
        offer1_id = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                 json={"amount": 100.0, "currency": "SAR", "provides_shipping": False}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller7@example.com")
        _make_own_store(client)
        offer2_id = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                 json={"amount": 90.0, "currency": "SAR", "provides_shipping": False}).json()["id"]

        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"login_identifier": buyer_email, "password": buyer_password})
        accept_resp = client.post(f"/api/v1/offers/{offer1_id}/accept")
        assert accept_resp.status_code == 200

        # لا Endpoint مباشر لعرض حالة عرض واحد بمعزل؛ نتحقق من الأثر الجانبي
        # (رفض العروض الأخرى تلقائيًا، REQ-PUR-014) مباشرة عبر المستودع نفسه،
        # بنفس ما تراه أي طبقة عرض لاحقة (لا اختصار في منطق العمل نفسه).
        offer2 = app.state.order_repository.get_offer_by_id(offer2_id)
        assert offer2.status == "rejected"


class TestWithdrawOffer:
    """REQ-PUR-018: البائع صاحب العرض حصرًا."""

    def test_non_owner_cannot_withdraw(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer12@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller8@example.com")
        _make_own_store(client)
        offer_id = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                json={"amount": 100.0, "currency": "SAR", "provides_shipping": False}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller9@example.com")
        _make_own_store(client)
        resp = client.post(f"/api/v1/offers/{offer_id}/withdraw")
        assert resp.status_code == 403

    def test_owner_can_withdraw(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer13@example.com")
        pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller10@example.com")
        _make_own_store(client)
        offer_id = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                                json={"amount": 100.0, "currency": "SAR", "provides_shipping": False}).json()["id"]

        resp = client.post(f"/api/v1/offers/{offer_id}/withdraw")
        assert resp.status_code == 200
        assert resp.json()["status"] == "withdrawn"




class TestCR021PurchaseRequestDisplayProjection:
    """CR-021 — Read Model منفصل، GET /purchase-requests/mine/display."""

    def test_part_name_manufacturer_model_resolved(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr021-1@example.com")

        app.state.order_repository.set_part_name(part_id, "طرمبة بنزين")
        app.state.order_repository.set_trim_vehicle_info(
            "trim-1", "model-1", "كامري", "mfr-1", "تويوتا",
        )
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"})

        resp = client.get("/api/v1/purchase-requests/mine/display")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["part_name"] == "طرمبة بنزين"
        assert item["manufacturer_name"] == "تويوتا"
        assert item["model_name"] == "كامري"

    def test_created_at_present_and_valid(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr021-2@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-x"})

        item = client.get("/api/v1/purchase-requests/mine/display").json()["items"][0]
        assert item["created_at"]  # ISO string غير فارغ

    def test_scoping_excludes_other_buyers_requests(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr021-3a@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-x"})
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "buyer-cr021-3b@example.com")
        resp = client.get("/api/v1/purchase-requests/mine/display")
        assert resp.json()["pagination"]["total_items"] == 0

    def test_missing_localized_names_resolve_to_null_not_error(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr021-4@example.com")
        # لا set_part_name/set_trim_vehicle_info هنا عمدًا — يحاكي غياب توطين حقيقي
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-unknown"})

        resp = client.get("/api/v1/purchase-requests/mine/display")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["part_name"] is None
        assert item["manufacturer_name"] is None
        assert item["model_name"] is None

    def test_original_mine_endpoint_unchanged(self, app_and_client):
        """يثبت أن GET /purchase-requests/mine الأساسي لم يتأثر إطلاقًا."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr021-5@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-x"})

        resp = client.get("/api/v1/purchase-requests/mine")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert "part_name" not in item
        assert "manufacturer_name" not in item
        assert set(item.keys()) == {"id", "business_code", "buyer_user_ref_id", "catalog_part_ref_id", "trim_ref_id", "status"}

    def test_no_internal_or_other_user_data_leak(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr021-6@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-x"})

        body_text = client.get("/api/v1/purchase-requests/mine/display").text
        assert "buyer_user_ref_id" not in body_text
        assert "password" not in body_text.lower()
