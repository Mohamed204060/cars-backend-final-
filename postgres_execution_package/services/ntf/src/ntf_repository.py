"""
ntf_repository.py — طبقة الوصول للبيانات لخدمة NTF (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.7؛ مراجعة جاهزية التنفيذ NTF v1.1

يشمل عقد Outbox (نمط Transactional Outbox المعتمَد): insert_outbox_entry تُستدعى
دائمًا ضمن نفس معاملة إنشاء Delivery/Recipient، لا كخطوة منفصلة لاحقة.
لا دالة حذف فعلي في هذا الملف لأي كيان من كيانات مجال الأعمال (BR-NTF-001).
"""

from abc import ABC, abstractmethod
from typing import Optional, List

from ntf_service import (
    Campaign, Delivery, Recipient, Template, TemplateVersion,
    ChannelProviderInfo, NotificationPreference, NotificationCenterEntry,
)


class NtfRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه ntf_service.py. لا دالة حذف فعلي عمدًا."""

    # --- Campaign / Delivery / Recipient ---
    @abstractmethod
    def insert_campaign(self, campaign: Campaign) -> Campaign: raise NotImplementedError

    @abstractmethod
    def get_campaign_by_id(self, campaign_id: str) -> Optional[Campaign]: raise NotImplementedError

    @abstractmethod
    def update_campaign(self, campaign: Campaign) -> Campaign: raise NotImplementedError

    @abstractmethod
    def insert_delivery(self, delivery: Delivery) -> Delivery: raise NotImplementedError

    @abstractmethod
    def get_recipients_for_delivery(self, delivery_id: str) -> List[Recipient]: raise NotImplementedError

    @abstractmethod
    def insert_recipient(self, recipient: Recipient) -> Recipient: raise NotImplementedError

    @abstractmethod
    def update_recipient(self, recipient: Recipient) -> Recipient: raise NotImplementedError

    # --- Transactional Outbox (مطلوبة صراحة من المالك) ---
    @abstractmethod
    def insert_outbox_entry(self, delivery_id: str, recipient_id: str, correlation_id: str) -> str:
        """يجب استدعاؤها ضمن نفس معاملة insert_recipient، لا بعدها منفصلة."""
        raise NotImplementedError

    @abstractmethod
    def get_pending_outbox_entries(self) -> List[dict]:
        """تستطلعها Outbox Worker دوريًا؛ لا استدعاء مباشر للطابور الحقيقي من هنا."""
        raise NotImplementedError

    @abstractmethod
    def mark_outbox_entry_dispatched(self, outbox_entry_id: str) -> None: raise NotImplementedError

    # --- Templates ---
    @abstractmethod
    def insert_template(self, template: Template, initial_version: TemplateVersion) -> Template: raise NotImplementedError

    @abstractmethod
    def insert_template_version(self, version: TemplateVersion) -> TemplateVersion: raise NotImplementedError

    # --- Channel Providers ---
    @abstractmethod
    def get_channel_provider(self, code: str) -> Optional[ChannelProviderInfo]: raise NotImplementedError

    @abstractmethod
    def update_channel_provider(self, provider: ChannelProviderInfo) -> ChannelProviderInfo: raise NotImplementedError

    # --- Preferences ---
    @abstractmethod
    def get_preferences_for_user(self, user_ref_id: str) -> List[NotificationPreference]: raise NotImplementedError

    # --- Notification Center ---
    @abstractmethod
    def insert_notification_center_entry(self, entry: NotificationCenterEntry) -> NotificationCenterEntry: raise NotImplementedError

    @abstractmethod
    def get_notification_center_entries_for_user(self, user_ref_id: str) -> List[NotificationCenterEntry]: raise NotImplementedError

    @abstractmethod
    def update_notification_center_entry(self, entry: NotificationCenterEntry) -> NotificationCenterEntry: raise NotImplementedError


class PostgresNtfRepository(NtfRepository):
    """
    تنفيذ فعلي عبر PostgreSQL وفق تصميم قاعدة البيانات المعتمَد في مراجعة
    جاهزية التنفيذ (8 جداول + ntf.outbox). غير مختبَر على اتصال حي.
    """

    def __init__(self, connection):
        self._connection = connection

    def insert_campaign(self, campaign: Campaign) -> Campaign:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ntf.campaigns (created_by_user_ref_id, title, body, audience_type, status, "
                "priority, campaign_version) VALUES (%(u)s, %(t)s, %(b)s, %(a)s, %(s)s, %(p)s, %(v)s) RETURNING id",
                {"u": campaign.created_by_user_ref_id, "t": campaign.title, "b": campaign.body,
                 "a": campaign.audience_type, "s": campaign.status, "p": campaign.priority,
                 "v": campaign.campaign_version},
            )
            campaign.id = cur.fetchone()["id"]
        return campaign

    def get_campaign_by_id(self, campaign_id: str) -> Optional[Campaign]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM ntf.campaigns WHERE id = %(id)s", {"id": campaign_id})
            row = cur.fetchone()
        if row is None:
            return None
        return Campaign(id=row["id"], created_by_user_ref_id=row["created_by_user_ref_id"],
                         title=row["title"], body=row["body"], audience_type=row["audience_type"],
                         status=row["status"], priority=row["priority"], campaign_version=row["campaign_version"])

    def update_campaign(self, campaign: Campaign) -> Campaign:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE ntf.campaigns SET status = %(s)s, campaign_version = %(v)s, updated_at = now() "
                    "WHERE id = %(id)s",
                    {"s": campaign.status, "v": campaign.campaign_version, "id": campaign.id},
                )
        return campaign

    def insert_delivery(self, delivery: Delivery) -> Delivery:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ntf.deliveries (campaign_id, campaign_version_snapshot, correlation_id, "
                "execution_status) VALUES (%(c)s, %(v)s, %(corr)s, %(s)s) RETURNING id",
                {"c": delivery.campaign_id, "v": delivery.campaign_version_snapshot,
                 "corr": delivery.correlation_id, "s": delivery.execution_status},
            )
            delivery.id = cur.fetchone()["id"]
        return delivery

    def get_recipients_for_delivery(self, delivery_id: str) -> List[Recipient]:
        # يعتمد على idx مركَّب (delivery_id, user_ref_id) — نفس فهرس التفرّد
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM ntf.recipients WHERE delivery_id = %(d)s", {"d": delivery_id})
            rows = cur.fetchall()
        return [self._row_to_recipient(r) for r in rows]

    def insert_recipient(self, recipient: Recipient) -> Recipient:
        # قيد UNIQUE(delivery_id, user_ref_id) هو الضامن الفعلي لمنع التكرار (REQ-NTF-012)
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ntf.recipients (delivery_id, user_ref_id, channel_provider_code, status) "
                "VALUES (%(d)s, %(u)s, %(c)s, %(s)s) RETURNING id",
                {"d": recipient.delivery_id, "u": recipient.user_ref_id,
                 "c": recipient.channel_provider_code, "s": recipient.status},
            )
            recipient.id = cur.fetchone()["id"]
        return recipient

    def update_recipient(self, recipient: Recipient) -> Recipient:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE ntf.recipients SET status = %(s)s, sent_at = %(sa)s, delivered_at = %(da)s, "
                    "read_at = %(ra)s, failure_reason_code = %(fr)s WHERE id = %(id)s",
                    {"s": recipient.status, "sa": recipient.sent_at, "da": recipient.delivered_at,
                     "ra": recipient.read_at, "fr": recipient.failure_reason_code, "id": recipient.id},
                )
        return recipient

    def insert_outbox_entry(self, delivery_id: str, recipient_id: str, correlation_id: str) -> str:
        # يُستدعى ضمن نفس معاملة insert_recipient تمامًا (نمط Transactional Outbox)
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ntf.outbox (delivery_id, recipient_id, correlation_id, dispatched) "
                "VALUES (%(d)s, %(r)s, %(corr)s, false) RETURNING id",
                {"d": delivery_id, "r": recipient_id, "corr": correlation_id},
            )
            return cur.fetchone()["id"]

    def get_pending_outbox_entries(self) -> List[dict]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM ntf.outbox WHERE dispatched = false ORDER BY created_at LIMIT 100")
            return cur.fetchall()

    def mark_outbox_entry_dispatched(self, outbox_entry_id: str) -> None:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute("UPDATE ntf.outbox SET dispatched = true WHERE id = %(id)s", {"id": outbox_entry_id})

    def insert_template(self, template: Template, initial_version: TemplateVersion) -> Template:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute("INSERT INTO ntf.templates (code, status) VALUES (%(c)s, %(s)s) RETURNING id",
                            {"c": template.code, "s": template.status})
                template.id = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO ntf.template_versions (template_id, version_number, title, body) "
                    "VALUES (%(t)s, 1, %(ti)s, %(b)s) RETURNING id",
                    {"t": template.id, "ti": initial_version.title, "b": initial_version.body},
                )
        return template

    def insert_template_version(self, version: TemplateVersion) -> TemplateVersion:
        # جدول Append-Only بالكامل: INSERT فقط، لا UPDATE على صف قائم أبدًا (BR-NTF-006)
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ntf.template_versions (template_id, version_number, title, body) "
                "VALUES (%(t)s, %(v)s, %(ti)s, %(b)s) RETURNING id",
                {"t": version.template_id, "v": version.version_number, "ti": version.title, "b": version.body},
            )
            version.id = cur.fetchone()["id"]
        return version

    def get_channel_provider(self, code: str) -> Optional[ChannelProviderInfo]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM ntf.channel_providers WHERE code = %(c)s", {"c": code})
            row = cur.fetchone()
        return ChannelProviderInfo(code=row["code"], display_name=row["display_name"],
                                    health_status=row["health_status"], is_enabled=row["is_enabled"]) if row else None

    def update_channel_provider(self, provider: ChannelProviderInfo) -> ChannelProviderInfo:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute("UPDATE ntf.channel_providers SET health_status = %(h)s WHERE code = %(c)s",
                            {"h": provider.health_status, "c": provider.code})
        return provider

    def get_preferences_for_user(self, user_ref_id: str) -> List[NotificationPreference]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM ntf.notification_preferences WHERE user_ref_id = %(u)s", {"u": user_ref_id})
            rows = cur.fetchall()
        return [NotificationPreference(id=r["id"], user_ref_id=r["user_ref_id"],
                                        channel_provider_code=r["channel_provider_code"],
                                        notification_type=r["notification_type"], is_enabled=r["is_enabled"]) for r in rows]

    def insert_notification_center_entry(self, entry: NotificationCenterEntry) -> NotificationCenterEntry:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ntf.notification_center_entries (recipient_id, user_ref_id, is_read, "
                "is_archived_by_user, is_deleted_by_user) VALUES (%(r)s, %(u)s, %(ir)s, %(ia)s, %(id_)s) RETURNING id",
                {"r": entry.recipient_id, "u": entry.user_ref_id, "ir": entry.is_read,
                 "ia": entry.is_archived_by_user, "id_": entry.is_deleted_by_user},
            )
            entry.id = cur.fetchone()["id"]
        return entry

    def get_notification_center_entries_for_user(self, user_ref_id: str) -> List[NotificationCenterEntry]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM ntf.notification_center_entries WHERE user_ref_id = %(u)s", {"u": user_ref_id})
            rows = cur.fetchall()
        return [self._row_to_nc_entry(r) for r in rows]

    def update_notification_center_entry(self, entry: NotificationCenterEntry) -> NotificationCenterEntry:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE ntf.notification_center_entries SET is_read = %(ir)s, is_archived_by_user = %(ia)s, "
                    "is_deleted_by_user = %(id_)s WHERE id = %(id)s",
                    {"ir": entry.is_read, "ia": entry.is_archived_by_user,
                     "id_": entry.is_deleted_by_user, "id": entry.id},
                )
        return entry

    @staticmethod
    def _row_to_recipient(row) -> Recipient:
        return Recipient(id=row["id"], delivery_id=row["delivery_id"], user_ref_id=row["user_ref_id"],
                          channel_provider_code=row["channel_provider_code"], status=row["status"],
                          sent_at=row["sent_at"], delivered_at=row["delivered_at"], read_at=row["read_at"],
                          failure_reason_code=row["failure_reason_code"])

    @staticmethod
    def _row_to_nc_entry(row) -> NotificationCenterEntry:
        return NotificationCenterEntry(id=row["id"], recipient_id=row["recipient_id"], user_ref_id=row["user_ref_id"],
                                        is_read=row["is_read"], is_archived_by_user=row["is_archived_by_user"],
                                        is_deleted_by_user=row["is_deleted_by_user"])


class InMemoryNtfRepository(NtfRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف فعلي هنا أيضًا، عمدًا."""

    def __init__(self):
        self._campaigns = {}
        self._deliveries = {}
        self._recipients = {}
        self._outbox = {}
        self._templates = {}
        self._template_versions = []
        self._channel_providers = {}
        self._preferences = []
        self._nc_entries = {}
        self._seq = {k: 1 for k in ["campaign", "delivery", "recipient", "outbox", "template", "version", "pref", "nc"]}

    def _next_id(self, kind):
        n = self._seq[kind]
        self._seq[kind] += 1
        return f"{kind}-{n}"

    def insert_campaign(self, campaign: Campaign) -> Campaign:
        campaign.id = self._next_id("campaign")
        self._campaigns[campaign.id] = campaign
        return campaign

    def get_campaign_by_id(self, campaign_id: str) -> Optional[Campaign]:
        return self._campaigns.get(campaign_id)

    def update_campaign(self, campaign: Campaign) -> Campaign:
        self._campaigns[campaign.id] = campaign
        return campaign

    def insert_delivery(self, delivery: Delivery) -> Delivery:
        delivery.id = self._next_id("delivery")
        self._deliveries[delivery.id] = delivery
        return delivery

    def get_recipients_for_delivery(self, delivery_id: str) -> List[Recipient]:
        return [r for r in self._recipients.values() if r.delivery_id == delivery_id]

    def insert_recipient(self, recipient: Recipient) -> Recipient:
        # يحاكي قيد UNIQUE(delivery_id, user_ref_id)
        for existing in self._recipients.values():
            if existing.delivery_id == recipient.delivery_id and existing.user_ref_id == recipient.user_ref_id:
                raise ValueError("قيد تفرّد: مستلِم مكرَّر لنفس عملية التنفيذ.")
        recipient.id = self._next_id("recipient")
        self._recipients[recipient.id] = recipient
        return recipient

    def update_recipient(self, recipient: Recipient) -> Recipient:
        self._recipients[recipient.id] = recipient
        return recipient

    def insert_outbox_entry(self, delivery_id: str, recipient_id: str, correlation_id: str) -> str:
        entry_id = self._next_id("outbox")
        self._outbox[entry_id] = {"id": entry_id, "delivery_id": delivery_id, "recipient_id": recipient_id,
                                   "correlation_id": correlation_id, "dispatched": False}
        return entry_id

    def get_pending_outbox_entries(self) -> List[dict]:
        return [e for e in self._outbox.values() if not e["dispatched"]]

    def mark_outbox_entry_dispatched(self, outbox_entry_id: str) -> None:
        self._outbox[outbox_entry_id]["dispatched"] = True

    def insert_template(self, template: Template, initial_version: TemplateVersion) -> Template:
        template.id = self._next_id("template")
        initial_version.template_id = template.id
        initial_version.id = self._next_id("version")
        self._templates[template.id] = template
        self._template_versions.append(initial_version)
        return template

    def insert_template_version(self, version: TemplateVersion) -> TemplateVersion:
        version.id = self._next_id("version")
        self._template_versions.append(version)
        return version

    def get_channel_provider(self, code: str) -> Optional[ChannelProviderInfo]:
        return self._channel_providers.get(code)

    def update_channel_provider(self, provider: ChannelProviderInfo) -> ChannelProviderInfo:
        self._channel_providers[provider.code] = provider
        return provider

    def get_preferences_for_user(self, user_ref_id: str) -> List[NotificationPreference]:
        return [p for p in self._preferences if p.user_ref_id == user_ref_id]

    def insert_notification_center_entry(self, entry: NotificationCenterEntry) -> NotificationCenterEntry:
        entry.id = self._next_id("nc")
        self._nc_entries[entry.id] = entry
        return entry

    def get_notification_center_entries_for_user(self, user_ref_id: str) -> List[NotificationCenterEntry]:
        return [e for e in self._nc_entries.values() if e.user_ref_id == user_ref_id]

    def update_notification_center_entry(self, entry: NotificationCenterEntry) -> NotificationCenterEntry:
        self._nc_entries[entry.id] = entry
        return entry
