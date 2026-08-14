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
from ref_repository import InMemoryRefRepository
from vct_repository import InMemoryVctRepository
from ref_service import RefValue


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
    app.state.ref_repository = InMemoryRefRepository()
    app.state.vct_repository = InMemoryVctRepository()
    for trim_id in ("trim-1", "trim-x", "trim-unknown"):
        app.state.vct_repository.seed_trim_for_testing(trim_id)

    client = TestClient(app, base_url="https://testserver")
    return app, client


def _make_ref_value(app, ref_type: str, code: str, status: str = "active") -> str:
    """CR-022: يُدرِج قيمة مرجعية مباشرة في المستودع الوهمي (لا Endpoint إنشاء متاح هنا خارج نطاق CR-022)."""
    repo = app.state.ref_repository
    value = repo.insert_value(RefValue(id="", ref_type=ref_type, code=code, status=status))
    return value.id


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
        """
        يثبت أن GET /purchase-requests/mine الأساسي لم يتأثر بـRead Model
        CR-021 (part_name/manufacturer_name تبقى غائبة، هذا العقد بلا JOINs
        ولا حقول Display Projection إضافية تتسرَّب إليه).

        تحديث CR-022 (2026-08): مجموعة المفاتيح المتوقَّعة توسَّعت إضافيًا
        بحقلي condition_ref_id/notes — نفس PurchaseRequestResponse يُستخدَم
        فعليًا لكل مسارات PR (إنشاء/عرض/قائمة)، وهذان عمودان حقيقيان في
        الجدول (لا Read Model منفصل كحالة CR-021)؛ إضافة حقلين Nullable لا
        تكسر أي مستهلك حالي (توافق خلفي قياسي لإضافات JSON).

        تحديث Batch 1: مجموعة المفاتيح توسَّعت مجددًا بحقل
        trim_model_year_ref_id — بنفس مبدأ condition_ref_id/notes أعلاه
        حرفيًا: عمود Domain حقيقي على pur.purchase_requests (Migration 031،
        Approved VCT Design Baseline §23)، لا حقل Display Projection محلول
        عبر JOIN. الفرق الجوهري الذي يبقى هذا الاختبار يحرسه دون تغيير:
        part_name/manufacturer_name (ومثيلاتهما من CR-021) تبقيان غائبتين
        عن هذا العقد الأساسي دائمًا — تلك حقول Read Model منفصلة تمامًا
        (GET /purchase-requests/mine/display)، لا تُخلَط بعقد /mine الخام.
        """
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr021-5@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-x"})

        resp = client.get("/api/v1/purchase-requests/mine")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert "part_name" not in item
        assert "manufacturer_name" not in item
        assert set(item.keys()) == {
            "id", "business_code", "buyer_user_ref_id", "catalog_part_ref_id", "trim_ref_id", "status",
            "condition_ref_id", "notes", "trim_model_year_ref_id",
        }
        assert item["condition_ref_id"] is None
        assert item["notes"] is None
        assert item["trim_model_year_ref_id"] is None

    def test_no_internal_or_other_user_data_leak(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr021-6@example.com")
        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-x"})

        body_text = client.get("/api/v1/purchase-requests/mine/display").text
        assert "buyer_user_ref_id" not in body_text
        assert "password" not in body_text.lower()


class TestCR022PurchaseRequestConditionAndNotes:
    """CR-022 — Purchase Request Condition & Buyer Notes (النطاق المعتمَد حرفيًا فقط)."""

    def test_condition_ref_id_null_accepted_as_no_preference(self, app_and_client):
        """
        Backward Compatibility: إنشاء Purchase Request بدون condition_ref_id
        (ولا notes) يعمل تمامًا كسلوك ما قبل CR-022 — نفس الحقول الأصلية
        بلا تغيير، مع NULL/NULL الصريحين على الحقلين الجديدين فقط.
        """
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr022-1@example.com")

        resp = client.post("/api/v1/purchase-requests",
                            json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "open"
        assert body["business_code"].startswith("PR-")
        assert body["catalog_part_ref_id"] == part_id
        assert body["trim_ref_id"] == "trim-1"
        assert body["condition_ref_id"] is None
        assert body["notes"] is None

    def test_valid_part_condition_ref_id_accepted(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        condition_id = _make_ref_value(app, "part_condition", "new")
        _login_as(app, client, "buyer-cr022-2@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": "trim-1", "condition_ref_id": condition_id,
        })
        assert resp.status_code == 201
        assert resp.json()["condition_ref_id"] == condition_id

    def test_ref_id_from_other_ref_type_rejected(self, app_and_client):
        """لا يكفي وجود UUID من نوع مرجعي آخر (مثال: fuel_type)."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        wrong_type_id = _make_ref_value(app, "fuel_type", "petrol")
        _login_as(app, client, "buyer-cr022-3@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": "trim-1", "condition_ref_id": wrong_type_id,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_CONDITION_REF"

    def test_nonexistent_condition_ref_id_rejected(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr022-4@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": "trim-1", "condition_ref_id": str(__import__("uuid").uuid4()),
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_CONDITION_REF"

    def test_archived_condition_ref_id_rejected(self, app_and_client):
        """status='active' هو نفس دلالة 'قابل للاستخدام' المعتمَدة أصلًا (get_values_for_type)."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        archived_id = _make_ref_value(app, "part_condition", "obsolete", status="archived")
        _login_as(app, client, "buyer-cr022-5@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": "trim-1", "condition_ref_id": archived_id,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_CONDITION_REF"

    def test_notes_absent_accepted(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr022-6@example.com")

        resp = client.post("/api/v1/purchase-requests",
                            json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1"})
        assert resp.status_code == 201
        assert resp.json()["notes"] is None

    def test_notes_at_exactly_2000_chars_accepted(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr022-7@example.com")
        notes = "أ" * 2000

        resp = client.post("/api/v1/purchase-requests",
                            json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1", "notes": notes})
        assert resp.status_code == 201
        assert resp.json()["notes"] == notes
        assert len(resp.json()["notes"]) == 2000

    def test_notes_over_2000_chars_rejected(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-cr022-8@example.com")
        notes = "أ" * 2001

        resp = client.post("/api/v1/purchase-requests",
                            json={"catalog_part_ref_id": part_id, "trim_ref_id": "trim-1", "notes": notes})
        # يُرفَض عند طبقة تحقق Pydantic (max_length) قبل الوصول لطبقة الخدمة
        assert resp.status_code == 422

    def test_condition_and_notes_visible_on_mine_list_round_trip(self, app_and_client):
        """
        القيمتان تُخزَّنان وتُعادان فعليًا على GET /purchase-requests/mine
        (نفس PurchaseRequestResponse)، لا فقط على استجابة الإنشاء المباشرة.
        """
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        condition_id = _make_ref_value(app, "part_condition", "used")
        _login_as(app, client, "buyer-cr022-9@example.com")
        client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": "trim-1",
            "condition_ref_id": condition_id, "notes": "ملاحظة",
        })

        resp = client.get("/api/v1/purchase-requests/mine")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["condition_ref_id"] == condition_id
        assert item["notes"] == "ملاحظة"


class TestBatch1ExtendedDisplayProjection:
    """Batch 1: توسيع CR-021 Display Projection بـtrim_name/generation_name/model_year/condition_code/notes."""

    def test_trim_generation_year_condition_notes_all_resolved(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("ext-trim-1").id
        condition_id = _make_ref_value(app, "part_condition", "new")
        _login_as(app, client, "buyer-ext1@example.com")

        app.state.order_repository.set_part_name(part_id, "فلتر زيت")
        app.state.order_repository.set_trim_vehicle_info(
            trim_id, "model-1", "كامري", "mfr-1", "تويوتا",
            generation_id="gen-1", generation_name="الجيل الثامن", trim_name="SE",
        )
        app.state.order_repository.set_condition_code(condition_id, "new")

        client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id,
            "condition_ref_id": condition_id, "notes": "أحتاج قطعة أصلية",
        })

        item = client.get("/api/v1/purchase-requests/mine/display").json()["items"][0]
        assert item["trim_name"] == "SE"
        assert item["generation_name"] == "الجيل الثامن"
        assert item["condition_code"] == "new"
        assert item["notes"] == "أحتاج قطعة أصلية"

    def test_model_year_resolved_when_trim_model_year_ref_id_present(self, app_and_client):
        app, client = app_and_client
        from vct_service import TrimModelYear
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("ext-trim-2").id
        tmy = app.state.vct_repository.insert_trim_model_year(TrimModelYear(id="", trim_ref_id=trim_id, year=2019))
        _login_as(app, client, "buyer-ext2@example.com")
        app.state.order_repository.set_trim_model_year(tmy.id, 2019)

        client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "trim_model_year_ref_id": tmy.id,
        })

        item = client.get("/api/v1/purchase-requests/mine/display").json()["items"][0]
        assert item["model_year"] == 2019
        assert item["trim_model_year_ref_id"] == tmy.id

    def test_null_safe_when_nothing_resolved(self, app_and_client):
        """Regression: سجل بلا أي توطين (لا اسم قطعة، لا معلومات سيارة، لا سنة، لا حالة) — كل الحقول الجديدة None بلا خطأ."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("ext-trim-3").id
        _login_as(app, client, "buyer-ext3@example.com")

        client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})

        resp = client.get("/api/v1/purchase-requests/mine/display")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["trim_name"] is None
        assert item["generation_name"] is None
        assert item["model_year"] is None
        assert item["condition_code"] is None
        assert item["notes"] is None


class TestBatch1OfferDisplayIntegration:
    """Batch 1 — Offers Integration: GET /purchase-requests/{prId}/offers/display."""

    def test_buyer_sees_offer_with_resolved_pr_context(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("offer-trim-1").id
        app.state.order_repository.set_part_name(part_id, "طرمبة بنزين")
        app.state.order_repository.set_trim_vehicle_info(
            trim_id, "model-1", "كامري", "mfr-1", "تويوتا", trim_name="SE",
        )
        buyer_id = _login_as(app, client, "buyer-offerdisp1@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "notes": "بسرعة من فضلك"}
                             ).json()["id"]
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "seller-offerdisp1@example.com")
        client.post("/api/v1/store/stores", json={})
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 200.0, "currency": "SAR", "provides_shipping": True, "notes": "متوفرة فورًا"})
        client.post("/api/v1/auth/logout")

        client.post("/api/v1/auth/login",
                    json={"login_identifier": "buyer-offerdisp1@example.com", "password": "Str0ngPass1!"})
        resp = client.get(f"/api/v1/purchase-requests/{pr_id}/offers/display")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["part_name"] == "طرمبة بنزين"
        assert item["trim_name"] == "SE"
        assert item["manufacturer_name"] == "تويوتا"
        assert item["buyer_notes"] == "بسرعة من فضلك"
        assert item["seller_notes"] == "متوفرة فورًا"
        assert item["purchase_request_status"] in ("open", "under_review")

    def test_seller_sees_only_own_offer_in_display(self, app_and_client):
        """Scoping: نفس منطق GET /offers الأساسي — البائع يرى عرضه فقط."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("offer-trim-2").id
        _login_as(app, client, "buyer-offerdisp2@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "seller-a-offerdisp2@example.com")
        client.post("/api/v1/store/stores", json={})
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 100.0, "currency": "SAR", "provides_shipping": False})
        client.post("/api/v1/auth/logout")

        _login_as(app, client, "seller-b-offerdisp2@example.com")
        client.post("/api/v1/store/stores", json={})
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 150.0, "currency": "SAR", "provides_shipping": False})

        resp = client.get(f"/api/v1/purchase-requests/{pr_id}/offers/display")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1
        assert resp.json()["items"][0]["amount"] == 150.0

    def test_no_raw_ids_when_names_resolved(self, app_and_client):
        """لا يعرض trim_ref_id/manufacturer_id كبديل عن الاسم عند توفره — الاسم موجود، فلا حاجة للمعرّف الخام في هذا العقد."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("offer-trim-3").id
        app.state.order_repository.set_trim_vehicle_info(trim_id, "m", "كامري", "mf", "تويوتا", trim_name="LE")
        _login_as(app, client, "buyer-offerdisp3@example.com")
        pr_id = client.post("/api/v1/purchase-requests",
                             json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]
        client.post("/api/v1/auth/logout")
        _login_as(app, client, "seller-offerdisp3@example.com")
        client.post("/api/v1/store/stores", json={})
        client.post(f"/api/v1/purchase-requests/{pr_id}/offers",
                    json={"amount": 90.0, "currency": "SAR", "provides_shipping": False})

        item = client.get(f"/api/v1/purchase-requests/{pr_id}/offers/display").json()["items"][0]
        assert "trim_ref_id" not in item
        assert "manufacturer_id" not in item
        assert item["trim_name"] == "LE"


class TestBatch1PurchaseRequestVctIntegration:
    """Approved VCT Design Baseline §23: Purchase Request مرتبط بـVCT الحقيقي."""

    def test_valid_trim_accepted(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("valid-trim-1").id
        _login_as(app, client, "buyer-b1pr-1@example.com")

        resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201
        assert resp.json()["trim_ref_id"] == trim_id
        assert resp.json()["trim_model_year_ref_id"] is None

    def test_nonexistent_trim_rejected(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "buyer-b1pr-2@example.com")

        resp = client.post("/api/v1/purchase-requests",
                            json={"catalog_part_ref_id": part_id, "trim_ref_id": "ghost-trim-does-not-exist"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "TRIM_NOT_FOUND"

    def test_valid_trim_model_year_belonging_to_same_trim_accepted(self, app_and_client):
        app, client = app_and_client
        from vct_service import TrimModelYear
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("valid-trim-2").id
        tmy = app.state.vct_repository.insert_trim_model_year(TrimModelYear(id="", trim_ref_id=trim_id, year=2019))
        _login_as(app, client, "buyer-b1pr-3@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "trim_model_year_ref_id": tmy.id,
        })
        assert resp.status_code == 201
        assert resp.json()["trim_model_year_ref_id"] == tmy.id

    def test_year_belonging_to_different_trim_rejected(self, app_and_client):
        """سنة موجودة فعليًا لكن تابعة لفئة أخرى — يجب رفضها، لا قبولها لمجرد وجودها في مكان ما."""
        app, client = app_and_client
        from vct_service import TrimModelYear
        part_id = _make_approved_part(app, client)
        trim_a = app.state.vct_repository.seed_trim_for_testing("trim-a-b1pr").id
        trim_b = app.state.vct_repository.seed_trim_for_testing("trim-b-b1pr").id
        tmy_of_b = app.state.vct_repository.insert_trim_model_year(TrimModelYear(id="", trim_ref_id=trim_b, year=2020))
        _login_as(app, client, "buyer-b1pr-4@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_a, "trim_model_year_ref_id": tmy_of_b.id,
        })
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "TRIM_MODEL_YEAR_NOT_FOUND"

    def test_nonexistent_trim_model_year_rejected(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("valid-trim-3").id
        _login_as(app, client, "buyer-b1pr-5@example.com")

        resp = client.post("/api/v1/purchase-requests", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "trim_model_year_ref_id": "ghost-tmy",
        })
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "TRIM_MODEL_YEAR_NOT_FOUND"

    def test_creation_without_year_still_works(self, app_and_client):
        """Backward Compatibility: trim_model_year_ref_id اختياري تمامًا — إنشاء بدون سنة يعمل كسلوك ما قبل هذه الدفعة."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("valid-trim-4").id
        _login_as(app, client, "buyer-b1pr-6@example.com")

        resp = client.post("/api/v1/purchase-requests", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201
        assert resp.json()["trim_model_year_ref_id"] is None

    def test_regression_old_records_have_null_trim_model_year(self, app_and_client):
        """Regression: طلبات أُنشئت قبل Migration 031 (لا مجال لتحديد سنة) تبقى NULL/تعمل بلا كسر."""
        app, client = app_and_client
        from order_service import PurchaseRequest
        part_id = _make_approved_part(app, client)
        trim_id = app.state.vct_repository.seed_trim_for_testing("valid-trim-5").id
        buyer_id = _login_as(app, client, "buyer-b1pr-7@example.com")

        # يحاكي سجلًا تاريخيًا أُدرِج مباشرة عبر Repository (كما لو كان قبل هذه الدفعة)، بلا trim_model_year_ref_id إطلاقًا
        old_style_pr = PurchaseRequest(id="", buyer_user_ref_id=buyer_id, catalog_part_ref_id=part_id, trim_ref_id=trim_id)
        app.state.order_repository.insert_purchase_request(old_style_pr)

        resp = client.get("/api/v1/purchase-requests/mine")
        assert resp.status_code == 200
        matching = [i for i in resp.json()["items"] if i["id"] == old_style_pr.id]
        assert len(matching) == 1
        assert matching[0]["trim_model_year_ref_id"] is None
