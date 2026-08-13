"""
advisory_lock.py — بدائية حتمية لحساب مفتاح PostgreSQL Advisory Lock
المرجع: Approved VCT Design Baseline — Batch 1 (القسم 15: "Advisory Lock —
        ممنوع Python built-in hash")

هذا الملف عابر للخدمات عمدًا (services/shared/src)، تمامًا كنمط
idempotency_service.py — يُستخدَم من CMP (منع تعايش General/Year-specific
لنفس القطعة/الفئة) ومن VCT (منع تعايش Trim-level/Year-specific Market
Availability لنفس الفئة)، وأي نطاق قفل حتمي مستقبلي.

لماذا لا نستخدم hash() المدمجة في بايثون: hash() على النصوص غير حتمية عبر
عمليات تشغيل مختلفة (PYTHONHASHSEED عشوائي افتراضيًا لكل Process) — قفلان
لنفس المدخلات في عمليتين مختلفتين (Worker/Request) قد يحسبان مفتاحين
مختلفين تمامًا، فينهار الغرض الكامل من القفل (منع التزامن على نفس المورد).
BLAKE2b حتمية عبر أي عملية/بيئة/لغة طالما نفس المدخلات بالضبط.

الخوارزمية:
1. المدخلات (namespace + أجزاء canonical) تُسلسَل بفاصل ثابت لا يظهر عادة
   داخل UUID/نص عادي (\\x1f — Unit Separator)، لمنع تصادم عرضي بين
   ("a", "bc") و("ab", "c").
2. BLAKE2b digest_size=8 بايت (64 بت) على الترميز UTF-8 للسلسلة الناتجة.
3. تُقرَأ الـ8 بايت كعدد صحيح 64 بت Signed (big-endian) — لأن
   pg_advisory_xact_lock(bigint) في PostgreSQL يتوقع bigint موقَّعًا.
4. تصادم نظري ممكن في فضاء 64 بت يعني فقط Serialization زائد عرضي (قفل
   مشترك بين مفتاحين مختلفين نادرًا)، لا فساد بيانات — التحقق الفعلي من
   صحة البيانات يقع دائمًا داخل نفس Transaction بعد الحصول على القفل،
   والقفل لا يُغني عن Unique Indexes بل يكملها (القسم 16).
"""

import hashlib
import uuid

_SEPARATOR = "\x1f"  # Unit Separator — لا يظهر عادة داخل UUID أو نص عادي


def _canonicalize_part(part) -> str:
    """UUIDs بصيغة موحَّدة (حروف صغيرة، بلا أقواس) بغضّ النظر عن صيغة الإدخال."""
    if isinstance(part, uuid.UUID):
        return str(part)
    text = str(part)
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError, TypeError):
        return text


def compute_advisory_lock_key(namespace: str, *canonical_parts) -> int:
    """
    يُعيد عددًا صحيحًا 64 بت Signed حتميًا، مناسبًا مباشرة لتمريره إلى
    pg_advisory_xact_lock(key). نفس (namespace, *canonical_parts) بالضبط
    يُنتج دائمًا نفس المفتاح، عبر أي عملية تشغيل أو بيئة.

    أمثلة Namespaces (القسم 15): cmp-compatibility، vct-market، media-binding.
    """
    if not namespace:
        raise ValueError("namespace إلزامي وغير فارغ لحساب مفتاح القفل.")
    parts = [namespace] + [_canonicalize_part(p) for p in canonical_parts]
    canonical_string = _SEPARATOR.join(parts)
    digest = hashlib.blake2b(canonical_string.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)
