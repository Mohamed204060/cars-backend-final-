"""
test_credential_service.py — اختبارات وحدة خالصة لتجزئة/تحقق كلمات المرور
(لا اعتماديات خارجية؛ تُشغَّل بلا اتصال شبكة) + اختبارات المستودع الوهمي
لتدفق التحقق الكامل (find_identity_and_verify_password).
المرجع: تعديل CR-013 v2 — REQ-SEC-002، REQ-SEC-006
"""

import pytest

from credential_service import (
    InvalidCredentialHashFormatError,
    hash_password,
    verify_password,
)
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity


class TestPasswordHashing:

    def test_correct_password_verifies(self):
        h = hash_password("Str0ng-Password!", iterations=10_000)
        assert verify_password("Str0ng-Password!", h) is True

    def test_wrong_password_rejected(self):
        h = hash_password("Str0ng-Password!", iterations=10_000)
        assert verify_password("totally-different", h) is False

    def test_each_hash_uses_a_unique_random_salt(self):
        h1 = hash_password("same-password", iterations=10_000)
        h2 = hash_password("same-password", iterations=10_000)
        assert h1 != h2  # الملح عشوائي في كل مرة، حتى لكلمة المرور نفسها

    def test_stored_hash_never_contains_raw_password_substring(self):
        raw = "MyVeryUniqueSecret123"
        h = hash_password(raw, iterations=10_000)
        assert raw not in h

    def test_malformed_stored_hash_raises_explicit_error(self):
        with pytest.raises(InvalidCredentialHashFormatError):
            verify_password("anything", "not-a-real-hash-format")

    def test_unknown_algorithm_tag_raises_explicit_error(self):
        with pytest.raises(InvalidCredentialHashFormatError):
            verify_password("anything", "md5$1$deadbeef$cafebabe")


class TestRepositoryPasswordVerificationFlow:
    """يثبت أن المستودع (حتى النسخة الوهمية) لا يعيد أبدًا التجزئة نفسها،
    ولا يميّز في القيمة المُعادة بين 'حساب غير موجود' و'كلمة مرور خاطئة'."""

    @pytest.fixture
    def repo_with_one_password_user(self):
        providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
        repo = InMemoryAuthRepository(providers=providers, identities=[])
        user_id = repo.create_user()
        identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                                 external_identifier="user@example.com", is_verified=True, is_primary=True)
        repo.insert_identity(identity, raw_password="CorrectHorseBattery1!")
        return repo, user_id

    def test_correct_password_returns_identity(self, repo_with_one_password_user):
        repo, user_id = repo_with_one_password_user
        result = repo.find_identity_and_verify_password("email_password", "user@example.com", "CorrectHorseBattery1!")
        assert result is not None
        assert result.user_id == user_id

    def test_wrong_password_returns_none(self, repo_with_one_password_user):
        repo, _ = repo_with_one_password_user
        assert repo.find_identity_and_verify_password("email_password", "user@example.com", "WrongPassword") is None

    def test_nonexistent_identifier_returns_none_same_as_wrong_password(self, repo_with_one_password_user):
        """REQ عدم كشف وجود الحساب: القيمة المُعادة (None) متطابقة تمامًا
        سواء كان السبب 'الحساب غير موجود' أو 'كلمة مرور خاطئة' — لا فرق قابل للرصد."""
        repo, _ = repo_with_one_password_user
        result_unknown_account = repo.find_identity_and_verify_password("email_password", "ghost@example.com", "anything")
        result_wrong_password = repo.find_identity_and_verify_password("email_password", "user@example.com", "wrong")
        assert result_unknown_account is None
        assert result_wrong_password is None
        assert result_unknown_account == result_wrong_password  # نفس القيمة (None) بلا استثناء

    def test_suspended_account_rejected_same_as_wrong_password(self, repo_with_one_password_user):
        repo, user_id = repo_with_one_password_user
        repo.set_user_status(user_id, "suspended")
        result = repo.find_identity_and_verify_password("email_password", "user@example.com", "CorrectHorseBattery1!")
        assert result is None  # حتى بكلمة مرور صحيحة تمامًا؛ الحساب موقوف

    def test_identity_without_any_password_set_returns_none(self):
        """هوية OAuth (بلا credential_secret_hash إطلاقًا) لا يجب أن تُقبَل أبدًا
        عبر مسار كلمة المرور، حتى لو صادف 'raw_password' أي قيمة."""
        providers = [IdentityProvider(code="google", display_name="Google", category="oauth", is_enabled=True)]
        repo = InMemoryAuthRepository(providers=providers, identities=[])
        user_id = repo.create_user()
        oauth_identity = UserIdentity(id="", user_id=user_id, provider_code="google",
                                       external_identifier="sub-12345", is_verified=True, is_primary=True)
        repo.insert_identity(oauth_identity)  # بلا raw_password — كما هو متوقَّع لـOAuth
        result = repo.find_identity_and_verify_password("google", "sub-12345", "any-guess")
        assert result is None
