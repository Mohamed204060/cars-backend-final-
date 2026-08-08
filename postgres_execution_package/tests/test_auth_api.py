"""
test_auth_api.py — اختبارات وحدة لطبقة REST API لخدمة Auth
تستخدم InMemoryAuthRepository وInMemorySessionRepository (لا اتصال قاعدة
بيانات حقيقي هنا)؛ اختبارات التكامل الحقيقية على PostgreSQL منفصلة
(tests/test_postgres_auth_sessions_integration.py) وتُشغَّل عبر GitHub Actions.

يتطلب: fastapi, httpx (لا تتوفر في بيئة الإعداد الحالية بلا اتصال شبكة؛
مُتحقَّق من منطق العمل المكافئ يدويًا دون هذه الاعتماديات — انظر ملخص
التسليم لتفاصيل التحقق البديل الذي أُجري فعليًا).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)

    providers = [
        IdentityProvider(code="email_password", display_name="البريد وكلمة المرور", category="password", is_enabled=True),
        IdentityProvider(code="phone_otp", display_name="الهاتف", category="otp", is_enabled=False),
    ]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()

    # base_url على https إلزامي هنا: الجلسة تُصدَر بخاصية Secure=True (CR-013،
    # لا نُعطِّلها في الاختبارات ولا في التطبيق)، وSecure Cookies لا تُرسَل من
    # المتصفح/العميل إلا فوق HTTPS. الـTestClient الافتراضي على http://testserver
    # كان يُنشئ الجلسة بنجاح لكن لا يُعيد إرسال الـCookie أبدًا في الطلبات
    # التالية، فتظهر كل المسارات المحمية 401 رغم أن تسجيل الدخول نجح فعليًا.
    client = TestClient(app, base_url="https://testserver")
    return app, client


def _seed_password_identity(app, email: str, raw_password: str) -> str:
    """أداة اختبار: تُنشئ حسابًا ووسيلة هوية email_password مباشرة عبر
    المستودع (لا يوجد Endpoint تسجيل/Signup عام بعد ضمن نطاق هذا التسليم)."""
    repo = app.state.auth_repository
    user_id = repo.create_user()
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password=raw_password)
    return user_id


def _login(client, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"login_identifier": email, "password": password})


class TestLoginRequiresValidPasswordCredential:
    """تعديل CR-013 v2: لا إنشاء حساب تلقائيًا؛ تحقق حقيقي من كلمة المرور."""

    def test_login_with_correct_password_sets_httponly_secure_cookie(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "buyer@example.com", "CorrectHorseBattery1!")

        resp = client.post("/api/v1/auth/login", json={"login_identifier": "buyer@example.com", "password": "CorrectHorseBattery1!"})
        assert resp.status_code == 200
        assert "session_id" not in resp.json()  # CR-013: لا توكن خام في الجسم إطلاقًا
        cookie_header = resp.headers.get("set-cookie", "")
        assert "session_id=" in cookie_header
        assert "httponly" in cookie_header.lower()
        assert "samesite=lax" in cookie_header.lower()

    def test_login_with_wrong_password_rejected_401(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "buyer2@example.com", "CorrectHorseBattery1!")

        resp = client.post("/api/v1/auth/login", json={"login_identifier": "buyer2@example.com", "password": "WrongPassword"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error_code"] == "INVALID_CREDENTIALS"

    def test_login_with_nonexistent_account_rejected_with_same_generic_message(self, app_and_client):
        """يثبت عدم كشف وجود الحساب: نفس error_code ونفس الرسالة تمامًا لحساب غير موجود."""
        _, client = app_and_client
        resp = client.post("/api/v1/auth/login", json={"login_identifier": "never-registered@example.com", "password": "whatever"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error_code"] == "INVALID_CREDENTIALS"

    def test_login_does_not_auto_create_account(self, app_and_client):
        """يثبت أن محاولة دخول فاشلة لا تُنشئ حسابًا جانبيًا (خلافًا للسلوك القديم)."""
        app, client = app_and_client
        resp = client.post("/api/v1/auth/login", json={"login_identifier": "should-not-exist@example.com", "password": "x"})
        assert resp.status_code == 401
        assert len(app.state.auth_repository._identities) == 0

    def test_password_or_hash_never_appear_in_any_response_body(self, app_and_client):
        app, client = app_and_client
        password = "CorrectHorseBattery1!"
        _seed_password_identity(app, "leak-check@example.com", password)

        resp = client.post("/api/v1/auth/login", json={"login_identifier": "leak-check@example.com", "password": password})
        body_text = resp.text
        assert password not in body_text
        for stored_hash in app.state.auth_repository._credential_hashes.values():
            assert stored_hash not in body_text


class TestProtectedEndpointsRequireSession:

    def test_identities_list_without_cookie_returns_401(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/auth/identities")
        assert resp.status_code == 401
        assert resp.json()["detail"]["error_code"] == "NO_SESSION"

    def test_logout_without_cookie_returns_401(self, app_and_client):
        _, client = app_and_client
        resp = client.post("/api/v1/auth/logout")
        assert resp.status_code == 401


class TestFullSessionLifecycle:

    def test_login_then_list_identities_then_logout_then_rejected(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "flow@example.com", "CorrectHorseBattery1!")

        login_resp = _login(client, "flow@example.com", "CorrectHorseBattery1!")
        assert login_resp.status_code == 200

        list_resp = client.get("/api/v1/auth/identities")
        assert list_resp.status_code == 200
        assert len(list_resp.json()["identities"]) == 1  # وسيلة email_password المزروعة مسبقًا

        logout_resp = client.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json()["status"] == "logged_out"

        # REQ-SEC-005: نفس الجلسة (حتى لو ما زالت الكوكي محليًا) تُرفَض فورًا بعد الإبطال
        after_logout = client.get("/api/v1/auth/identities")
        assert after_logout.status_code == 401
        assert after_logout.json()["detail"]["error_code"] in ("SESSION_REVOKED", "NO_SESSION")


class TestIdentityManagement:

    def test_add_identity_success(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "adder@example.com", "CorrectHorseBattery1!")
        _login(client, "adder@example.com", "CorrectHorseBattery1!")

        resp = client.post("/api/v1/auth/identities", json={
            "provider_code": "email_password", "external_identifier": "adder-alt@example.com", "is_verified": False,
        })
        assert resp.status_code == 201
        assert resp.json()["provider_code"] == "email_password"

    def test_add_disabled_provider_rejected(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "disabled-test@example.com", "CorrectHorseBattery1!")
        _login(client, "disabled-test@example.com", "CorrectHorseBattery1!")

        resp = client.post("/api/v1/auth/identities", json={
            "provider_code": "phone_otp", "external_identifier": "+966500000000", "is_verified": False,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "PROVIDER_DISABLED"

    def test_remove_last_identity_rejected_with_409(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "onlyone@example.com", "CorrectHorseBattery1!")
        _login(client, "onlyone@example.com", "CorrectHorseBattery1!")

        identities = client.get("/api/v1/auth/identities").json()["identities"]
        only_id = identities[0]["id"]

        resp = client.delete(f"/api/v1/auth/identities/{only_id}")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "LAST_IDENTITY"

    def test_remove_one_of_two_identities_succeeds(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "twoidentities@example.com", "CorrectHorseBattery1!")
        _login(client, "twoidentities@example.com", "CorrectHorseBattery1!")
        client.post("/api/v1/auth/identities", json={
            "provider_code": "email_password", "external_identifier": "twoidentities-alt@example.com", "is_verified": True,
        })

        identities = client.get("/api/v1/auth/identities").json()["identities"]
        assert len(identities) == 2

        resp = client.delete(f"/api/v1/auth/identities/{identities[0]['id']}")
        assert resp.status_code == 204

        remaining = client.get("/api/v1/auth/identities").json()["identities"]
        assert len(remaining) == 1


class TestPublicProvidersEndpoint:

    def test_providers_no_auth_required(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/auth/providers")
        assert resp.status_code == 200

    def test_disabled_provider_not_listed(self, app_and_client):
        _, client = app_and_client
        codes = [p["code"] for p in client.get("/api/v1/auth/providers").json()["providers"]]
        assert "email_password" in codes
        assert "phone_otp" not in codes  # REQ-IAM-013: غير مفعَّل، يجب ألا يظهر


class TestSessionIntrospection:
    """CR-016: GET /auth/me — يمكِّن الواجهة من معرفة المستخدم الحالي بعد
    Refresh/SSR/فتح مباشر/تبويب جديد، دون كشف الجلسة نفسها لجافاسكربت."""

    def test_me_without_cookie_returns_401(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_me_after_login_returns_user_id_role_and_status(self, app_and_client):
        app, client = app_and_client
        user_id = _seed_password_identity(app, "me@example.com", "Str0ngPass1!")
        app.state.auth_repository.set_user_role(user_id, "business_seller")

        login_resp = client.post("/api/v1/auth/login", json={
            "login_identifier": "me@example.com", "password": "Str0ngPass1!",
        })
        assert login_resp.status_code == 200

        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == user_id
        assert body["primary_role"] == "business_seller"
        assert body["account_status"] == "active"

    def test_me_reflects_current_status_not_stale_login_time_value(self, app_and_client):
        """يتحقق أن /auth/me يقرأ الحالة الفعلية الآن، لا قيمة محفوظة وقت
        Login — بالضبط سبب وجود هذا الـEndpoint بدل الاعتماد على LoginResponse."""
        app, client = app_and_client
        user_id = _seed_password_identity(app, "status-check@example.com", "Str0ngPass1!")
        client.post("/api/v1/auth/login", json={
            "login_identifier": "status-check@example.com", "password": "Str0ngPass1!",
        })

        assert client.get("/api/v1/auth/me").json()["account_status"] == "active"

        app.state.auth_repository.set_user_status(user_id, "suspended")
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["account_status"] == "suspended"

    def test_me_after_logout_returns_401(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "logout-me@example.com", "Str0ngPass1!")
        client.post("/api/v1/auth/login", json={
            "login_identifier": "logout-me@example.com", "password": "Str0ngPass1!",
        })
        assert client.get("/api/v1/auth/me").status_code == 200

        client.post("/api/v1/auth/logout")
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_no_password_or_hash_leaks_in_me_response(self, app_and_client):
        """نفس فحص التسرّب المطبَّق على بقية Endpoints هذا الملف — يُعاد هنا
        تحديدًا لأن /auth/me حقل جديد قد يُغري بإضافة بيانات إضافية لاحقًا."""
        app, client = app_and_client
        _seed_password_identity(app, "leak-check@example.com", "Str0ngPass1!")
        client.post("/api/v1/auth/login", json={
            "login_identifier": "leak-check@example.com", "password": "Str0ngPass1!",
        })
        body_text = client.get("/api/v1/auth/me").text
        assert "Str0ngPass1!" not in body_text
        assert "password" not in body_text.lower()
        assert "hash" not in body_text.lower()
