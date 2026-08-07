"""
workers.py — عمال المعالجة الخلفية لخدمة NTF (Background Workers)
المرجع: مراجعة جاهزية التنفيذ NTF v1.1

ملاحظة أمانة: هذه محاكاة حقيقية قابلة للاختبار لمنطق كل عامل (يمكن استدعاء
كل عامل يدويًا "دورة واحدة" واختبار أثره الفعلي)، لا عمليات خلفية حقيقية
تعمل باستمرار داخل هذه البيئة (تستوجب بنية تشغيل فعلية كـCelery أو RQ أو
مماثل، خارج نطاق الاختبار الآلي هنا)؛ التنفيذ الفعلي لدورة تشغيل مستمرة
(Polling Loop) يُبنى لاحقًا فوق منطق "دورة واحدة" الموثَّق هنا دون أي تعديل.
"""

from typing import Optional
from datetime import datetime

from ntf_service import transition_recipient_status, Recipient


class OutboxWorker:
    """
    يستطلع جدول Outbox (لا الطابور الحقيقي مباشرة) ويُرسِل المهام الجاهزة
    فعليًا إلى الطابور، ثم يُعلِّمها كمُرسَلة؛ يحقِّق نمط Transactional Outbox
    المطلوب صراحة: لا Job يُرسَل مباشرة من داخل معاملة قاعدة بيانات أبدًا.
    """

    def __init__(self, repository, real_queue_enqueue_fn):
        self._repository = repository
        self._enqueue = real_queue_enqueue_fn  # دالة محقونة لإرسال العنصر للطابور الحقيقي

    def run_one_cycle(self) -> int:
        """يُنفَّذ دورة واحدة: يعالج كل عناصر Outbox المعلَّقة حاليًا. يُعيد عدد العناصر المُرسَلة."""
        pending = self._repository.get_pending_outbox_entries()
        dispatched_count = 0
        for entry in pending:
            self._enqueue(entry)  # إرسال فعلي للطابور الحقيقي (محقون، خارج هذا الملف)
            self._repository.mark_outbox_entry_dispatched(entry["id"])
            dispatched_count += 1
        return dispatched_count


class DeliveryWorker:
    """ينفِّذ عملية الإرسال الفعلية لعنصر Recipient واحد عبر مزوِّد القناة المناسب."""

    def __init__(self, repository, channel_provider_registry):
        self._repository = repository
        self._registry = channel_provider_registry

    def process_recipient(self, recipient: Recipient, title: str, body: str, correlation_id: str) -> Recipient:
        provider = self._registry.get(recipient.channel_provider_code)
        if provider is None:
            transition_recipient_status(recipient, "failed", datetime.now(), failure_reason_code="permission_denied")
            return self._repository.update_recipient(recipient)

        # يعكس Idempotency: لا إعادة معالجة لعنصر وصل بالفعل لحالة نهائية
        if recipient.status in {"delivered", "read", "failed", "cancelled"}:
            return recipient

        transition_recipient_status(recipient, "queued", datetime.now())
        result = provider.send(recipient.user_ref_id, title, body, correlation_id)

        if result.success:
            transition_recipient_status(recipient, "sent", datetime.now())
            transition_recipient_status(recipient, "delivered", datetime.now())
        else:
            transition_recipient_status(recipient, "failed", datetime.now(),
                                         failure_reason_code=result.failure_reason_code)
        return self._repository.update_recipient(recipient)


class RetryWorker:
    """يعيد محاولة عناصر Recipient الفاشلة وفق سياسة إعادة محاولة بسيطة (حد أقصى للمحاولات)."""

    def __init__(self, repository, delivery_worker: DeliveryWorker, max_retries: int = 3):
        self._repository = repository
        self._delivery_worker = delivery_worker
        self._max_retries = max_retries

    def retry_if_eligible(self, recipient: Recipient, title: str, body: str, correlation_id: str) -> Optional[Recipient]:
        if recipient.status != "failed":
            return None
        if recipient.retry_count >= self._max_retries:
            return None  # يستوجب النقل لـDead Letter Queue في تكامل لاحق، لا إعادة محاولة أخرى
        recipient.retry_count += 1
        # إعادة الحالة إلى pending (لا queued) لتبدأ process_recipient دورتها الطبيعية
        # عبر آلة الحالة نفسها (pending -> queued -> ...)، بدلاً من محاولة قفز غير صالح
        recipient.status = "pending"
        return self._delivery_worker.process_recipient(recipient, title, body, correlation_id)


class ChannelHealthCheckWorker:
    """يراقب صحة مزوِّدي القنوات دوريًا؛ ينقل الحالة إلى Offline بعد فشل متكرر."""

    def __init__(self, repository, degraded_threshold: int = 3, offline_threshold: int = 6):
        self._repository = repository
        self._degraded_threshold = degraded_threshold
        self._offline_threshold = offline_threshold

    def evaluate(self, provider_code: str, consecutive_failures: int):
        from ntf_service import update_channel_health
        provider = self._repository.get_channel_provider(provider_code)
        if provider is None:
            return None
        if consecutive_failures >= self._offline_threshold:
            update_channel_health(provider, "offline")
        elif consecutive_failures >= self._degraded_threshold:
            update_channel_health(provider, "degraded")
        else:
            update_channel_health(provider, "healthy")
        return self._repository.update_channel_provider(provider)
