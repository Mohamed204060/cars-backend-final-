"""
identifier_hmac.py — ارتباط آمن لمعرِّفات محاولات الدخول الفاشلة
المرجع: Admin Operational Completion — Login/Security History (Gap Sweep v2.2، بند 3)

الهدف: تمكين ربط أمني بين محاولات فاشلة متكررة تستهدف نفس المعرِّف (بريد/
هاتف/اسم مستخدم) — دون تخزين المعرِّف الخام نفسه في سجل التدقيق إطلاقًا.

قواعد صارمة (غير قابلة للتفاوض، محسومة صراحةً في Gap Sweep v2.2):
- لا نص خام للمعرِّف المحاول يُخزَّن أبدًا.
- لا Hash عادي بلا مفتاح (SHA-256 وحدها قابلة لهجوم القاموس/Rainbow Table
  على مساحة صغيرة نسبيًا من عناوين البريد/الهواتف المحتملة).
- لا Salt عام ثابت (يُفشِل غرضه — أي طرف يعرفه يعيد نفس الهجوم).
- لا Salt عشوائي لكل حدث (يُلغي إمكانية الربط بين المحاولات المتكررة، وهو
  الغرض الوحيد من هذه الوظيفة أصلًا).
- المطلوب حصرًا: HMAC-SHA256 حتمي بمفتاح سرّي واحد على مستوى الخادم
  (LOGIN_IDENTIFIER_HMAC_SECRET)، منفصل تمامًا عن أسرار كلمات المرور/JWT/
  الجلسات — تسريب هذا السر لا يجب أن يُمكِّن أي مهاجمة أخرى غير عكس هذا
  الربط تحديدًا.

Key Management (مسؤولية بيئة التشغيل، لا هذا الملف):
- LOGIN_IDENTIFIER_HMAC_SECRET يُقرأ من متغيّر بيئة فقط — لا قيمة افتراضية
  مُدمَجة في الكود، ولا Fallback صامت إلى قيمة ثابتة (لتفادي إنتاج HMACs
  قابلة للتخمين إن نُسي ضبط السر في بيئة حقيقية).
- إن غاب السر تمامًا وقت التشغيل، الدالة ترفع خطأ صريحًا بدل حساب Hash
  بمفتاح فارغ/متوقَّع.
- يدعم التدوير (Rotation) عبر تغيير قيمة متغيّر البيئة نفسه في بيئة النشر —
  لا آلية Rotation إضافية داخل الكود؛ القيمة الحالية فقط تُستخدَم دائمًا (لا
  دعم للتحقق من HMACs بمفاتيح قديمة متعددة في هذا الإصدار — خارج النطاق
  المعتمَد حاليًا).
"""

import hashlib
import hmac
import os


class MissingHmacSecretError(RuntimeError):
    """LOGIN_IDENTIFIER_HMAC_SECRET غير مضبوط في بيئة التشغيل — فشل صريح،
    لا حساب صامت بمفتاح افتراضي متوقَّع/فارغ."""


def _normalize_identifier(raw_identifier: str) -> str:
    """تطبيع حتمي قبل الـHMAC: إزالة المسافات الطرفية + تحويل لحروف صغيرة.
    يضمن أن "User@Example.com " و"user@example.com" ينتجان نفس الربط —
    وإلا يفقد الربط جدواه لأتفه اختلاف في الإدخال."""
    return raw_identifier.strip().lower()


def compute_attempted_identifier_hmac(raw_identifier: str, secret: str | None = None) -> str:
    """
    secret: للاختبار فقط — يسمح بحقن سر مباشر بدل قراءته من os.environ
    (تفاديًا للاعتماد على متغيرات بيئة حقيقية داخل الاختبارات). في التشغيل
    الفعلي دائمًا None، فتُقرأ القيمة من LOGIN_IDENTIFIER_HMAC_SECRET.

    يُعيد سلسلة hex لناتج HMAC-SHA256 (64 حرفًا). يرفع MissingHmacSecretError
    إن غاب السر تمامًا (لا قيمة افتراضية أبدًا).
    """
    effective_secret = secret if secret is not None else os.environ.get("LOGIN_IDENTIFIER_HMAC_SECRET")
    if not effective_secret:
        raise MissingHmacSecretError(
            "LOGIN_IDENTIFIER_HMAC_SECRET غير مضبوط — لا يمكن حساب ارتباط آمن "
            "لمحاولة الدخول الفاشلة. اضبط هذا المتغيّر عبر إدارة الأسرار الخاصة "
            "بالبيئة (منفصل تمامًا عن أسرار كلمات المرور/JWT/الجلسات)."
        )

    normalized = _normalize_identifier(raw_identifier)
    digest = hmac.new(
        key=effective_secret.encode("utf-8"),
        msg=normalized.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return digest
