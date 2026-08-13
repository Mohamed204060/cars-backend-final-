"""
test_cmp_api.py — اختبارات وحدة لطبقة REST API لخدمة CMP
تستخدم مستودعات PCT/VCT الحقيقية في الذاكرة (لا محاكاة للتحقق نفسه)، تمامًا
كما يستهلكها CMP فعليًا في الإنتاج عبر حقن الاعتمادية (SSOT).
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
from vct_api import router as vct_router
from vct_repository import InMemoryVctRepository
from cmp_api import router as cmp_router
from cmp_repository import InMemoryCmpRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(pct_router)
    app.include_router(vct_router)
    app.include_router(cmp_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.pct_repository = InMemoryPctRepository()
    app.state.vct_repository = InMemoryVctRepository()
    app.state.cmp_repository = InMemoryCmpRepository(
        trim_model_year_resolver=app.state.vct_repository.get_trim_ref_id_for_trim_model_year
    )

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


def _make_approved_part(client) -> str:
    """يستخدم PCT REST API الفعلي لإنشاء قطعة معتمَدة (السلسلة الحقيقية، لا Fixture منفصل)."""
    part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]
    client.post(f"/api/v1/pct/parts/{part_id}/approve")
    return part_id


def _make_valid_trim(client) -> str:
    """يستخدم VCT REST API الفعلي لإنشاء سلسلة manufacturer->model->generation->trim كاملة."""
    m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
    client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
    model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
    gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]
    trim_id = client.post(f"/api/v1/vct/generations/{gen_id}/trims",
                           json={"fuel_type_ref_id": "fuel-1", "transmission_type_ref_id": "trans-1"}).json()["id"]
    return trim_id


class TestCreateRecordAuthorization:
    """REQ-CMP-001: مدير النظام حصريًا."""

    def test_regular_user_forbidden(self, app_and_client):
        app, client = app_and_client
        # 1-3: تجهيز البيانات (قطعة معتمَدة + فئة سيارة صالحة) بصلاحية admin،
        # لأن الاعتماد نفسه (PCT approve وVCT manufacturer approve) يتطلب
        # admin/super_admin أيضًا — لا يمكن تجهيز بيانات صالحة بحساب buyer.
        _login_as(app, client, "admin-setup@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)

        # 4: إنهاء جلسة admin صراحة قبل التبديل
        client.post("/api/v1/auth/logout")

        # 5: تسجيل الدخول بحساب عادي منفصل
        _login_as(app, client, "buyer@example.com", role="individual_buyer")

        # 6-7: محاولة إنشاء سجل CMP بحساب buyer، بالبيانات الجاهزة مسبقًا فقط
        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "FORBIDDEN"

    def test_admin_can_create(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)

        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"


class TestSsotValidation:
    """يثبت أن CMP تستهلك PCT/VCT فعليًا عبر الحقن، لا بمعزل عنهما (REQ-CMP-001)."""

    def test_unapproved_part_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", role="admin")
        part_id = client.post("/api/v1/pct/parts", json={"category_id": "cat-1"}).json()["id"]  # لم تُعتمَد
        trim_id = _make_valid_trim(client)

        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "PART_NOT_APPROVED"

    def test_invalid_trim_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin3@example.com", role="admin")
        part_id = _make_approved_part(client)

        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": "ghost-trim"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "TRIM_NOT_VALID"


class TestDuplicatePrevention:
    """REQ-CMP-002."""

    def test_duplicate_pair_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin4@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)

        first = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert first.status_code == 201
        second = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "DUPLICATE_COMPATIBILITY_RECORD"


class TestArchive:
    """REQ-CMP-003: مدير النظام حصريًا."""

    def test_regular_user_cannot_archive(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin5@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        record_id = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]

        _login_as(app, client, "notadmin@example.com", role="individual_buyer")
        resp = client.post(f"/api/v1/cmp/records/{record_id}/archive")
        assert resp.status_code == 403

    def test_admin_can_archive(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin6@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        record_id = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]

        resp = client.post(f"/api/v1/cmp/records/{record_id}/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    def test_double_archive_returns_409(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin7@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        record_id = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]

        client.post(f"/api/v1/cmp/records/{record_id}/archive")
        second = client.post(f"/api/v1/cmp/records/{record_id}/archive")
        assert second.status_code == 409

    def test_archive_nonexistent_record_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin8@example.com", role="admin")
        resp = client.post("/api/v1/cmp/records/ghost/archive")
        assert resp.status_code == 404


class TestGetAndListRecords:

    def test_get_record_by_id(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin9@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        record_id = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id}).json()["id"]

        resp = client.get(f"/api/v1/cmp/records/{record_id}")
        assert resp.status_code == 200

    def test_get_nonexistent_record_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin10@example.com", role="admin")
        resp = client.get("/api/v1/cmp/records/ghost")
        assert resp.status_code == 404

    def test_list_records_for_part(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin11@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})

        resp = client.get(f"/api/v1/cmp/parts/{part_id}/records")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestBatch1YearSpecificCompatibility:
    """Approved VCT Design Baseline §10-17: General/Year-specific Compatibility."""

    def _make_trim_model_year(self, client, trim_id: str, year: int) -> str:
        return client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": year}).json()["id"]

    def test_general_compatibility_regression_unchanged(self, app_and_client):
        """Regression: نفس السلوك القديم حرفيًا — trim_ref_id فقط، بلا trim_model_year_ref_id."""
        app, client = app_and_client
        _login_as(app, client, "admin-b1-1@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)

        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert resp.status_code == 201
        body = resp.json()
        assert body["trim_ref_id"] == trim_id
        assert body["trim_model_year_ref_id"] is None

    def test_year_specific_compatibility_created(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin-b1-2@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        tmy_id = self._make_trim_model_year(client, trim_id, 2019)

        resp = client.post("/api/v1/cmp/records",
                            json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_id})
        assert resp.status_code == 201
        body = resp.json()
        assert body["trim_ref_id"] is None
        assert body["trim_model_year_ref_id"] == tmy_id

    def test_both_targets_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin-b1-3@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        tmy_id = self._make_trim_model_year(client, trim_id, 2019)

        resp = client.post("/api/v1/cmp/records", json={
            "catalog_part_ref_id": part_id, "trim_ref_id": trim_id, "trim_model_year_ref_id": tmy_id,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_COMPATIBILITY_TARGET"

    def test_neither_target_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin-b1-4@example.com", role="admin")
        part_id = _make_approved_part(client)

        resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_COMPATIBILITY_TARGET"

    def test_general_then_year_specific_for_same_trim_conflicts(self, app_and_client):
        """§13: منع تعايش General مع Year-specific لنفس (قطعة، فئة)."""
        app, client = app_and_client
        _login_as(app, client, "admin-b1-5@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        tmy_id = self._make_trim_model_year(client, trim_id, 2019)

        general_resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert general_resp.status_code == 201

        conflict_resp = client.post("/api/v1/cmp/records",
                                     json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_id})
        assert conflict_resp.status_code == 409
        assert conflict_resp.json()["detail"]["error_code"] == "COMPATIBILITY_LEVEL_CONFLICT"

    def test_year_specific_then_general_for_same_trim_conflicts(self, app_and_client):
        """§13: الاتجاه المعاكس — Year-specific أولًا ثم General لنفس الفئة."""
        app, client = app_and_client
        _login_as(app, client, "admin-b1-6@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        tmy_id = self._make_trim_model_year(client, trim_id, 2019)

        year_resp = client.post("/api/v1/cmp/records",
                                 json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_id})
        assert year_resp.status_code == 201

        conflict_resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_id})
        assert conflict_resp.status_code == 409
        assert conflict_resp.json()["detail"]["error_code"] == "COMPATIBILITY_LEVEL_CONFLICT"

    def test_multiple_year_specific_records_for_different_years_allowed(self, app_and_client):
        """§12: القطعة تناسب 2018-2020 فقط → ثلاثة سجلات Year-specific، بلا تعارض بينها."""
        app, client = app_and_client
        _login_as(app, client, "admin-b1-7@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        tmy_2018 = self._make_trim_model_year(client, trim_id, 2018)
        tmy_2019 = self._make_trim_model_year(client, trim_id, 2019)
        tmy_2020 = self._make_trim_model_year(client, trim_id, 2020)

        for tmy in (tmy_2018, tmy_2019, tmy_2020):
            resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy})
            assert resp.status_code == 201

        resp = client.get(f"/api/v1/cmp/parts/{part_id}/records")
        assert len(resp.json()) == 3

    def test_duplicate_exact_year_specific_target_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin-b1-8@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_id = _make_valid_trim(client)
        tmy_id = self._make_trim_model_year(client, trim_id, 2019)

        first = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_id})
        assert first.status_code == 201
        second = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_id})
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "DUPLICATE_COMPATIBILITY_RECORD"

    def test_nonexistent_trim_model_year_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin-b1-9@example.com", role="admin")
        part_id = _make_approved_part(client)

        resp = client.post("/api/v1/cmp/records",
                            json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": "ghost-tmy"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "TRIM_MODEL_YEAR_NOT_FOUND"

    def test_different_trims_do_not_conflict(self, app_and_client):
        """Sanity: تعايش General لفئة وYear-specific لفئة أخرى مختلفة تمامًا — لا تعارض."""
        app, client = app_and_client
        _login_as(app, client, "admin-b1-10@example.com", role="admin")
        part_id = _make_approved_part(client)
        trim_a = _make_valid_trim(client)
        trim_b = _make_valid_trim(client)
        tmy_b = self._make_trim_model_year(client, trim_b, 2019)

        general_resp = client.post("/api/v1/cmp/records", json={"catalog_part_ref_id": part_id, "trim_ref_id": trim_a})
        assert general_resp.status_code == 201
        year_resp = client.post("/api/v1/cmp/records",
                                 json={"catalog_part_ref_id": part_id, "trim_model_year_ref_id": tmy_b})
        assert year_resp.status_code == 201
