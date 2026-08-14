"""
storage.py — Storage Abstraction (Media Foundation Approved Baseline v1.0 §8)
=====================================================================
Business Logic (media_service.py) لا يعرف S3/filesystem إطلاقًا ولا يخزن
أي URL — يتعامل حصرًا مع Storage Keys مجردة (نصوص داخلية، UUID/random، لا
علاقة لها بالاسم الأصلي للملف — §4). التبديل بين مزود تخزين وآخر (Local
للتطوير، S3-compatible للإنتاج) لا يستوجب أي Migration ولا تغيير في طبقة
الأعمال — فقط تبديل الـAdapter المحقون عند التركيب (نفس نمط Repository
Pattern القائم في كل المشروع: PostgresXRepository/InMemoryXRepository).

هذا الملف عابر للخدمات عمدًا (services/shared/src)، بنفس نمط
advisory_lock.py/idempotency_service.py — media وأي خدمة مستقبلية تحتاج
تخزين ملفات تعتمد عليه، لا تُعيد تنفيذه.
"""

import os
import shutil
from abc import ABC, abstractmethod
from typing import BinaryIO, Optional


class StorageError(Exception):
    """خطأ عام في طبقة التخزين (فشل قراءة/كتابة/حذف)."""


class StorageAdapter(ABC):
    """العقد الوحيد الذي تعتمد عليه media_service.py — لا تفاصيل مزوِّد هنا."""

    @abstractmethod
    def put(self, key: str, data: BinaryIO, content_type: Optional[str] = None) -> None:
        """يكتب البيانات تحت key مُعطى (يستبدل إن كان موجودًا). key نصّي داخلي، لا مسار متحكَّم به من المستخدم."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        """حذف فعلي؛ لا يرفع خطأ إن كان key غير موجود أصلًا (Idempotent)."""
        raise NotImplementedError

    @abstractmethod
    def get_private_access_url(self, key: str, ttl_seconds: int) -> str:
        """رابط موقَّع (Signed) صالح لمدة ttl_seconds — للمحتوى Private (PR/Offer images، §10)."""
        raise NotImplementedError

    @abstractmethod
    def get_public_url(self, key: str) -> str:
        """رابط عام دائم — للمحتوى Public فقط (Inventory Derived Display، §9). لا TTL."""
        raise NotImplementedError

    @abstractmethod
    def read(self, key: str) -> bytes:
        """
        Batch 2 Unit 2: قراءة محتوى مُخزَّن فعليًا — ضرورية لإعادة معالجة
        صورة موجودة (Watermark على Derived Display لـinventory_item، §9)
        بلا الاحتفاظ بالبايتات الأصلية خارج Storage. يرفع StorageError
        إن لم يكن key موجودًا.
        """
        raise NotImplementedError

    @abstractmethod
    def exists(self, key: str) -> bool:
        raise NotImplementedError


class LocalStorageAdapter(StorageAdapter):
    """
    للتطوير/الاختبار فقط (§8). يخزن الملفات فعليًا على القرص المحلي تحت
    base_dir، خارج أي Web Root (المسؤولية على من يُشغِّل الخادم أن يضبط
    base_dir بعيدًا عن أي مجلد يُقدَّم مباشرة — §13). لا Signed URLs
    حقيقية هنا (لا خادم HTTP فعلي لهذا الـAdapter) — يُعيد مسار Placeholder
    قابل للتمييز بوضوح أنه للتطوير فقط، لا رابطًا صالحًا للإنتاج.
    """

    def __init__(self, base_dir: str, public_base_url: str = "/media-dev"):
        self._base_dir = os.path.abspath(base_dir)
        self._public_base_url = public_base_url.rstrip("/")
        os.makedirs(self._base_dir, exist_ok=True)

    def _resolve_path(self, key: str) -> str:
        # منع Path Traversal (§13): key يجب ألا يخرج من base_dir إطلاقًا
        candidate = os.path.abspath(os.path.join(self._base_dir, key))
        if not candidate.startswith(self._base_dir + os.sep) and candidate != self._base_dir:
            raise StorageError(f"محاولة وصول خارج نطاق التخزين المسموح: {key!r}")
        return candidate

    def put(self, key: str, data: BinaryIO, content_type: Optional[str] = None) -> None:
        path = self._resolve_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            shutil.copyfileobj(data, f)

    def delete(self, key: str) -> None:
        path = self._resolve_path(key)
        if os.path.isfile(path):
            os.remove(path)
        # Idempotent عمدًا: لا خطأ إن كان الملف غير موجود أصلًا (قد يكون حُذِف مسبقًا)

    def get_private_access_url(self, key: str, ttl_seconds: int) -> str:
        # Placeholder تطويري فقط — لا توقيع تشفيري حقيقي هنا (Local Adapter لا يخدم HTTP فعليًا)
        return f"{self._public_base_url}/private/{key}?ttl={ttl_seconds}"

    def get_public_url(self, key: str) -> str:
        return f"{self._public_base_url}/public/{key}"

    def read(self, key: str) -> bytes:
        path = self._resolve_path(key)
        if not os.path.isfile(path):
            raise StorageError(f"key غير موجود: {key!r}")
        with open(path, "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        return os.path.isfile(self._resolve_path(key))


class S3CompatibleStorageAdapter(StorageAdapter):
    """
    للإنتاج (§8) — S3 أو متوافق (MinIO، إلخ). هيكل العقد كامل ومطابق
    لـStorageAdapter تمامًا؛ التنفيذ الفعلي لعمليات الشبكة (boto3) غير
    مكتمل في هذه الدفعة تحديدًا — لا اعتماديات/بيانات اعتماد S3 حقيقية
    متاحة للاختبار في بيئة التطوير الحالية. يُستبدَل بتنفيذ فعلي مُختبَر
    ضد S3/MinIO حقيقي قبل استخدامه في الإنتاج، بلا أي تغيير على
    media_service.py أو Migration عند ذلك (هذا بالضبط الغرض من الفصل).
    """

    def __init__(self, bucket: str, region: Optional[str] = None, endpoint_url: Optional[str] = None):
        self._bucket = bucket
        self._region = region
        self._endpoint_url = endpoint_url

    def put(self, key: str, data: BinaryIO, content_type: Optional[str] = None) -> None:
        raise NotImplementedError(
            "S3CompatibleStorageAdapter: يستوجب تنفيذًا فعليًا عبر boto3 مُختبَرًا ضد "
            "S3/MinIO حقيقي قبل الاستخدام — غير متاح في بيئة هذه الدفعة."
        )

    def delete(self, key: str) -> None:
        raise NotImplementedError("S3CompatibleStorageAdapter.delete: انظر تعليق put أعلاه.")

    def get_private_access_url(self, key: str, ttl_seconds: int) -> str:
        raise NotImplementedError("S3CompatibleStorageAdapter.get_private_access_url: انظر تعليق put أعلاه.")

    def get_public_url(self, key: str) -> str:
        raise NotImplementedError("S3CompatibleStorageAdapter.get_public_url: انظر تعليق put أعلاه.")

    def read(self, key: str) -> bytes:
        raise NotImplementedError("S3CompatibleStorageAdapter.read: انظر تعليق put أعلاه.")

    def exists(self, key: str) -> bool:
        raise NotImplementedError("S3CompatibleStorageAdapter.exists: انظر تعليق put أعلاه.")


class InMemoryStorageAdapter(StorageAdapter):
    """اختباري فقط — بلا لمس القرص إطلاقًا، لاختبارات الوحدة السريعة."""

    def __init__(self):
        self._store: dict[str, bytes] = {}

    def put(self, key: str, data: BinaryIO, content_type: Optional[str] = None) -> None:
        self._store[key] = data.read()

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def get_private_access_url(self, key: str, ttl_seconds: int) -> str:
        return f"memory://private/{key}?ttl={ttl_seconds}"

    def get_public_url(self, key: str) -> str:
        return f"memory://public/{key}"

    def exists(self, key: str) -> bool:
        return key in self._store

    def read(self, key: str) -> bytes:
        """اختباري فقط: قراءة مباشرة لمحتوى مُخزَّن، للتحقق من صحة المعالجة في الاختبارات."""
        if key not in self._store:
            raise StorageError(f"key غير موجود: {key!r}")
        return self._store[key]
