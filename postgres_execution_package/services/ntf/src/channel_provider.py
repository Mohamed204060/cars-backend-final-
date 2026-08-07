"""
channel_provider.py — واجهة مزوِّد القناة الموحَّدة (Channel Provider) وتنفيذ داخل الموقع
المرجع: مراجعة جاهزية التنفيذ NTF v1.1 — بنية Channel Provider

واجهة موحَّدة واحدة يلتزم بها كل مزوِّد؛ الإصدار الأول ينفِّذ "داخل الموقع"
فقط فعليًا. إضافة بريد إلكتروني أو SMS أو Push لاحقًا تعني تنفيذ الواجهة
نفسها فقط، دون أي تعديل على منطق الحملة أو Recipient.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class DeliveryResult:
    success: bool
    failure_reason_code: Optional[str] = None


class ChannelProvider(ABC):
    """الواجهة الموحَّدة التي يجب أن ينفِّذها أي مزوِّد قناة، حاليًا أو مستقبلاً."""

    @property
    @abstractmethod
    def code(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def send(self, user_ref_id: str, title: str, body: str, correlation_id: str) -> DeliveryResult:
        raise NotImplementedError


class InAppChannelProvider(ChannelProvider):
    """
    تنفيذ فعلي وحيد في هذا الإصدار: يخزِّن الإشعار كسجل NotificationCenterEntry
    داخل الموقع مباشرة (لا اتصال خارجي، لا شبكة، لا فشل متوقَّع عادةً بخلاف
    أخطاء داخلية نادرة)؛ يمثِّل أبسط تنفيذ ممكن للواجهة، ليكون مرجعًا واضحًا
    لأي مزوِّد قناة يُضاف لاحقًا (بريد/SMS/Push/WhatsApp/Telegram).
    """

    def __init__(self, repository):
        self._repository = repository

    @property
    def code(self) -> str:
        return "in_app"

    def send(self, user_ref_id: str, title: str, body: str, correlation_id: str) -> DeliveryResult:
        try:
            # التنفيذ الفعلي: إنشاء سجل مركز إشعارات جديد للمستخدم
            from ntf_service import NotificationCenterEntry
            entry = NotificationCenterEntry(id="", recipient_id="", user_ref_id=user_ref_id)
            self._repository.insert_notification_center_entry(entry)
            return DeliveryResult(success=True)
        except Exception:
            return DeliveryResult(success=False, failure_reason_code="unknown_error")


class ChannelProviderRegistry:
    """
    سجل بسيط يربط رمز القناة بتنفيذها الفعلي؛ إضافة مزوِّد جديد تعني تسجيله
    هنا فقط، دون أي تعديل على منطق الحملة أو Delivery أو Recipient.
    """

    def __init__(self):
        self._providers = {}

    def register(self, provider: ChannelProvider) -> None:
        self._providers[provider.code] = provider

    def get(self, code: str) -> Optional[ChannelProvider]:
        return self._providers.get(code)
