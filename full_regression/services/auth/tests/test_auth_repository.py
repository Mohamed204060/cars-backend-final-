"""
test_auth_repository.py — اختبارات وحدة لتنسيق المصادقة عبر طبقة Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
تستخدم InMemoryAuthRepository فقط (بلا قاعدة بيانات حقيقية).
"""

import sys
import os
import unittest
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auth_service import (  # noqa: E402
    IdentityProvider, UserIdentity,
    register_or_login_via_repository, add_identity_via_repository, remove_identity_via_repository,
    DuplicateIdentityError, LastIdentityRemovalError, ProviderDisabledError,
)
from auth_repository import InMemoryAuthRepository  # noqa: E402

PROVIDERS = [
    IdentityProvider(code="email_password", display_name="البريد وكلمة المرور", category="password", is_enabled=True),
    IdentityProvider(code="google", display_name="Google", category="oauth", is_enabled=True),
    IdentityProvider(code="facebook", display_name="Facebook", category="oauth", is_enabled=True),
    IdentityProvider(code="phone_otp", display_name="الهاتف (OTP)", category="otp", is_enabled=False),
]


class TestRegisterOrLoginViaRepository(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryAuthRepository(providers=list(PROVIDERS), identities=[])

    def test_first_registration_creates_new_account(self):
        result = register_or_login_via_repository(self.repo, "email_password", "sami@example.com", is_verified=True)
        self.assertEqual(result["action"], "create_new")
        self.assertTrue(result["user_id"].startswith("user-"))

    def test_login_via_same_identity_returns_existing_login(self):
        first = register_or_login_via_repository(self.repo, "email_password", "sami@example.com", is_verified=True)
        second = register_or_login_via_repository(self.repo, "email_password", "sami@example.com", is_verified=True)
        self.assertEqual(second["action"], "existing_login")
        self.assertEqual(second["user_id"], first["user_id"])

    def test_google_login_with_same_verified_email_links_to_existing_account(self):
        # السيناريو المحوري لـCR-005 عبر طبقة Repository الفعلية
        email_result = register_or_login_via_repository(self.repo, "email_password", "huda@example.com", is_verified=True)
        google_result = register_or_login_via_repository(self.repo, "google", "huda@example.com", is_verified=True)
        self.assertEqual(google_result["action"], "link_to_existing")
        self.assertEqual(google_result["user_id"], email_result["user_id"])

        # التأكد من أن المستخدم أصبح يملك وسيلتَي هوية فعليًا في المستودع
        identities = self.repo.get_identities_for_user(email_result["user_id"])
        provider_codes = {i.provider_code for i in identities}
        self.assertEqual(provider_codes, {"email_password", "google"})

    def test_disabled_provider_rejected(self):
        with self.assertRaises(ProviderDisabledError):
            register_or_login_via_repository(self.repo, "phone_otp", "+966500000000", is_verified=True)


class TestAtomicUserCreation(unittest.TestCase):
    """
    توصية المالك: التحقق من أن إنشاء المستخدم ووسيلة هويته الأولى يتمّان
    كوحدة واحدة عبر create_user_and_primary_identity؛ في InMemoryAuthRepository
    هذا يعني: كل تسجيل جديد يُنتج مستخدمًا واحدًا بوسيلة هوية أساسية واحدة
    مرتبطة به فورًا، لا حالة وسيطة يظهر فيها مستخدم بلا أي وسيلة هوية.
    """

    def test_new_registration_produces_user_with_primary_identity_atomically(self):
        repo = InMemoryAuthRepository(providers=list(PROVIDERS), identities=[])
        result = register_or_login_via_repository(repo, "email_password", "atomic@example.com", is_verified=True)

        self.assertEqual(result["action"], "create_new")
        identities = repo.get_identities_for_user(result["user_id"])
        # لا حالة وسيطة: بمجرد وجود المستخدم، وسيلة هويته الأساسية موجودة فورًا
        self.assertEqual(len(identities), 1)
        self.assertTrue(identities[0].is_primary)
        self.assertEqual(identities[0].provider_code, "email_password")


class TestAddAndRemoveIdentityViaRepository(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryAuthRepository(providers=list(PROVIDERS), identities=[])
        result = register_or_login_via_repository(self.repo, "email_password", "nora@example.com", is_verified=True)
        self.user_id = result["user_id"]

    def test_add_identity_success(self):
        new_identity = add_identity_via_repository(self.repo, self.user_id, "facebook", "nora.fb@example.com", is_verified=True)
        self.assertEqual(new_identity.user_id, self.user_id)
        identities = self.repo.get_identities_for_user(self.user_id)
        self.assertEqual(len(identities), 2)

    def test_add_identity_conflict_rejected(self):
        # مستخدم آخر يملك هذا المعرّف فعلاً على مزوّد آخر تمامًا
        other = register_or_login_via_repository(self.repo, "google", "shared@example.com", is_verified=True)
        with self.assertRaises(DuplicateIdentityError):
            add_identity_via_repository(self.repo, self.user_id, "google", "shared@example.com")

    def test_remove_identity_blocked_when_last_remaining(self):
        with self.assertRaises(LastIdentityRemovalError):
            identities = self.repo.get_identities_for_user(self.user_id)
            remove_identity_via_repository(self.repo, self.user_id, identities[0].id)

    def test_remove_identity_succeeds_when_another_remains(self):
        add_identity_via_repository(self.repo, self.user_id, "facebook", "nora.fb@example.com", is_verified=True)
        identities_before = self.repo.get_identities_for_user(self.user_id)
        email_identity = next(i for i in identities_before if i.provider_code == "email_password")

        remove_identity_via_repository(self.repo, self.user_id, email_identity.id)

        identities_after = self.repo.get_identities_for_user(self.user_id)
        self.assertEqual(len(identities_after), 1)
        self.assertEqual(identities_after[0].provider_code, "facebook")


class TestConcurrentIdentityLinking(unittest.TestCase):
    """
    توصية المالك: سيناريو تزامن — طلبان متزامنان يحاولان ربط نفس وسيلة الهوية
    (نفس provider ونفس external_identifier) بحسابين مختلفين في اللحظة نفسها.
    النتيجة المتوقَّعة: نجاح واحد فقط، ورفض الآخر، دون بيانات مكرَّرة أو حالة
    غير متناسقة. يُحاكى قيد قاعدة البيانات uq_user_identities_provider_identifier
    عبر قفل في InMemoryAuthRepository (انظر التعليق في auth_repository.py).
    """

    def test_two_concurrent_requests_linking_same_identity_only_one_succeeds(self):
        repo = InMemoryAuthRepository(providers=list(PROVIDERS), identities=[])
        user_a = repo.create_user()
        user_b = repo.create_user()

        results = {"success": 0, "failure": 0}
        results_lock = threading.Lock()

        def attempt(user_id):
            try:
                add_identity_via_repository(repo, user_id, "google", "race@example.com", is_verified=True)
                with results_lock:
                    results["success"] += 1
            except DuplicateIdentityError:
                with results_lock:
                    results["failure"] += 1

        t1 = threading.Thread(target=attempt, args=(user_a,))
        t2 = threading.Thread(target=attempt, args=(user_b,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # نتيجة حتمية: نجاح واحد بالضبط، وفشل واحد بالضبط
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["failure"], 1)

        # التحقق من عدم وجود بيانات مكرَّرة: سجل واحد فقط لهذا المعرّف في كامل المستودع
        matching = [i for i in repo._identities if i.external_identifier == "race@example.com"]
        self.assertEqual(len(matching), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
