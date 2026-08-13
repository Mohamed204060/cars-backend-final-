"""
test_inventory_item_api.py — اختبارات وحدة لطبقة REST API لعنصر مخزون البائع
مطابقة حرفيًا للعقد المعتمَد أصلًا (openapi.yaml): store_id يُشتَق من الجلسة،
Idempotency-Key مطلوب لإنشاء العنصر، الاستجابة {id, business_code, status}.

ملاحظة منهجية (تصحيح بعد فشل فعلي): اعتماد قطعة PCT يتطلب صلاحية
admin/super_admin (REQ-PCT-002)، لا صلاحية البائع. لذلك _make_approved_part
تُنفَّذ دائمًا عبر جلسة admin منفصلة ومؤقَّتة، **قبل** تسجيل دخول البائع
الفعلي صاحب الاختبار — بنفس نمط الإصلاح المطبَّق على اختبارات CMP سابقًا.
لا يجوز استدعاؤها بعد تسجيل دخول البائع، ولا الاعتماد على نجاح استدعاء
"/approve" ضمنيًا دون التحقق من صلاحية الجلسة الحالية أولاً.
"""

import itertools

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
from inventory_item_api import router as inventory_router
from inventory_item_repository import InMemoryInventoryItemRepository
from idempotency_repository import InMemoryIdempotencyRepository

_counter = itertools.count()


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(store_router)
    app.include_router(pct_router)
    app.include_router(inventory_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.store_repository = InMemoryStoreRepository()
    app.state.pct_repository = InMemoryPctRepository()
    app.state.inventory_repository = InMemoryInventoryItemRepository()
    app.state.idempotency_repository = InMemoryIdempotencyRepository()

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


def _make_approved_part(app, client) -> str:
    """
    تُنشئ وتعتمد قطعة PCT عبر جلسة admin مؤقَّتة ومستقلة تمامًا، ثم تُسجِّل
    الخروج فورًا — لا جلسة نشطة بعد إرجاعها. **يجب استدعاؤها قبل تسجيل
    دخول البائع صاحب الاختبار الفعلي**، لا بعده.
    """
    admin_email = f"admin-setup-{next(_counter)}@example.com"
    _login_as(app, client, admin_email, role="admin")

    part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]
    approve_resp = client.post(f"/api/v1/pct/parts/{part_id}/approve")
    assert approve_resp.status_code == 200, (
        f"فشل اعتماد القطعة أثناء التجهيز: {approve_resp.status_code} {approve_resp.text}"
    )

    client.post("/api/v1/auth/logout")
    return part_id


def _make_unapproved_part(client) -> str:
    """لا تحتاج جلسة admin؛ propose فقط بلا approve، تحت أي جلسة حالية."""
    return client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]


def _make_own_store(client) -> str:
    return client.post("/api/v1/store/stores", json={}).json()["id"]


def _create_item_request_body(part_id: str) -> dict:
    return {
        "catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
        "pricing_mode": "contact_for_price", "quantity": 3,
    }


class TestCreateItemMatchesApprovedContract:

    def test_create_item_requires_idempotency_key_header(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller@example.com")
        _make_own_store(client)

        resp = client.post("/api/v1/inventory-items", json=_create_item_request_body(part_id))
        assert resp.status_code == 422  # Header مطلوب (required) مفقود

    def test_create_item_response_matches_minimal_contract_shape(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller2@example.com")
        _make_own_store(client)

        resp = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": "key-abc-1"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body.keys()) == {"id", "business_code", "status"}
        assert body["status"] == "active"
        assert body["business_code"].startswith("IT-")


class TestIdempotencyReplay:
    """DD الحزمة 2، القسم 2.2: نفس المفتاح يُعيد نفس النتيجة دون تنفيذ ثانٍ."""

    def test_same_key_returns_same_result_without_duplicate_creation(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller4@example.com")
        _make_own_store(client)

        first = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": "same-key-1"},
        )
        second = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": "same-key-1"},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201
        assert first.json() == second.json()  # نفس id، لا عنصر جديد

    def test_different_key_creates_a_new_item(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller5@example.com")
        _make_own_store(client)

        first = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": "key-A"},
        )
        second = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": "key-B"},
        )
        assert first.json()["id"] != second.json()["id"]

    def test_failed_request_not_cached_can_retry_same_key(self, app_and_client):
        """طلب فاشل (قطعة غير معتمَدة) لا يُخزَّن؛ إعادة المحاولة بنفس المفتاح
        ونفس المستخدم بعد إصلاح السبب يجب أن تنجح، لا أن تُعيد فشلًا مخزَّنًا."""
        app, client = app_and_client
        seller_email, seller_password = "seller6@example.com", "Str0ngPass1!"
        _login_as(app, client, seller_email)
        _make_own_store(client)
        unapproved_part_id = _make_unapproved_part(client)

        first = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(unapproved_part_id),
            headers={"Idempotency-Key": "retry-key"},
        )
        assert first.status_code == 409

        # اعتماد القطعة عبر جلسة admin منفصلة ومؤقَّتة
        client.post("/api/v1/auth/logout")
        _login_as(app, client, "admin-fix@example.com", role="admin")
        client.post(f"/api/v1/pct/parts/{unapproved_part_id}/approve")
        client.post("/api/v1/auth/logout")

        # العودة لنفس حساب البائع بالضبط (نفس البريد وكلمة المرور => نفس
        # user_id، جلسة جديدة فقط) — ضروري لأن مفتاح Idempotency مرتبط
        # بمعرّف المستخدم؛ مستخدم مختلف لن يختبر إعادة المحاولة الحقيقية.
        client.post("/api/v1/auth/login", json={"login_identifier": seller_email, "password": seller_password})

        second = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(unapproved_part_id),
            headers={"Idempotency-Key": "retry-key"},
        )
        assert second.status_code == 201, second.text  # نجحت هذه المرة؛ الفشل الأول لم يُخزَّن


class TestStoreDerivedFromSession:

    def test_no_store_returns_403(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "nostoreuser@example.com")  # لم يُنشئ متجرًا
        part_id = "some-part"

        resp = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": "no-store-key"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "STORE_NOT_ACTIVE_OR_NOT_OWNED"

    def test_item_created_under_correct_owned_store(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller7@example.com")
        store_id = _make_own_store(client)

        item_id = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": "own-store-key"},
        ).json()["id"]

        full_item = client.get(f"/api/v1/inventory/items/{item_id}").json()
        assert full_item["store_id"] == store_id


class TestUnapprovedPartRejected:

    def test_unapproved_part_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller8@example.com")
        _make_own_store(client)
        unapproved_part_id = _make_unapproved_part(client)

        resp = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(unapproved_part_id),
            headers={"Idempotency-Key": "unapproved-key"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "PART_NOT_APPROVED"


class TestOwnershipEnforcedOnMutations:
    """REQ-STR-019: التعديل مقصور على البائع المالك فقط."""

    def _create_item_as(self, app, client, seller_email: str, key: str):
        part_id = _make_approved_part(app, client)
        _login_as(app, client, seller_email)
        _make_own_store(client)
        resp = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": key},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    def test_non_owner_cannot_update_quantity(self, app_and_client):
        app, client = app_and_client
        item_id = self._create_item_as(app, client, "owner1@example.com", "k1")

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "stranger1@example.com")
        resp = client.patch(f"/api/v1/inventory/items/{item_id}/quantity", json={"new_quantity": 10})
        assert resp.status_code == 403

    def test_owner_can_update_quantity(self, app_and_client):
        app, client = app_and_client
        item_id = self._create_item_as(app, client, "owner2@example.com", "k2")

        resp = client.patch(f"/api/v1/inventory/items/{item_id}/quantity", json={"new_quantity": 0})
        assert resp.status_code == 200
        assert resp.json()["status"] == "out_of_stock"

    def test_owner_can_archive_then_no_further_modification(self, app_and_client):
        app, client = app_and_client
        item_id = self._create_item_as(app, client, "owner3@example.com", "k3")

        archive_resp = client.post(f"/api/v1/inventory/items/{item_id}/archive")
        assert archive_resp.status_code == 200
        assert archive_resp.json()["status"] == "archived"

        resp = client.patch(f"/api/v1/inventory/items/{item_id}/quantity", json={"new_quantity": 5})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "ITEM_ARCHIVED"

    def test_owner_can_hide_then_unhide(self, app_and_client):
        app, client = app_and_client
        item_id = self._create_item_as(app, client, "owner4@example.com", "k4")

        hide_resp = client.post(f"/api/v1/inventory/items/{item_id}/hide")
        assert hide_resp.status_code == 200
        assert hide_resp.json()["status"] == "hidden"

        unhide_resp = client.post(f"/api/v1/inventory/items/{item_id}/unhide")
        assert unhide_resp.status_code == 200
        assert unhide_resp.json()["status"] in ("active", "out_of_stock")

    def test_owner_can_update_pricing(self, app_and_client):
        app, client = app_and_client
        item_id = self._create_item_as(app, client, "owner5@example.com", "k5")

        resp = client.patch(f"/api/v1/inventory/items/{item_id}/pricing",
                             json={"pricing_mode": "fixed_price", "price_amount": 250.0, "price_currency": "SAR"})
        assert resp.status_code == 200
        assert resp.json()["price_amount"] == 250.0


class TestGetItem:

    def test_get_item_no_ownership_required(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "owner6@example.com")
        _make_own_store(client)
        item_id = client.post(
            "/api/v1/inventory-items", json=_create_item_request_body(part_id),
            headers={"Idempotency-Key": "get-test-key"},
        ).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "anyone@example.com")
        resp = client.get(f"/api/v1/inventory/items/{item_id}")
        assert resp.status_code == 200

    def test_get_nonexistent_item_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "getter@example.com")
        resp = client.get("/api/v1/inventory/items/ghost")
        assert resp.status_code == 404


class TestCR017PublicInventoryItemDetail:
    """CR-017: GET /inventory/items/{itemId}/public — تفاصيل عنصر واحد
    للعموم، مسار مسطَّح (لا storeId)، بلا جلسة، يستبعد hidden/archived."""

    def test_active_item_public_detail_readable_without_session(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller13@example.com")
        _make_own_store(client)
        item_id = client.post(
            "/api/v1/inventory-items", headers={"Idempotency-Key": "k-pub-1"},
            json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                  "pricing_mode": "fixed_price", "price_amount": 75.0,
                  "price_currency": "SAR", "quantity": 2},
        ).json()["id"]
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/inventory/items/{item_id}/public")
        assert resp.status_code == 200
        body = resp.json()
        assert body["price_amount"] == 75.0
        assert "quantity" not in body
        # CR-019: store_id أصبح حقلًا معتمَدًا فعليًا في هذه الاستجابة تحديدًا
        # (لم يكن كذلك في نطاق CR-017 الأصلي الأضيق) — لا تسريب owner، ذلك
        # يبقى مستبعَدًا تمامًا (مؤكَّد أدناه في TestCR019).
        assert body["store_id"]

    def test_hidden_item_returns_404_for_public(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller14@example.com")
        _make_own_store(client)
        item_id = client.post(
            "/api/v1/inventory-items", headers={"Idempotency-Key": "k-pub-2"},
            json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                  "pricing_mode": "contact_for_price", "quantity": 1},
        ).json()["id"]
        client.post(f"/api/v1/inventory/items/{item_id}/hide")
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/inventory/items/{item_id}/public")
        assert resp.status_code == 404

    def test_nonexistent_item_returns_404(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/inventory/items/ghost/public")
        assert resp.status_code == 404


class TestCR019PublicDetailEnrichedFields:
    """CR-019: store_id/part_name/condition_code في مسار التفاصيل العام
    المخصَّص فقط (get_public_detail) — لا owner_user_ref_id، لا store_name
    وهمي، لا صورة، لا Migration."""

    def test_store_id_and_part_name_and_condition_code_present(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller15@example.com")
        store_id = _make_own_store(client)

        app.state.inventory_repository.set_part_name(part_id, "طرمبة بنزين")
        app.state.inventory_repository.set_condition_code("cond-1", "used")

        item_id = client.post(
            "/api/v1/inventory-items", headers={"Idempotency-Key": "k-cr019-1"},
            json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                  "pricing_mode": "fixed_price", "price_amount": 90.0,
                  "price_currency": "SAR", "quantity": 1},
        ).json()["id"]
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/inventory/items/{item_id}/public")
        assert resp.status_code == 200
        body = resp.json()
        assert body["store_id"] == store_id
        assert body["part_name"] == "طرمبة بنزين"
        assert body["condition_code"] == "used"

    def test_no_owner_user_ref_id_leak(self, app_and_client):
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller16@example.com")
        _make_own_store(client)
        item_id = client.post(
            "/api/v1/inventory-items", headers={"Idempotency-Key": "k-cr019-2"},
            json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                  "pricing_mode": "contact_for_price", "quantity": 1},
        ).json()["id"]
        client.post("/api/v1/auth/logout")

        body = client.get(f"/api/v1/inventory/items/{item_id}/public").json()
        assert "owner_user_ref_id" not in body
        assert "store_name" not in body
        assert "image_url" not in body

    def test_missing_part_name_or_condition_resolves_to_null_not_error(self, app_and_client):
        """لا set_part_name/set_condition_code هنا عمدًا — يحاكي غياب تطابق LEFT JOIN حقيقي."""
        app, client = app_and_client
        part_id = _make_approved_part(app, client)
        _login_as(app, client, "seller17@example.com")
        _make_own_store(client)
        item_id = client.post(
            "/api/v1/inventory-items", headers={"Idempotency-Key": "k-cr019-3"},
            json={"catalog_part_ref_id": part_id, "condition_ref_id": "cond-1",
                  "pricing_mode": "contact_for_price", "quantity": 1},
        ).json()["id"]
        client.post("/api/v1/auth/logout")

        resp = client.get(f"/api/v1/inventory/items/{item_id}/public")
        assert resp.status_code == 200
        body = resp.json()
        assert body["part_name"] is None
        assert body["condition_code"] is None
