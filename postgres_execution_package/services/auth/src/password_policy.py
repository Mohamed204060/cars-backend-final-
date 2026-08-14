"""
password_policy.py — سياسة تعقيد كلمة المرور المركزية (REQ-SEC-006)
قرار منتج صريح (لا رقم SRS محدَّد وُجد في الكود قابل للتعارض): الحد الأدنى
8 أحرف فقط، بلا إلزام تركيبة معقَّدة (حرف كبير/صغير/رقم/رمز معًا) — لتقليل
Friction في التسجيل لمستخدم عادي أو صاحب محل/تشليح بخبرة تقنية محدودة.

مصمَّمة كدالة واحدة قابلة لإعادة الاستخدام: Registration الآن، وChange
Password/Reset Password لاحقًا — لا تكرار للعتبة في أكثر من مكان.

لا Blocklist لكلمات مرور شائعة/مسرَّبة هنا عمدًا (تحسين أمني مستقل مستقبلي،
يحتاج مصدر بيانات خارجي لم يُعتمَد بعد).
"""

MIN_PASSWORD_LENGTH = 8


class WeakPasswordError(Exception):
    """كلمة المرور لا تحقق الحد الأدنى للسياسة الحالية."""


def validate_password_policy(raw_password: str) -> None:
    """يرفع WeakPasswordError برسالة بسيطة ومفهومة عند عدم تحقّق الحد الأدنى فقط.
    لا فحص أي شيء آخر (لا تركيبة، لا Blocklist) — طبقًا للقرار المعتمَد صراحة."""
    if len(raw_password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"كلمة المرور قصيرة جدًا — الحد الأدنى {MIN_PASSWORD_LENGTH} أحرف.")
