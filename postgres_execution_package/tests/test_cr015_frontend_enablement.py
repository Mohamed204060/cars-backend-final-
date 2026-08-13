"""
test_cr015_frontend_enablement.py — اختبارات وحدة لـCR-015 (Frontend Enablement APIs)
يغطي الـ8 Endpoints الجديدة + عضوية المحادثات الصريحة (027_com_conversation_participants.sql).

منطق كل الحالات هنا تحقَّق منه فعليًا بتنفيذ مباشر (بلا FastAPI، بيئة التأليف
بلا شبكة) قبل كتابة هذا الملف — راجع سجل الجلسة. هذا الملف يبني الطبقة
الكاملة (REST) فوق نفس المنطق المُتحقَّق منه، بنفس تشكيلة الـFixtures
المتَّبعة في بقية الاختبارات.
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
from ref_repository import InMemoryRefRepository
from vct_repository import InMemoryVctRepository
from inventory_item_api import router as inventory_router
from inventory_item_repository import InMemoryInventoryItemRepository
from idempotency_repository import InMemoryIdempotencyRepository
from scheduler_api import router as scheduler_router
from scheduler_repository import InMemorySchedulerRepository
from message_api import router as message_router
from message_repository import InMemoryMessageRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(store_router)
    app.include_router(pct_router)
    app.include_router(order_router)
    app.include_router(inventory_router)
    app.include_router(scheduler_router)
    app.include_router(message_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.store_repository = InMemoryStoreRepository()
    app.state.pct_repository = InMemoryPctRepository()
    app.state.order_repository = InMemoryOrderRepository()
    app.state.ref_repository = InMemoryRefRepository()  # CR-022: order_api.create_purchase_request يعتمد عليه الآن
    app.state.vct_repository = InMemoryVctRepository()  # Batch 1: order_api.create_purchase_request يعتمد عليه الآن
    app.state.vct_repository.seed_trim_for_testing("trim-1")
    app.state.inventory_repository = InMemoryInventoryItemRepository()
    app.state.idempotency_repository = InMemoryIdempotencyRepository()
    app.state.scheduler_repository = InMemorySchedulerRepository()
    app.state.message_repository = InMemoryMessageRepository()

    client = TestClient(app, base_url="https://testserver")
    return app, client


_TEST_PASSWORD = "Str0ngPass1!"


def _login_as(app, client, email: str, role: str = "individual_buyer") -> str:
    """إنشاء مستخدم جديد + Identity جديدة + أول تسجيل دخول له.

    لا تُستخدَم لإعادة تسجيل دخول مستخدم أُنشئ سابقًا في نفس الاختبار (بعد
    logout) — استخدم _login_existing لذلك؛ استدعاء هذه الدالة بنفس البريد
    مرتين يحاول إنشاء Identity مكرَّرة عمدًا يرفضها InMemoryAuthRepository
    (سلوك صحيح للـRepository، وليس خطأً فيه)."""
    repo = app.state.auth_repository
    user_id = repo.create_user()
    repo.set_user_role(user_id, role)
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password=_TEST_PASSWORD)
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": _TEST_PASSWORD})
    assert resp.status_code == 200
    return user_id


def _login_existing(client, email: str) -> None:
    """إعادة تسجيل دخول لمستخدم/Identity أُنشئا مسبقًا عبر _login_as في نفس
    الاختبار (عادة بعد logout) — بلا أي إنشاء مستخدم أو Identity جديدة."""
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": _TEST_PASSWORD})
    assert resp.status_code == 200


def _make_approved_part(app, client) -> str:
    """جلسة admin مستقلة، بنفس النمط المعتمَد في بقية الاختبارات."""
    _login_as(app, client, "admin-setup@example.com", role="admin")
    part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]
    resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
    assert resp.status_code == 200, resp.text
    client.post("/api/v1/auth/logout")
    return part_id


def _make_own_store(client) -> str:
    return client.post("/api/v1/store/stores", json={}).json()["id"]


class TestMyPurchaseRequests:
    """GET /purchase-requests/mine"""

    def test_scoped_to_current_buyer_only(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer1@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"})
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"})

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "buyer2@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"})

        resp = client.get("/api/v1/purchase-requests/mine")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_items"] == 1
        assert len(body["items"]) == 1

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/purchase-requests/mine")
        assert resp.status_code == 401


class TestPurchaseRequestOffers:
    """GET /purchase-requests/{prId}/offers"""

    def test_buyer_sees_all_offers(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer3@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller1@example.com")
        _make_own_store(client)
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller2@example.com")
        _make_own_store(client)
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 120.0, "currency": "SAR", "provides_shipping": True})

        client.post("/api/v1/auth/logout")
        _login_existing(client, "buyer3@example.com")
        resp = client.get(f"/api/v1/purchase-requests/{pr_id}/offers")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 2

    def test_seller_sees_only_own_offer(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer4@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller3@example.com")
        _make_own_store(client)
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller4@example.com")
        _make_own_store(client)
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 130.0, "currency": "SAR", "provides_shipping": True})

        resp = client.get(f"/api/v1/purchase-requests/{pr_id}/offers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_items"] == 1
        assert body["items"][0]["amount"] == 130.0

    def test_stranger_forbidden(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer5@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "stranger@example.com")
        resp = client.get(f"/api/v1/purchase-requests/{pr_id}/offers")
        assert resp.status_code == 403


class TestStoreListAdmin:
    """GET /store/stores"""

    def test_non_admin_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller5@example.com")
        resp = client.get("/api/v1/store/stores")
        assert resp.status_code == 403

    def test_admin_sees_all_stores(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller6@example.com")
        _make_own_store(client)
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "seller7@example.com")
        _make_own_store(client)
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "admin1@example.com", role="admin")
        resp = client.get("/api/v1/store/stores")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 2

    def test_moderator_also_allowed(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "mod1@example.com", role="moderator")
        resp = client.get("/api/v1/store/stores")
        assert resp.status_code == 200


class TestMyInventoryItems:
    """GET /inventory/items/mine"""

    def test_owner_sees_all_including_hidden(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller8@example.com")
        _make_own_store(client)

        r1 = client.post("/api/v1/inventory-items",
                          headers={"Idempotency-Key": "k-1"},
                          json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                                "pricing_mode": "contact_for_price", "quantity": 2})
        r2 = client.post("/api/v1/inventory-items",
                          headers={"Idempotency-Key": "k-2"},
                          json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                                "pricing_mode": "contact_for_price", "quantity": 1})
        item2_id = r2.json()["id"]
        client.post(f"/api/v1/inventory/items/{item2_id}/hide")

        resp = client.get("/api/v1/inventory/items/mine")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 2

    def test_non_owner_without_store_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer6@example.com")
        resp = client.get("/api/v1/inventory/items/mine")
        assert resp.status_code == 403


class TestPublicStoreInventory:
    """GET /store/stores/{storeId}/inventory-items"""

    def test_excludes_hidden_and_uses_limited_fields(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller9@example.com")
        store_id = _make_own_store(client)

        r1 = client.post("/api/v1/inventory-items",
                          headers={"Idempotency-Key": "k-3"},
                          json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                                "pricing_mode": "fixed_price", "price_amount": 50.0,
                                "price_currency": "SAR", "quantity": 10})
        r2 = client.post("/api/v1/inventory-items",
                          headers={"Idempotency-Key": "k-4"},
                          json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                                "pricing_mode": "contact_for_price", "quantity": 1})
        item2_id = r2.json()["id"]
        client.post(f"/api/v1/inventory/items/{item2_id}/hide")
        client.post("/api/v1/auth/logout")

        # لا جلسة إطلاقًا — عام بالكامل
        resp = client.get(f"/api/v1/store/stores/{store_id}/inventory-items")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_items"] == 1
        item = body["items"][0]
        assert "quantity" not in item
        assert "store_id" not in item
        assert item["price_amount"] == 50.0


class TestScheduledJobsAdmin:
    """GET /admin/scheduled-jobs"""

    def test_non_admin_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer7@example.com")
        resp = client.get("/api/v1/admin/scheduled-jobs")
        assert resp.status_code == 403

    def test_admin_lists_jobs(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", role="admin")
        client.post("/api/v1/admin/scheduled-jobs",
                     json={"job_type": "notify_expiry", "target_ref_id": "t-1",
                           "scheduled_at": "2026-09-01T00:00:00Z"})
        resp = client.get("/api/v1/admin/scheduled-jobs")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 1


class TestCatalogPartsList:
    """GET /pct/parts — dual-mode (public approved / admin-gated proposed)"""

    def test_approved_is_fully_public(self, app_and_client):
        app, client = app_and_client
        _make_approved_part(app, client)  # يعتمد ثم يسجّل خروجًا — لا جلسة بعده

        resp = client.get("/api/v1/pct/parts")  # بلا status صراحة => approved افتراضيًا
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 1

    def test_proposed_requires_session(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/pct/parts", params={"status_filter": "proposed"})
        assert resp.status_code == 401

    def test_proposed_requires_admin_role(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer8@example.com")
        resp = client.get("/api/v1/pct/parts", params={"status_filter": "proposed"})
        assert resp.status_code == 403

    def test_admin_sees_proposed(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin3@example.com", role="admin")
        client.post("/api/v1/pct/parts", json={"category_id": "cat-1"})  # يبقى proposed بلا اعتماد
        resp = client.get("/api/v1/pct/parts", params={"status_filter": "proposed"})
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 1


class TestConversationsList:
    """GET /conversations + عضوية صريحة (027_com_conversation_participants.sql)"""

    def test_lists_only_conversations_i_participate_in(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "u1@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-conv-1", "body": "مرحبًا",
        }).json()
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "u2@example.com")
        resp = client.get("/api/v1/conversations")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 0

        client.post("/api/v1/auth/logout")
        _login_existing(client, "u1@example.com")
        resp = client.get("/api/v1/conversations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_items"] == 1
        assert body["items"][0]["id"] == sent["conversation_id"]
        assert body["items"][0]["last_message"]["body_preview"] == "مرحبًا"

    def test_non_participant_cannot_list_or_delete_messages(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "u3@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-conv-2", "body": "سري",
        }).json()
        conv_id = sent["conversation_id"]
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "u4@example.com")
        list_resp = client.get(f"/api/v1/conversations/{conv_id}/messages")
        assert list_resp.status_code == 403

        delete_resp = client.delete(f"/api/v1/conversations/{conv_id}/messages/{sent['id']}")
        assert delete_resp.status_code == 403

    def test_participant_can_list_and_delete(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "u5@example.com")
        sent = client.post("/api/v1/messages", json={
            "context_type": "purchase_request", "context_ref_id": "pr-conv-3", "body": "عادي",
        }).json()

        resp = client.get(f"/api/v1/conversations/{sent['conversation_id']}/messages")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_delete_nonexistent_message_still_404_not_403(self, app_and_client):
        """راجع regression fix: NOT_FOUND يسبق FORBIDDEN دومًا."""
        app, client = app_and_client
        _login_as(app, client, "u6@example.com")
        resp = client.delete("/api/v1/conversations/some-conv/messages/ghost")
        assert resp.status_code == 404
