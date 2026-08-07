"""
test_auth_service.py — اختبارات وحدة لخدمة الهوية والمصادقة
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auth_service import (  # noqa: E402
    IdentityProvider,
    UserIdentity,
    ensure_provider_enabled,
    find_existing_account_by_verified_identifier,
    resolve_registration,
    add_identity,
    remove_identity,
    build_security_audit_event,
    ProviderDisabledError,
    DuplicateIdentityError,
    LastIdentityRemovalError,
)

PROVIDERS = [
    IdentityProvider(code="email_password", display_name="البريد وكلمة المرور", category="password", is_enabled=True),
    IdentityProvider(code="google", display_name="Google", category="oauth", is_enabled=True),
    IdentityProvider(code="facebook", display_name="Facebook", category="oauth", is_enabled=True),
    IdentityProvider(code="x", display_name="X", category="oauth", is_enabled=True),
    IdentityProvider(code="phone_otp", display_name="الهاتف (OTP)", category="otp", is_enabled=False),  # REQ-IAM-012
]


class TestProviderEnablement(unittest.TestCase):
    """REQ-IAM-013"""

    def test_enabled_provider_passes(self):
        provider = ensure_provider_enabled(PROVIDERS, "google")
        self.assertEqual(provider.code, "google")

    def test_disabled_provider_raises(self):
        # REQ-IAM-012: phone_otp محجوز وغير مفعَّل افتراضيًا في الإصدار الأول
        with self.assertRaises(ProviderDisabledError):
            ensure_provider_enabled(PROVIDERS, "phone_otp")

    def test_unknown_provider_raises(self):
        with self.assertRaises(ProviderDisabledError):
            ensure_provider_enabled(PROVIDERS, "apple")


class TestDuplicateAccountPrevention(unittest.TestCase):
    """REQ-IAM-014"""

    def setUp(self):
        self.identities = [
            UserIdentity(id="i1", user_id="user-1", provider_code="email_password",
                         external_identifier="ali@example.com", is_verified=True),
        ]

    def test_matching_verified_identifier_returns_existing_user(self):
        found = find_existing_account_by_verified_identifier(self.identities, "ali@example.com")
        self.assertEqual(found, "user-1")

    def test_case_insensitive_match(self):
        found = find_existing_account_by_verified_identifier(self.identities, "ALI@EXAMPLE.COM")
        self.assertEqual(found, "user-1")

    def test_unverified_identity_does_not_match(self):
        identities = [
            UserIdentity(id="i2", user_id="user-2", provider_code="google",
                         external_identifier="sara@example.com", is_verified=False),
        ]
        found = find_existing_account_by_verified_identifier(identities, "sara@example.com")
        self.assertIsNone(found)

    def test_no_match_returns_none(self):
        found = find_existing_account_by_verified_identifier(self.identities, "nobody@example.com")
        self.assertIsNone(found)

    def test_resolve_registration_links_to_existing_account_on_verified_match(self):
        # مستخدم يسجّل عبر Google بنفس البريد الموثَّق لحساب قائم أصلاً عبر البريد/كلمة المرور
        result = resolve_registration(
            all_identities=self.identities,
            providers=PROVIDERS,
            provider_code="google",
            external_identifier="ali@example.com",
            is_verified=True,
            new_user_id_factory=lambda: "should-not-be-called",
        )
        self.assertEqual(result["action"], "link_to_existing")
        self.assertEqual(result["user_id"], "user-1")

    def test_resolve_registration_creates_new_account_when_no_match(self):
        result = resolve_registration(
            all_identities=self.identities,
            providers=PROVIDERS,
            provider_code="facebook",
            external_identifier="new-person@example.com",
            is_verified=True,
            new_user_id_factory=lambda: "user-new-123",
        )
        self.assertEqual(result["action"], "create_new")
        self.assertEqual(result["user_id"], "user-new-123")

    def test_resolve_registration_rejects_disabled_provider(self):
        with self.assertRaises(ProviderDisabledError):
            resolve_registration(
                all_identities=self.identities,
                providers=PROVIDERS,
                provider_code="phone_otp",
                external_identifier="+966500000000",
                is_verified=True,
                new_user_id_factory=lambda: "user-x",
            )

    def test_end_to_end_scenario_email_registration_then_google_link(self):
        """
        سيناريو محوري في CR-005: مستخدم يسجِّل أولاً عبر البريد/كلمة المرور،
        ثم يسجّل الدخول لاحقًا عبر Google بنفس بريده الموثَّق — يجب أن يُربَط
        بالحساب القائم نفسه لا أن يُنشئ حسابًا مكرَّرًا (REQ-IAM-014).
        """
        # الخطوة 1: تسجيل أولي عبر البريد/كلمة المرور
        step1 = resolve_registration(
            all_identities=[],
            providers=PROVIDERS,
            provider_code="email_password",
            external_identifier="omar@example.com",
            is_verified=True,
            new_user_id_factory=lambda: "user-omar",
        )
        self.assertEqual(step1["action"], "create_new")
        self.assertEqual(step1["user_id"], "user-omar")

        existing_identities = [
            UserIdentity(id="i1", user_id="user-omar", provider_code="email_password",
                         external_identifier="omar@example.com", is_verified=True, is_primary=True),
        ]

        # الخطوة 2: تسجيل دخول لاحق عبر Google بنفس البريد الموثَّق
        step2 = resolve_registration(
            all_identities=existing_identities,
            providers=PROVIDERS,
            provider_code="google",
            external_identifier="omar@example.com",
            is_verified=True,
            new_user_id_factory=lambda: "should-not-be-used",
        )
        self.assertEqual(step2["action"], "link_to_existing")
        self.assertEqual(step2["user_id"], "user-omar")  # لا حساب مكرَّر

    def test_remove_identity_allowed_regardless_of_which_type_remains(self):
        """
        REQ-IAM-016 (توضيح): القيد على العدد الإجمالي المتبقي لا نوع الوسيلة؛
        إزالة البريد/كلمة المرور مسموحة طالما تبقى وسيلة Google مثلاً.
        """
        identities = [
            UserIdentity(id="i1", user_id="user-1", provider_code="email_password",
                         external_identifier="ali@example.com", is_verified=True),
            UserIdentity(id="i2", user_id="user-1", provider_code="google",
                         external_identifier="ali@gmail.com", is_verified=True),
        ]
        result = remove_identity(identities, user_id="user-1", identity_id="i1")
        remaining_ids = [i.id for i in result]
        self.assertNotIn("i1", remaining_ids)
        self.assertIn("i2", remaining_ids)


class TestIdentityManagement(unittest.TestCase):
    """REQ-IAM-015, 016"""

    def setUp(self):
        self.identities = [
            UserIdentity(id="i1", user_id="user-1", provider_code="email_password",
                         external_identifier="ali@example.com", is_verified=True, is_primary=True),
        ]

    def test_add_identity_success(self):
        new_identity = add_identity(
            self.identities, PROVIDERS, user_id="user-1",
            provider_code="google", external_identifier="ali@gmail.com", is_verified=True,
        )
        self.assertEqual(new_identity.user_id, "user-1")
        self.assertEqual(new_identity.provider_code, "google")

    def test_add_identity_rejects_disabled_provider(self):
        with self.assertRaises(ProviderDisabledError):
            add_identity(self.identities, PROVIDERS, user_id="user-1",
                         provider_code="phone_otp", external_identifier="+966500000000")

    def test_add_identity_rejects_identifier_linked_to_another_account(self):
        """
        سيناريو أمني محوري (CR-005): محاولة ربط وسيلة هوية (Google) مرتبطة
        فعلاً بحساب مستخدم آخر — يجب أن تُرفَض العملية بالكامل دون أي تعديل
        على البيانات القائمة؛ يعكس قيد قاعدة البيانات
        uq_user_identities_provider_identifier. تسجيل الحدث الأمني الفعلي
        لهذه المحاولة (REQ-IAM-017) يتم عند اكتمال الربط بخدمة AUD الحقيقية،
        خارج نطاق هذا الاختبار.
        """
        other_account_identities = self.identities + [
            UserIdentity(id="i2", user_id="user-2", provider_code="google",
                         external_identifier="shared@example.com", is_verified=True),
        ]
        snapshot_before = list(other_account_identities)  # نسخة لإثبات عدم التعديل

        with self.assertRaises(DuplicateIdentityError):
            add_identity(other_account_identities, PROVIDERS, user_id="user-1",
                         provider_code="google", external_identifier="shared@example.com")

        # إثبات صريح: لا تعديل على قائمة الهويات القائمة بعد الرفض
        self.assertEqual(other_account_identities, snapshot_before)
        self.assertEqual(len(other_account_identities), 2)

    def test_remove_identity_rejected_when_last_remaining(self):
        # REQ-IAM-016: منع إزالة آخر وسيلة هوية متبقية
        with self.assertRaises(LastIdentityRemovalError):
            remove_identity(self.identities, user_id="user-1", identity_id="i1")

    def test_remove_identity_succeeds_when_not_last(self):
        identities = self.identities + [
            UserIdentity(id="i2", user_id="user-1", provider_code="google",
                         external_identifier="ali@gmail.com", is_verified=True),
        ]
        result = remove_identity(identities, user_id="user-1", identity_id="i2")
        remaining_ids = [i.id for i in result]
        self.assertNotIn("i2", remaining_ids)
        self.assertIn("i1", remaining_ids)


class TestSecurityAuditEventBuilder(unittest.TestCase):
    """REQ-IAM-017"""

    def test_build_event_for_known_action(self):
        event = build_security_audit_event("identity_added", "user-1", "google")
        self.assertEqual(event["log_type"], "security")
        self.assertEqual(event["event_name"], "identity_added")
        self.assertEqual(event["actor_ref_id"], "user-1")

    def test_build_event_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            build_security_audit_event("unknown_action", "user-1", "google")


if __name__ == "__main__":
    unittest.main(verbosity=2)
