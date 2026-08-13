"""
credential_service.py — تجزئة والتحقق من كلمات المرور (Password Credentials)
المرجع: تعديل CR-013 (إضافة دورة بيانات اعتماد كلمة المرور)؛
        REQ-SEC-002 (حماية بيانات الاعتماد)، REQ-SEC-006 (تعقيد كلمة المرور)

اختيار الخوارزمية: PBKDF2-HMAC-SHA256 (مكتبة hashlib القياسية، معتمَدة وفق
NIST SP 800-132)، لا bcrypt أو argon2. السبب موثَّق صراحة في CR-013 (v2):
هذا المشروع واجه مرارًا قيود شبكة فعلية منعت تثبيت حزم خارجية (403
Forbidden مؤكَّد أكثر من مرة عبر مراحل هذا المشروع)؛ الاعتماد على hashlib
القياسية يُزيل هذا الخطر تمامًا لخوارزمية أمنية جوهرية، ويسمح بالتحقق
الفعلي من هذا الكود الآن دون انتظار بيئة بها اتصال شبكي، بدلًا من تسليم
كود غير مُختبَر فعليًا اعتمادًا على حزمة لم يتحقق أحد من عملها هنا.

صيغة التخزين ذاتية الوصف (Self-Describing) للسماح برفع عدد التكرارات
مستقبلاً دون إبطال كلمات المرور القديمة:
    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""

import hashlib
import hmac
import secrets

ALGORITHM_TAG = "pbkdf2_sha256"
# عدد التكرارات: يتبع توصية OWASP الحالية لـPBKDF2-HMAC-SHA256 (>=600,000)؛
# قيمة قابلة للرفع مستقبلاً (الصيغة ذاتية الوصف تحتفظ بالقيمة المستخدَمة
# فعليًا لكل كلمة مرور على حدة، فلا يُبطِل رفع القيمة الافتراضية أي حساب قائم).
DEFAULT_ITERATIONS = 600_000
SALT_BYTES = 16


class InvalidCredentialHashFormatError(Exception):
    """صيغة تجزئة مخزَّنة غير معروفة أو تالفة؛ لا يمكن التحقق منها."""


def hash_password(raw_password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    """يولِّد ملحًا عشوائيًا فريدًا لكل كلمة مرور (لا ملح ثابت أو مشترَك إطلاقًا)."""
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM_TAG}${iterations}${salt.hex()}${derived.hex()}"


def verify_password(raw_password: str, stored_hash: str) -> bool:
    """مقارنة بزمن ثابت (hmac.compare_digest) لمنع هجمات التوقيت (Timing Attack)."""
    try:
        algorithm, iterations_str, salt_hex, hash_hex = stored_hash.split("$")
    except (ValueError, AttributeError):
        raise InvalidCredentialHashFormatError("صيغة تجزئة كلمة المرور المخزَّنة غير صالحة.")

    if algorithm != ALGORITHM_TAG:
        raise InvalidCredentialHashFormatError(f"خوارزمية تجزئة غير مدعومة: {algorithm}")

    iterations = int(iterations_str)
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)

    derived = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)
