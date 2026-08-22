"""
test_client_ip_and_identifier_hmac.py — اختبارات وحدة لموديولَي الأمن
المشتركَين: client_ip.py (استخراج IP موثوق) وidentifier_hmac.py (ارتباط
معرِّف محاولات الدخول الفاشلة). Admin Operational Completion — Login/
Security History (Gap Sweep v2.2).
"""

import pytest

from client_ip import resolve_authoritative_client_ip
from identifier_hmac import MissingHmacSecretError, compute_attempted_identifier_hmac


class TestResolveAuthoritativeClientIp:

    def test_no_trusted_proxies_uses_peer_directly(self):
        result = resolve_authoritative_client_ip(
            peer_address="203.0.113.50", forwarded_for_header=None, trusted_proxy_cidrs_env="",
        )
        assert result == "203.0.113.50"

    def test_untrusted_peer_spoofed_xff_is_ignored(self):
        """الاختبار الأمني الحاسم: عميل غير موثوق يحاول انتحال IP عبر
        X-Forwarded-For — يجب تجاهله بالكامل مهما كانت قيمته."""
        result = resolve_authoritative_client_ip(
            peer_address="203.0.113.50",
            forwarded_for_header="1.2.3.4",  # مُنتحَل من طرف غير موثوق
            trusted_proxy_cidrs_env="",  # لا وسطاء موثوقون مضبوطون
        )
        assert result == "203.0.113.50"
        assert result != "1.2.3.4"

    def test_untrusted_peer_spoofed_xff_ignored_even_with_other_trusted_cidrs_configured(self):
        """عميل مباشر (غير عبر أي وسيط) يحاول انتحال IP، رغم وجود نطاقات
        وسطاء موثوقة مضبوطة لعناوين أخرى — عنوانه هو نفسه ليس ضمنها."""
        result = resolve_authoritative_client_ip(
            peer_address="198.51.100.99",  # ليس ضمن 10.0.0.0/8
            forwarded_for_header="9.9.9.9",
            trusted_proxy_cidrs_env="10.0.0.0/8",
        )
        assert result == "198.51.100.99"

    def test_trusted_proxy_legitimate_chain_resolves_correctly(self):
        result = resolve_authoritative_client_ip(
            peer_address="10.0.0.5",
            forwarded_for_header="198.51.100.7, 10.0.0.5",
            trusted_proxy_cidrs_env="10.0.0.0/8",
        )
        assert result == "198.51.100.7"

    def test_trusted_proxy_attacker_prepended_ip_does_not_forge_result(self):
        """هجوم انتحال أكثر دقة: عميل يُدرج IP مزيَّفًا في بداية السلسلة قبل
        وصولها للوسيط الموثوق. الخوارزمية الصحيحة (يمين-لليسار) يجب ألا
        تنخدع بالعنصر الأول."""
        result = resolve_authoritative_client_ip(
            peer_address="10.0.0.5",
            forwarded_for_header="9.9.9.9, 198.51.100.7, 10.0.0.5",
            trusted_proxy_cidrs_env="10.0.0.0/8",
        )
        assert result == "198.51.100.7"
        assert result != "9.9.9.9"

    def test_chained_trusted_proxies_skipped_correctly(self):
        result = resolve_authoritative_client_ip(
            peer_address="10.0.0.9",
            forwarded_for_header="198.51.100.20, 10.0.0.1, 10.0.0.9",
            trusted_proxy_cidrs_env="10.0.0.0/8",
        )
        assert result == "198.51.100.20"

    def test_malformed_peer_ip_returns_none_not_arbitrary_string(self):
        result = resolve_authoritative_client_ip(
            peer_address="not-an-ip-address", forwarded_for_header=None, trusted_proxy_cidrs_env="",
        )
        assert result is None

    def test_missing_peer_address_returns_none(self):
        result = resolve_authoritative_client_ip(
            peer_address=None, forwarded_for_header="1.2.3.4", trusted_proxy_cidrs_env="",
        )
        assert result is None

    def test_malformed_entry_in_trusted_chain_fails_safe_to_none(self):
        """تصحيح أمني: عنصر فاسد داخل سلسلة X-Forwarded-For خلف وسيط موثوق
        كان يُتخطَّى سابقًا أملًا في عنصر صالح تالٍ — هذا "إعادة تفسير"
        لبيانات حدود ثقة مشبوهة، ممنوع صراحة الآن. النتيجة الصحيحة: None
        (فشل آمن صريح)، لا تخمين عنوان آخر."""
        result = resolve_authoritative_client_ip(
            peer_address="10.0.0.5",
            forwarded_for_header="not-an-ip, 198.51.100.7, 10.0.0.5",
            trusted_proxy_cidrs_env="10.0.0.0/8",
        )
        assert result is None

    def test_malformed_chain_entry_emits_observable_warning(self):
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = resolve_authoritative_client_ip(
                peer_address="10.0.0.5",
                forwarded_for_header="garbage-entry, 10.0.0.5",
                trusted_proxy_cidrs_env="10.0.0.0/8",
            )
        assert result is None
        assert any(issubclass(w.category, RuntimeWarning) for w in caught)

    def test_ipv6_supported(self):
        result = resolve_authoritative_client_ip(
            peer_address="2001:db8::1", forwarded_for_header=None, trusted_proxy_cidrs_env="",
        )
        assert result == "2001:db8::1"

    def test_invalid_trusted_proxy_cidr_config_fails_whole_config_not_partial(self):
        """تصحيح أمني: CIDR فاسد واحد كان يُتجاهَل بصمت سابقًا مع قبول باقي
        الإعداد جزئيًا — الآن الإعداد بأكمله يُعتبَر فاسدًا (فشل آمن صريح
        وقابل للملاحظة عبر تحذير)، لا تجاهل جزئي صامت."""
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = resolve_authoritative_client_ip(
                peer_address="203.0.113.1", forwarded_for_header="1.2.3.4",
                trusted_proxy_cidrs_env="not-a-valid-cidr,,also-invalid",
            )
        assert result == "203.0.113.1"  # Peer فقط — الافتراض الآمن
        assert any(issubclass(w.category, RuntimeWarning) for w in caught)

    def test_invalid_cidr_alongside_valid_cidr_still_invalidates_whole_config(self):
        """إثبات أدق: حتى لو كان أحد الإدخالات صالحًا (10.0.0.0/8)، وجود
        إدخال فاسد واحد آخر يُسقِط الإعداد بأكمله — لا نستخدم الجزء الصالح
        جزئيًا، لتفادي تفسير غامض لنية المُشغِّل الحقيقية."""
        result = resolve_authoritative_client_ip(
            peer_address="10.0.0.5", forwarded_for_header="198.51.100.7, 10.0.0.5",
            trusted_proxy_cidrs_env="10.0.0.0/8,not-valid",
        )
        # لو استُخدِم "10.0.0.0/8" جزئيًا لكانت النتيجة "198.51.100.7"؛ بما
        # أن الإعداد بأكمله أصبح فارغًا، الـPeer نفسه غير موثوق كوسيط الآن.
        assert result == "10.0.0.5"


class TestComputeAttemptedIdentifierHmac:

    def test_deterministic_same_input_same_output(self):
        h1 = compute_attempted_identifier_hmac("user@example.com", secret="secret-key-1")
        h2 = compute_attempted_identifier_hmac("user@example.com", secret="secret-key-1")
        assert h1 == h2

    def test_normalization_case_and_whitespace(self):
        h1 = compute_attempted_identifier_hmac("User@Example.com", secret="secret-key-1")
        h2 = compute_attempted_identifier_hmac("  user@example.com  ", secret="secret-key-1")
        assert h1 == h2

    def test_different_identifiers_produce_different_hashes(self):
        h1 = compute_attempted_identifier_hmac("alice@example.com", secret="secret-key-1")
        h2 = compute_attempted_identifier_hmac("bob@example.com", secret="secret-key-1")
        assert h1 != h2

    def test_different_secret_produces_different_hash(self):
        """يُثبِت أن المفتاح فعليًا جزء من الحساب — لا Hash عادي بلا مفتاح."""
        h1 = compute_attempted_identifier_hmac("user@example.com", secret="secret-key-1")
        h2 = compute_attempted_identifier_hmac("user@example.com", secret="secret-key-2")
        assert h1 != h2

    def test_output_is_hex_sha256_length(self):
        h = compute_attempted_identifier_hmac("user@example.com", secret="secret-key-1")
        assert len(h) == 64
        int(h, 16)  # يرفع ValueError لو لم يكن hex صالحًا

    def test_missing_secret_raises_explicit_error(self):
        with pytest.raises(MissingHmacSecretError):
            compute_attempted_identifier_hmac("user@example.com", secret="")

    def test_missing_secret_from_env_raises(self, monkeypatch):
        monkeypatch.delenv("LOGIN_IDENTIFIER_HMAC_SECRET", raising=False)
        with pytest.raises(MissingHmacSecretError):
            compute_attempted_identifier_hmac("user@example.com")

    def test_raw_identifier_never_appears_in_output(self):
        h = compute_attempted_identifier_hmac("very-identifiable-email@example.com", secret="secret-key-1")
        assert "very-identifiable-email" not in h
        assert "example.com" not in h
