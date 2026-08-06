"""
test_trm_api.py — اختبارات وحدة لطبقة REST API لخدمة الثقة والتقييمات (TRM)
تستخدم Order الحقيقية (لا محاكاة) للتحقق من الأهلية الفعلية.
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
from order_api import router as order_router
from order_repository import InMemoryOrderRepository
from store_api import router as store_router
from store_repository import InMemoryStoreRepository
from trm_api import router as trm_router
from trm_repository import InMemoryTrmRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(pct_router)
    app.include_router(order_router)
    app.include_router(store_router)
    app.include_router(trm_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.pct_repository = InMemoryPctRepository()
    app.state.order_repository = InMemoryOrderRepository()
    app.state.store_repository = InMemoryStoreRepository()
    app.state.trm_repository = InMemoryTrmRepository()

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
    _login_as(app, client, "admin-setup@example.com", role="admin")
    part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]
    assert client.post(f"/api/v1/pct/parts/{part_id}/approve").status_code == 200
    client.post("/api/v1/auth/logout")
    return part_id


def _make_fulfilled_purchase(app, client) -> tuple:
    """يُنشئ سلسلة كاملة: مشترٍ → طلب → بائع → عرض → قبول، تعيد (buyer_email, buyer_password, pr_id)."""
    part_id = _make_approved_part(app, client)
    buyer_email, buyer_password = "trm-buyer@example.com", "Str0ngPass1!"
    _login_as(app, client, buyer_email)
    pr_id = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

    client.post("/api/v1/auth/logout")
    _login_as(app, client, "trm-seller@example.com")
    client.post("/api/v1/store/stores", json={})
    offer_id = client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                            json={"amount": 100.0, "currency": "SAR", "provides_shipping": False}).json()["id"]

    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"login_identifier": buyer_email, "password": buyer_password})
    accept_resp = client.post(f"/api/v1/offers/{offer_id}/accept")
    assert accept_resp.status_code == 200
    return buyer_email, buyer_password, pr_id


class TestCreateRatingEligibility:

    def test_ineligible_user_rejected(self, app_and_client):
        """مستخدم لم يُتمّ أي صفقة على هذا الطلب لا يجوز أن يقيِّم عنه."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "unrelated-user@example.com")
        fake_pr_id = "nonexistent-pr"

        resp = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": "some-store", "source_purchase_request_ref_id": fake_pr_id, "score": 5,
        })
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "RATING_INELIGIBLE"

    def test_eligible_buyer_can_rate_after_fulfillment(self, app_and_client):
        app, client = app_and_client
        buyer_email, buyer_password, pr_id = _make_fulfilled_purchase(app, client)
        # الجلسة الحالية بالفعل هي المشتري (آخر خطوة في _make_fulfilled_purchase)

        resp = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": "seller-store-1",
            "source_purchase_request_ref_id": pr_id, "score": 5, "comment": "بائع ممتاز",
        })
        assert resp.status_code == 201, resp.text
        assert resp.json()["score"] == 5

    def test_duplicate_rating_rejected(self, app_and_client):
        app, client = app_and_client
        buyer_email, buyer_password, pr_id = _make_fulfilled_purchase(app, client)

        first = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": "seller-store-1",
            "source_purchase_request_ref_id": pr_id, "score": 5,
        })
        assert first.status_code == 201
        second = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": "seller-store-1",
            "source_purchase_request_ref_id": pr_id, "score": 3,
        })
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "DUPLICATE_RATING"

    def test_invalid_score_rejected(self, app_and_client):
        app, client = app_and_client
        buyer_email, buyer_password, pr_id = _make_fulfilled_purchase(app, client)

        resp = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": "seller-store-1",
            "source_purchase_request_ref_id": pr_id, "score": 99,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_SCORE"


class TestUpdateAndArchiveRating:

    def test_non_owner_cannot_update(self, app_and_client):
        app, client = app_and_client
        buyer_email, buyer_password, pr_id = _make_fulfilled_purchase(app, client)
        rating_id = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": "seller-store-1",
            "source_purchase_request_ref_id": pr_id, "score": 4,
        }).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "stranger@example.com")
        resp = client.patch(f"/api/v1/ratings/{rating_id}", json={"score": 1})
        assert resp.status_code == 403

    def test_owner_can_update_then_archive(self, app_and_client):
        app, client = app_and_client
        buyer_email, buyer_password, pr_id = _make_fulfilled_purchase(app, client)
        rating_id = client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": "seller-store-1",
            "source_purchase_request_ref_id": pr_id, "score": 4,
        }).json()["id"]

        update_resp = client.patch(f"/api/v1/ratings/{rating_id}", json={"score": 2, "comment": "غيَّرت رأيي"})
        assert update_resp.status_code == 200
        assert update_resp.json()["score"] == 2

        archive_resp = client.post(f"/api/v1/ratings/{rating_id}/archive")
        assert archive_resp.status_code == 200
        assert archive_resp.json()["status"] == "archived"

        second_archive = client.post(f"/api/v1/ratings/{rating_id}/archive")
        assert second_archive.status_code == 409


class TestAverageScore:

    def test_average_with_no_ratings_is_null(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "viewer@example.com")
        resp = client.get("/api/v1/ratings/target/seller/nonexistent-store/average")
        assert resp.status_code == 200
        assert resp.json()["average_score"] is None

    def test_average_computed_correctly(self, app_and_client):
        app, client = app_and_client
        buyer_email, buyer_password, pr_id = _make_fulfilled_purchase(app, client)
        client.post("/api/v1/ratings", json={
            "target_type": "seller", "target_ref_id": "avg-store", "source_purchase_request_ref_id": pr_id, "score": 4,
        })
        resp = client.get("/api/v1/ratings/target/seller/avg-store/average")
        assert resp.status_code == 200
        assert resp.json()["average_score"] == 4.0
