"""
test_ref_api.py — اختبارات وحدة لطبقة REST API للبيانات المرجعية (REF)
تشمل توليد ملف .xlsx حقيقي واختبار رفعه فعليًا عبر multipart/form-data،
لا محاكاة للتحليل.
"""

import io

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from ref_api import router as ref_router
from ref_repository import InMemoryRefRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(ref_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.ref_repository = InMemoryRefRepository()

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


def _build_xlsx(rows: list) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["code"])
    for row in rows:
        ws.append([row])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestCreateRefValue:

    def test_regular_user_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        resp = client.post("/api/v1/reference-data", json={"ref_type": "country", "code": "SA"})
        assert resp.status_code == 403

    def test_admin_can_create(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.post("/api/v1/reference-data", json={"ref_type": "country", "code": "SA"})
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    def test_invalid_ref_type_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", role="admin")
        resp = client.post("/api/v1/reference-data", json={"ref_type": "not_a_real_type", "code": "X"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_REF_TYPE"

    def test_duplicate_code_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin3@example.com", role="admin")
        client.post("/api/v1/reference-data", json={"ref_type": "country", "code": "EG"})
        resp = client.post("/api/v1/reference-data", json={"ref_type": "country", "code": "EG"})
        assert resp.status_code == 409


class TestListAndArchive:

    def test_list_only_active_values(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin4@example.com", role="admin")
        v1 = client.post("/api/v1/reference-data", json={"ref_type": "language", "code": "ar"}).json()
        client.post("/api/v1/reference-data", json={"ref_type": "language", "code": "en"})
        client.post(f"/api/v1/reference-data/{v1['id']}/archive")

        resp = client.get("/api/v1/reference-data/language")
        assert resp.status_code == 200
        codes = [v["code"] for v in resp.json()]
        assert "en" in codes and "ar" not in codes

    def test_double_archive_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin5@example.com", role="admin")
        v = client.post("/api/v1/reference-data", json={"ref_type": "fuel_type", "code": "diesel"}).json()
        client.post(f"/api/v1/reference-data/{v['id']}/archive")
        second = client.post(f"/api/v1/reference-data/{v['id']}/archive")
        assert second.status_code == 409


class TestBulkImportPreview:
    """يستخدم ملف .xlsx حقيقيًا مُولَّدًا فعليًا، لا بيانات JSON محاكاة."""

    def test_regular_user_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer2@example.com", role="individual_buyer")
        xlsx_bytes = _build_xlsx(["SA"])
        resp = client.post(
            "/api/v1/reference-data/country/bulk-import/preview",
            files={"file": ("countries.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 403

    def test_admin_preview_classifies_rows_correctly(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin6@example.com", role="admin")
        client.post("/api/v1/reference-data", json={"ref_type": "country", "code": "SA"})  # موجودة مسبقًا

        xlsx_bytes = _build_xlsx(["SA", "EG", ""])  # محدَّثة، جديدة، مرفوضة (فارغة)
        resp = client.post(
            "/api/v1/reference-data/country/bulk-import/preview",
            files={"file": ("countries.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["updated_count"] == 1
        assert body["new_count"] == 1
        assert body["rejected_count"] == 1
        assert len(body["rejected_rows"]) == 1

    def test_non_xlsx_file_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin7@example.com", role="admin")
        resp = client.post(
            "/api/v1/reference-data/country/bulk-import/preview",
            files={"file": ("data.csv", b"code\nSA", "text/csv")},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error_code"] == "UNSUPPORTED_FILE_FORMAT"
