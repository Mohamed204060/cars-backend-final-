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
from aud_repository import InMemoryAudRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(vct_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.aud_repository = InMemoryAudRepository()
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


def _make_full_chain(app, client, email: str, start_year=None, end_year=None):
    """Batch 1: manufacturer -> model -> generation (بنطاق سنوات اختياري) -> trim."""
    _login_as(app, client, email, role="admin")
    m_id = client.post("/api/v1/vct/manufacturers").json()["id"]
    client.post(f"/api/v1/vct/manufacturers/{m_id}/approve")
    model_id = client.post(f"/api/v1/vct/manufacturers/{m_id}/models").json()["id"]
    gen_id = client.post(f"/api/v1/vct/models/{model_id}/generations").json()["id"]
    if start_year is not None or end_year is not None:
        client.post(f"/api/v1/vct/generations/{gen_id}/year-range",
                    json={"start_year": start_year, "end_year": end_year})
    trim_id = client.post(f"/api/v1/vct/generations/{gen_id}/trims",
                           json={"fuel_type_ref_id": "fuel-1", "transmission_type_ref_id": "trans-1"}).json()["id"]
    return gen_id, trim_id


class TestBatch1TrimModelYears:
    """Approved VCT Design Baseline §3-4."""

    def test_year_at_exactly_start_year_accepted(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "tmy1@example.com", start_year=2018, end_year=2023)
        resp = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2018})
        assert resp.status_code == 201

    def test_year_at_exactly_end_year_accepted(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "tmy2@example.com", start_year=2018, end_year=2023)
        resp = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2023})
        assert resp.status_code == 201

    def test_year_below_start_rejected(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "tmy3@example.com", start_year=2018, end_year=2023)
        resp = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2017})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "YEAR_OUT_OF_GENERATION_RANGE"

    def test_year_above_end_rejected(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "tmy4@example.com", start_year=2018, end_year=2023)
        resp = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2024})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "YEAR_OUT_OF_GENERATION_RANGE"

    def test_start_year_only_applies_lower_bound_only(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "tmy5@example.com", start_year=2018, end_year=None)
        assert client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2050}).status_code == 201
        assert client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2017}).status_code == 409

    def test_end_year_only_applies_upper_bound_only(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "tmy6@example.com", start_year=None, end_year=2020)
        assert client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 1990}).status_code == 201
        assert client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2021}).status_code == 409

    def test_both_null_no_temporal_constraint(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "tmy7@example.com")  # بلا نطاق سنوات إطلاقًا
        assert client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 1950}).status_code == 201
        assert client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2099}).status_code == 201

    def test_duplicate_year_for_same_trim_rejected(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "tmy8@example.com")
        assert client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019}).status_code == 201
        second = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019})
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "DUPLICATE_TRIM_MODEL_YEAR"

    def test_generation_range_update_rejected_if_existing_year_falls_outside(self, app_and_client):
        """§4 الفقرة الثانية: يرفض تضييق النطاق إذا كان سيُخرج سنة موجودة فعليًا."""
        app, client = app_and_client
        gen_id, trim_id = _make_full_chain(app, client, "tmy9@example.com", start_year=2010, end_year=2025)
        client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2020})

        resp = client.post(f"/api/v1/vct/generations/{gen_id}/year-range", json={"start_year": 2021, "end_year": 2025})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "YEAR_RANGE_CONFLICTS_WITH_EXISTING_MODEL_YEARS"

    def test_generation_range_update_accepted_when_years_still_within(self, app_and_client):
        app, client = app_and_client
        gen_id, trim_id = _make_full_chain(app, client, "tmy10@example.com", start_year=2010, end_year=2025)
        client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2020})

        resp = client.post(f"/api/v1/vct/generations/{gen_id}/year-range", json={"start_year": 2015, "end_year": 2022})
        assert resp.status_code == 200


class TestBatch1MarketAvailability:
    """Approved VCT Design Baseline §6-9، 17."""

    def test_no_rows_means_global_availability(self, app_and_client):
        """§7: القراءة المباشرة عبر repository — لا Endpoint GET لحالة إتاحة سوق واحدة، الدلالة تُختبَر عبر repository مباشرة."""
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "market1@example.com")
        from vct_service import is_trim_available_in_country_via_repository
        assert is_trim_available_in_country_via_repository(
            app.state.vct_repository, country_ref_id="SA", trim_ref_id=trim_id,
        ) is True

    def test_one_row_makes_whitelist_strict(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "market2@example.com")
        resp = client.post(f"/api/v1/vct/trims/{trim_id}/market-availability", json={"country_ref_id": "SA"})
        assert resp.status_code == 201

        from vct_service import is_trim_available_in_country_via_repository
        assert is_trim_available_in_country_via_repository(
            app.state.vct_repository, country_ref_id="SA", trim_ref_id=trim_id) is True
        assert is_trim_available_in_country_via_repository(
            app.state.vct_repository, country_ref_id="AE", trim_ref_id=trim_id) is False

    def test_trim_level_then_year_specific_conflicts(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "market3@example.com")
        tmy_id = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019}).json()["id"]

        trim_level = client.post(f"/api/v1/vct/trims/{trim_id}/market-availability", json={"country_ref_id": "SA"})
        assert trim_level.status_code == 201

        conflict = client.post(f"/api/v1/vct/trim-model-years/{tmy_id}/market-availability",
                                json={"country_ref_id": "AE"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["error_code"] == "MARKET_AVAILABILITY_LEVEL_CONFLICT"

    def test_year_specific_then_trim_level_conflicts(self, app_and_client):
        app, client = app_and_client
        _, trim_id = _make_full_chain(app, client, "market4@example.com")
        tmy_id = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019}).json()["id"]

        year_level = client.post(f"/api/v1/vct/trim-model-years/{tmy_id}/market-availability",
                                  json={"country_ref_id": "SA"})
        assert year_level.status_code == 201

        conflict = client.post(f"/api/v1/vct/trims/{trim_id}/market-availability", json={"country_ref_id": "AE"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["error_code"] == "MARKET_AVAILABILITY_LEVEL_CONFLICT"

    def test_both_targets_rejected(self, app_and_client):
        app, client = app_and_client
        from vct_service import add_market_availability_via_repository, InvalidMarketAvailabilityTargetError
        _, trim_id = _make_full_chain(app, client, "market5@example.com")
        tmy_id = client.post(f"/api/v1/vct/trims/{trim_id}/model-years", json={"year": 2019}).json()["id"]
        with pytest.raises(InvalidMarketAvailabilityTargetError):
            add_market_availability_via_repository(
                app.state.vct_repository, country_ref_id="SA", trim_ref_id=trim_id, trim_model_year_ref_id=tmy_id,
            )

    def test_neither_target_rejected(self, app_and_client):
        app, client = app_and_client
        from vct_service import add_market_availability_via_repository, InvalidMarketAvailabilityTargetError
        with pytest.raises(InvalidMarketAvailabilityTargetError):
            add_market_availability_via_repository(app.state.vct_repository, country_ref_id="SA")
