"""
test_auth_api.py — اختبارات وحدة لطبقة REST API لخدمة Auth
تستخدم InMemoryAuthRepository وInMemorySessionRepository (لا اتصال قاعدة
بيانات حقيقي هنا)؛ اختبارات التكامل الحقيقية على PostgreSQL منفصلة
(tests/test_postgres_auth_sessions_integration.py) وتُشغَّل عبر GitHub Actions.

يتطلب: fastapi, httpx (لا تتوفر في بيئة الإعداد الحالية بلا اتصال شبكة؛
مُتحقَّق من منطق العمل المكافئ يدويًا دون هذه الاعتماديات — انظر ملخص
التسليم لتفاصيل التحقق البديل الذي أُجري فعليًا).
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from store_repository import InMemoryStoreRepository
from aud_repository import InMemoryAudRepository

# Root Cause (GitHub Evidence Gate failure — 3 pre-existing legacy tests):
# _record_login_security_event أصبحت Fail-Closed (تصحيح أمني معتمَد، لا
# رجوع عنه) — أي محاولة دخول فاشلة تحمل معرِّفًا تتطلب LOGIN_IDENTIFIER_HMAC_SECRET
# فعليًا الآن، وإلا تُرفَض الاستجابة بـ503 بدل إتمام تصنيف الفشل الطبيعي (401).
# ثلاثة اختبارات قديمة (تسبق هذه الدفعة بالكامل: test_login_with_wrong_password_
# rejected_401، test_login_with_nonexistent_account_rejected_with_same_generic_
# message، test_login_does_not_auto_create_account) لم تُحدَّث لتوفير هذا
# السرّ — هذا فجوة إعداد اختبار قديم، لا خطأ في السلوك الأمني نفسه (الذي
# يجب ألا يُضعَف). الإصلاح: قيمة افتراضية حتمية غير إنتاجية على مستوى
# الـFixture نفسه (لا سرّ حقيقي، نص واضح "test-only" في القيمة ذاتها) —
# كل اختبار في هذا الملف يحصل عليها تلقائيًا، فلا حاجة لتصحيح كل اختبار
# فشل دخول قديم يدويًا واحدًا تلو الآخر (ولا خطر تكرار نفس الفجوة مستقبلًا
# لاختبار جديد ينسى ضبطها). الاختبارات التي تتحقق تحديدًا من سلوك "السرّ
# غائب" (TestFailedLoginHistory وTestLoginSecurityHistory) تُلغي هذه القيمة
# صراحة بـmonkeypatch.delenv داخل جسم الاختبار نفسه — يعمل بشكل صحيح لأن
# monkeypatch كائن واحد مشترَك بين الـFixture وجسم الاختبار لكل اختبار.
_DEFAULT_TEST_HMAC_SECRET = "test-only-hmac-secret-not-for-production-do-not-reuse"


@pytest.fixture
def app_and_client(monkeypatch):
    monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", _DEFAULT_TEST_HMAC_SECRET)

    app = FastAPI()
    app.include_router(auth_router)

    providers = [
        IdentityProvider(code="email_password", display_name="البريد وكلمة المرور", category="password", is_enabled=True),
        IdentityProvider(code="phone_otp", display_name="الهاتف", category="otp", is_enabled=False),
    ]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    # CR-018: register يحتاج store_repository (إنشاء متجر تلقائي للبائع، REQ-STR-001)
    app.state.store_repository = InMemoryStoreRepository()
    # Login/Security History (Gap Sweep v2.2): login/register يكتبان login_success/
    # login_failed إلى aud.events فعليًا الآن — بلا هذا، get_aud_repository
    # (auth_api.py) يفشل بـAttributeError عند أول محاولة دخول/تسجيل.
    app.state.aud_repository = InMemoryAudRepository()

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


class TestRegister:
    """CR-018 — REQ-IAM-001/002/006، REQ-STR-001، REQ-SEC-006."""

    def _register(self, client, role_choice, account_type, email, password="password1"):
        return client.post("/api/v1/auth/register", json={
            "role_choice": role_choice, "account_type": account_type,
            "email": email, "password": password,
        })

    def test_buyer_individual_mapping(self, app_and_client):
        _, client = app_and_client
        resp = self._register(client, "buyer", "individual", "b1@example.com")
        assert resp.status_code == 201
        body = resp.json()
        assert body["primary_role"] == "individual_buyer"
        assert body["account_type"] == "individual"
        assert body["store_id"] is None

    def test_buyer_business_mapping(self, app_and_client):
        _, client = app_and_client
        resp = self._register(client, "buyer", "business", "b2@example.com")
        body = resp.json()
        assert body["primary_role"] == "business_buyer"
        assert body["store_id"] is None

    def test_seller_individual_mapping_and_store_created(self, app_and_client):
        app, client = app_and_client
        resp = self._register(client, "seller", "individual", "s1@example.com")
        body = resp.json()
        assert body["primary_role"] == "individual_seller"
        assert body["store_id"] is not None

        store = app.state.store_repository.get_store_by_id(body["store_id"])
        assert store.owner_user_ref_id == body["user_id"]

    def test_seller_business_mapping_and_store_created(self, app_and_client):
        app, client = app_and_client
        resp = self._register(client, "seller", "business", "s2@example.com")
        body = resp.json()
        assert body["primary_role"] == "business_seller"
        assert body["store_id"] is not None
        store = app.state.store_repository.get_store_by_id(body["store_id"])
        assert store.owner_user_ref_id == body["user_id"]

    def test_seller_gets_exactly_one_store(self, app_and_client):
        app, client = app_and_client
        resp = self._register(client, "seller", "individual", "s3@example.com")
        user_id = resp.json()["user_id"]
        matching = [s for s in app.state.store_repository._stores.values() if s.owner_user_ref_id == user_id]
        assert len(matching) == 1

    def test_admin_role_choice_rejected(self, app_and_client):
        """role_choice مقيَّد بنيويًا بـLiteral["buyer","seller"] — قيمة أخرى ترفضها Pydantic نفسها (422)."""
        _, client = app_and_client
        resp = client.post("/api/v1/auth/register", json={
            "role_choice": "admin", "account_type": "individual",
            "email": "hacker@example.com", "password": "password1",
        })
        assert resp.status_code == 422

    def test_duplicate_email_409_no_partial_state(self, app_and_client):
        app, client = app_and_client
        self._register(client, "buyer", "individual", "dup@example.com")
        resp = self._register(client, "buyer", "individual", "dup@example.com")
        assert resp.status_code == 409

    def test_password_under_8_chars_rejected(self, app_and_client):
        _, client = app_and_client
        resp = self._register(client, "buyer", "individual", "weak@example.com", password="short1")
        assert resp.status_code == 400

    def test_password_exactly_8_chars_accepted(self, app_and_client):
        _, client = app_and_client
        resp = self._register(client, "buyer", "individual", "eight@example.com", password="12345678")
        assert resp.status_code == 201

    def test_password_without_complexity_mix_not_rejected(self, app_and_client):
        """قرار صريح: لا إلزام تركيبة (حرف كبير/رمز/رقم) — كلمة مرور بسيطة طولها كافٍ تُقبَل."""
        _, client = app_and_client
        resp = self._register(client, "buyer", "individual", "simple@example.com", password="simplepass")
        assert resp.status_code == 201

    def test_session_cookie_issued_and_me_works_immediately(self, app_and_client):
        _, client = app_and_client
        resp = self._register(client, "buyer", "individual", "session@example.com")
        assert resp.status_code == 201
        assert "session_id" in resp.cookies

        me_resp = client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200
        assert me_resp.json()["primary_role"] == "individual_buyer"

    def test_no_password_or_hash_in_response(self, app_and_client):
        _, client = app_and_client
        resp = self._register(client, "buyer", "individual", "leakcheck@example.com", password="password1")
        body_text = resp.text
        assert "password1" not in body_text
        assert "hash" not in body_text.lower()


class TestLoginSecurityHistory:
    """Admin Operational Completion — Login/Security History (Gap Sweep v2.2).
    aud.events هو الـSSOT — نتحقق هنا من طبقة الـAPI عبر InMemoryAudRepository
    (التحقق الحقيقي لصلاحيات القاعدة/Append-Only في اختبارات PostgreSQL)."""

    def test_successful_login_creates_login_success_event(self, app_and_client):
        app, client = app_and_client
        _seed_password_identity(app, "loginhist@example.com", "Str0ngPass1!")
        resp = _login(client, "loginhist@example.com", "Str0ngPass1!")
        assert resp.status_code == 200

        events = app.state.aud_repository._events
        success_events = [e for e in events if e.event_name == "login_success"]
        assert len(success_events) == 1
        assert success_events[0].log_type == "security"
        assert success_events[0].actor_ref_id is not None
        assert success_events[0].occurred_at_utc is not None

    def test_successful_registration_creates_login_success_event(self, app_and_client):
        """التسجيل يُنشئ جلسة مصادَقة حقيقية فعليًا (نفس آلية /login) — لذا
        يُسجَّل حدث login_success واحد أيضًا، تمثيلًا صادقًا لما حدث فعلًا."""
        app, client = app_and_client
        resp = client.post("/api/v1/auth/register", json={
            "role_choice": "buyer", "account_type": "individual",
            "email": "regevt@example.com", "password": "Str0ngPass1!",
        })
        assert resp.status_code == 201

        events = app.state.aud_repository._events
        success_events = [e for e in events if e.event_name == "login_success"]
        assert len(success_events) == 1
        assert success_events[0].actor_ref_id == resp.json()["user_id"]

    def test_failed_login_creates_login_failed_event_with_null_actor(self, app_and_client, monkeypatch):
        monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", "test-secret-for-unit-tests")
        app, client = app_and_client
        resp = _login(client, "nonexistent@example.com", "wrongpass")
        assert resp.status_code == 401

        events = app.state.aud_repository._events
        failed_events = [e for e in events if e.event_name == "login_failed"]
        assert len(failed_events) == 1
        assert failed_events[0].actor_ref_id is None
        assert failed_events[0].log_type == "security"

    def test_failed_login_wrong_password_still_null_actor(self, app_and_client, monkeypatch):
        """رسالة الفشل الموحَّدة (منع تعداد الحسابات) تنعكس أيضًا في التدقيق:
        لا فرق بين 'هوية غير موجودة' و'كلمة مرور خاطئة' في actor_ref_id."""
        monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", "test-secret-for-unit-tests")
        app, client = app_and_client
        _seed_password_identity(app, "wrongpass@example.com", "Str0ngPass1!")
        resp = _login(client, "wrongpass@example.com", "totallywrong")
        assert resp.status_code == 401

        failed_events = [e for e in app.state.aud_repository._events if e.event_name == "login_failed"]
        assert len(failed_events) == 1
        assert failed_events[0].actor_ref_id is None

    def test_no_password_or_credential_material_in_any_audit_event(self, app_and_client, monkeypatch):
        monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", "test-secret-for-unit-tests")
        app, client = app_and_client
        _seed_password_identity(app, "credcheck@example.com", "Str0ngPass1!")
        _login(client, "credcheck@example.com", "Str0ngPass1!")
        _login(client, "credcheck@example.com", "wrongpass")

        for event in app.state.aud_repository._events:
            metadata_str = str(event.metadata)
            assert "Str0ngPass1!" not in metadata_str
            assert "wrongpass" not in metadata_str
            assert "password" not in metadata_str.lower()
            assert "token" not in metadata_str.lower()

    def test_no_raw_attempted_identifier_in_failed_login_event(self, app_and_client, monkeypatch):
        monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", "test-secret-for-unit-tests")
        app, client = app_and_client
        _login(client, "raw-identifier-check@example.com", "wrongpass")

        failed_events = [e for e in app.state.aud_repository._events if e.event_name == "login_failed"]
        assert len(failed_events) == 1
        metadata_str = str(failed_events[0].metadata)
        assert "raw-identifier-check@example.com" not in metadata_str

    def test_attempted_identifier_hmac_present_and_deterministic(self, app_and_client, monkeypatch):
        monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", "test-secret-for-unit-tests")
        app, client = app_and_client
        _login(client, "hmactest@example.com", "wrong1")
        _login(client, "HMACTest@Example.com  ", "wrong2")  # نفس الهوية بعد التطبيع

        failed_events = [e for e in app.state.aud_repository._events if e.event_name == "login_failed"]
        assert len(failed_events) == 2
        hmac1 = failed_events[0].metadata.get("attempted_identifier_hmac")
        hmac2 = failed_events[1].metadata.get("attempted_identifier_hmac")
        assert hmac1 is not None
        assert hmac1 == hmac2

    def test_login_failure_returns_503_not_401_if_audit_write_fails(self, app_and_client, monkeypatch):
        """تصحيح أمني (استبدال اختبار سابق كان يفترض تجاهل فشل التدقيق):
        سجل الدخول التاريخي إلزامي، لا Best-Effort — عطل حقيقي في
        aud_repository يجب أن يمنع اعتبار الطلب مكتملًا بأي استجابة عادية
        (401 أو 200)، ويُستبدَل بخطأ صريح (503) يعكس فشل تسجيل الدليل
        الأمني الإلزامي، لا نجاحًا/فشلًا صامتًا للمصادقة بمعزل عن ذلك."""
        monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", "test-secret-for-unit-tests")
        app, client = app_and_client
        _seed_password_identity(app, "auditcrash@example.com", "Str0ngPass1!")

        def _broken_insert(event):
            raise RuntimeError("simulated aud outage")
        app.state.aud_repository.insert_event = _broken_insert

        resp = _login(client, "auditcrash@example.com", "wrongpass")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "LOGIN_HISTORY_PERSISTENCE_FAILED"

    def test_successful_login_returns_503_and_no_session_cookie_if_audit_write_fails(self, app_and_client, monkeypatch):
        """الحالة الأهم على مستوى HTTP: دخول بكلمة مرور صحيحة، لكن تسجيل
        الدليل التاريخي يفشل — يجب ألا يصل العميل أي Cookie جلسة صالح
        (Fail-Closed فعلي من منظور العميل).

        تصحيح نهائي مهم بخصوص نطاق هذا الاختبار: التصميم النهائي يُغلِّف
        إنشاء الجلسة + تسجيل الحدث داخل معاملة صريحة واحدة
        (auth_repo.connection)، بحيث لا يُثبَّت صف الجلسة في PostgreSQL
        الحقيقي أصلًا عند الفشل (Rollback حقيقي، لا Compensation). لكن
        InMemorySessionRepository لا تملك دلالات معاملات حقيقية —
        _NoOpTransaction (auth_repository.py) توثِّق هذا صراحةً: "لا
        Rollback حقيقيًا عند فشل جزئي". لذا هذا الاختبار الوهمي يتحقق فقط
        من عقد HTTP (503 + بلا Cookie) — إثبات عدم بقاء صف جلسة فعلي غير
        مُبطَل حقيقةً يقع حصرًا على
        test_postgres_login_security_history_integration.py (PostgreSQL حي)."""
        monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", "test-secret-for-unit-tests")
        app, client = app_and_client
        _seed_password_identity(app, "successcrash@example.com", "Str0ngPass1!")

        def _broken_insert(event):
            raise RuntimeError("simulated aud outage")
        app.state.aud_repository.insert_event = _broken_insert

        resp = _login(client, "successcrash@example.com", "Str0ngPass1!")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "LOGIN_HISTORY_PERSISTENCE_FAILED"
        assert "session_id" not in resp.cookies

    def test_registration_succeeded_session_failed_reports_honestly(self, app_and_client, monkeypatch):
        """تصحيح الحالة الفاصلة (Register) — عقد HTTP فقط هنا (راجع ملاحظة
        النطاق في الاختبار أعلاه؛ الإثبات الحقيقي لعدم بقاء جلسة نشطة في
        القاعدة الفعلية يقع في اختبار PostgreSQL المخصَّص). الحساب يُنشَأ
        فعلًا وبلا رجوع عنه (مُثبَت من registration_service.py —
        with auth_repo.connection: يُغلِق قبل عودة register_user())، ويجب
        أن يُبلَّغ العميل صراحةً بذلك (لا حالة غامضة، رمز خطأ مختلف عن
        فشل /login)."""
        monkeypatch.setenv("LOGIN_IDENTIFIER_HMAC_SECRET", "test-secret-for-unit-tests")
        app, client = app_and_client

        def _broken_insert(event):
            raise RuntimeError("simulated aud outage")
        app.state.aud_repository.insert_event = _broken_insert

        resp = client.post("/api/v1/auth/register", json={
            "role_choice": "buyer", "account_type": "individual",
            "email": "regcrash@example.com", "password": "Str0ngPass1!",
        })
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "REGISTRATION_SUCCEEDED_SESSION_FAILED"
        assert "session_id" not in resp.cookies

        # الحساب موجود فعلًا رغم استجابة الخطأ — إثبات مباشر عبر المستودع
        matching_identities = [
            ident for ident in app.state.auth_repository._identities
            if ident.external_identifier == "regcrash@example.com"
        ]
        assert len(matching_identities) == 1, "الحساب يجب أن يكون موجودًا فعلًا رغم 503"

        # محاولة تسجيل ثانية بنفس البريد تصطدم بـ409 (الحساب موجود فعلًا) —
        # يثبت أن الحالة ليست غامضة: العميل يجب أن يستخدم /login، لا يعيد /register
        retry_resp = client.post("/api/v1/auth/register", json={
            "role_choice": "buyer", "account_type": "individual",
            "email": "regcrash@example.com", "password": "Str0ngPass1!",
        })
        assert retry_resp.status_code == 409

    def test_missing_hmac_secret_fails_closed_not_silently_downgraded(self, app_and_client, monkeypatch):
        """تصحيح أمني: سرّ HMAC غائب لمحاولة فاشلة تحمل معرِّفًا لم يعد
        يُنتِج حدثًا بحقل ناقص (attempted_identifier_hmac=None) بصمت — يمنع
        اكتمال الطلب بخطأ صريح، لأن غياب السرّ فشل تهيئة/أمان حقيقي."""
        monkeypatch.delenv("LOGIN_IDENTIFIER_HMAC_SECRET", raising=False)
        app, client = app_and_client

        resp = _login(client, "missing-secret-check@example.com", "wrongpass")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error_code"] == "LOGIN_HISTORY_PERSISTENCE_FAILED"

        # لا حدث بحقل ناقص أُدرِج بصمت — الفشل منع الإدراج بالكامل لهذه المحاولة
        failed_events = [e for e in app.state.aud_repository._events if e.event_name == "login_failed"]
        assert len(failed_events) == 0


class TestAccountStatusAdministration:
    """Admin Operational Completion — Members/Accounts Administration
    (Gap Sweep v2/v2.2، بند 4/7: Account Status فقط)."""

    def _login_as_admin(self, app, client, email="admin-status@example.com"):
        user_id = _seed_password_identity(app, email, "Str0ngPass1!")
        app.state.auth_repository.set_user_role(user_id, "admin")
        _login(client, email, "Str0ngPass1!")
        return user_id

    def test_requires_authentication(self, app_and_client):
        _, client = app_and_client
        resp = client.post("/api/v1/auth/users/some-id/status", json={"status": "suspended"})
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        user_id = _seed_password_identity(app, "regular-user@example.com", "Str0ngPass1!")
        _login(client, "regular-user@example.com", "Str0ngPass1!")
        resp = client.post(f"/api/v1/auth/users/{user_id}/status", json={"status": "suspended"})
        assert resp.status_code == 403

    def test_admin_can_suspend_account(self, app_and_client):
        app, client = app_and_client
        target_id = _seed_password_identity(app, "target-suspend@example.com", "Str0ngPass1!")
        self._login_as_admin(app, client)

        resp = client.post(f"/api/v1/auth/users/{target_id}/status", json={"status": "suspended"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "suspended"

    def test_invalid_status_value_rejected(self, app_and_client):
        app, client = app_and_client
        target_id = _seed_password_identity(app, "target-invalid@example.com", "Str0ngPass1!")
        self._login_as_admin(app, client)

        resp = client.post(f"/api/v1/auth/users/{target_id}/status", json={"status": "not_a_real_status"})
        assert resp.status_code == 400

    def test_nonexistent_user_404(self, app_and_client):
        app, client = app_and_client
        self._login_as_admin(app, client)
        resp = client.post("/api/v1/auth/users/does-not-exist/status", json={"status": "suspended"})
        # InMemoryAuthRepository.set_user_status يُعيد True دائمًا (راجع
        # auth_repository.py — لا قائمة مستخدمين مستقلة للتحقق من الوجود في
        # الاختبار الوهمي)؛ 404 حقيقي يُثبَت في اختبار PostgreSQL فقط.
        assert resp.status_code == 200

    def test_endpoint_never_accepts_role_field(self, app_and_client):
        """يثبت صراحة أن Endpoint الحالة لا يقبل primary_role إطلاقًا —
        Privileged Role Assignment يبقى محظورًا (Gap Sweep v2.2، بند 6/7)."""
        app, client = app_and_client
        target_id = _seed_password_identity(app, "no-role-field@example.com", "Str0ngPass1!")
        self._login_as_admin(app, client)

        resp = client.post(
            f"/api/v1/auth/users/{target_id}/status",
            json={"status": "suspended", "primary_role": "super_admin"},
        )
        assert resp.status_code == 200
        # لم يتغيّر الدور — UserStatusUpdateRequest (Pydantic) يتجاهل الحقل
        # الزائد؛ لا مسار كود يقرأ primary_role من هذا الطلب إطلاقًا.
        assert app.state.auth_repository.get_user_role(target_id) != "super_admin"
