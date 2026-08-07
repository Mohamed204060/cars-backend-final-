"""
idempotency_service.py — منطق عدم التكرار للعمليات الحساسة (Idempotency)
المرجع: DD الحزمة 2، القسم 2.2 — Store + Inventory Contract Extension
        (أول واجهة تُطبِّق هذه السياسة فعليًا: POST /api/v1/inventory-items)

هذا الملف عابر للخدمات عمدًا (services/shared/src)، لا خاص بـInventory وحده:
DD-2 يذكر صراحة أن نفس السياسة تشمل مستقبلاً إنشاء طلب شراء، تقديم عرض
سعر، قبول عرض، وتأكيد استيراد جماعي — كلها في خدمات أخرى قادمة.

النطاق: نفس المفتاح (Idempotency-Key) + نفس المستخدم + نفس الواجهة يُعيد
نتيجة العملية الأصلية دون تنفيذها مرة ثانية. نفس المفتاح من مستخدم آخر أو
لواجهة مختلفة لا يتعارض إطلاقًا (تفاديًا لتصادم مفاتيح عشوائية بين مستخدمين).
"""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CachedResponse:
    response_status: int
    response_body: dict


def get_cached_response_via_repository(
    repository, idempotency_key: str, user_ref_id: str, endpoint: str
) -> Optional[CachedResponse]:
    return repository.get_cached_response(idempotency_key, user_ref_id, endpoint)


def store_response_via_repository(
    repository, idempotency_key: str, user_ref_id: str, endpoint: str,
    response_status: int, response_body: dict,
) -> None:
    repository.store_response(idempotency_key, user_ref_id, endpoint, response_status, response_body)
