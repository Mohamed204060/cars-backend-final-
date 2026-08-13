"""
message_extended_repository.py — طبقة الوصول للبيانات لتوسعة خدمة التواصل
المرجع: دليل حوكمة التنفيذ v1.7

لا دالة حذف فعلي عمدًا؛ الأرشفة أو تعطيل العلم فقط.
"""

from abc import ABC, abstractmethod
from typing import Optional, List

from message_extended_service import (
    UserPresence, MessageDeliveryTracking, MessageThreadLink, ForwardRecord,
    Attachment, ConversationUserSettings,
)


class MessageExtendedRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه message_extended_service.py. لا دالة حذف عمدًا."""

    @abstractmethod
    def get_presence(self, user_ref_id: str) -> Optional[UserPresence]: raise NotImplementedError

    @abstractmethod
    def upsert_presence(self, presence: UserPresence) -> UserPresence: raise NotImplementedError

    @abstractmethod
    def insert_delivery_tracking(self, tracking: MessageDeliveryTracking) -> MessageDeliveryTracking:
        raise NotImplementedError

    @abstractmethod
    def get_delivery_tracking(self, message_id: str) -> Optional[MessageDeliveryTracking]:
        raise NotImplementedError

    @abstractmethod
    def update_delivery_tracking(self, tracking: MessageDeliveryTracking) -> MessageDeliveryTracking:
        raise NotImplementedError

    @abstractmethod
    def insert_thread_link(self, link: MessageThreadLink) -> MessageThreadLink: raise NotImplementedError

    @abstractmethod
    def insert_forward_record(self, record: ForwardRecord) -> ForwardRecord: raise NotImplementedError

    @abstractmethod
    def insert_attachment(self, attachment: Attachment) -> Attachment: raise NotImplementedError

    @abstractmethod
    def get_attachments_for_message(self, message_id: str) -> List[Attachment]: raise NotImplementedError

    @abstractmethod
    def get_conversation_user_settings(self, conversation_id: str, user_ref_id: str) -> Optional[ConversationUserSettings]:
        raise NotImplementedError

    @abstractmethod
    def upsert_conversation_user_settings(self, settings: ConversationUserSettings) -> ConversationUserSettings:
        raise NotImplementedError


class PostgresMessageExtendedRepository(MessageExtendedRepository):
    """تنفيذ فعلي عبر PostgreSQL. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def get_presence(self, user_ref_id: str) -> Optional[UserPresence]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM com.user_presence WHERE user_ref_id = %(u)s", {"u": user_ref_id})
            row = cur.fetchone()
        return UserPresence(user_ref_id=row["user_ref_id"], is_online=row["is_online"],
                             last_seen_at=row["last_seen_at"]) if row else None

    def upsert_presence(self, presence: UserPresence) -> UserPresence:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO com.user_presence (user_ref_id, is_online, last_seen_at) "
                    "VALUES (%(u)s, %(io)s, %(ls)s) "
                    "ON CONFLICT (user_ref_id) DO UPDATE SET is_online = %(io)s, last_seen_at = %(ls)s",
                    {"u": presence.user_ref_id, "io": presence.is_online, "ls": presence.last_seen_at},
                )
        return presence

    def insert_delivery_tracking(self, tracking: MessageDeliveryTracking) -> MessageDeliveryTracking:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO com.message_delivery_tracking (message_id, sent_at, delivered_at, read_at) "
                "VALUES (%(m)s, %(sa)s, %(da)s, %(ra)s)",
                {"m": tracking.message_id, "sa": tracking.sent_at, "da": tracking.delivered_at, "ra": tracking.read_at},
            )
        return tracking

    def get_delivery_tracking(self, message_id: str) -> Optional[MessageDeliveryTracking]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM com.message_delivery_tracking WHERE message_id = %(m)s", {"m": message_id})
            row = cur.fetchone()
        return MessageDeliveryTracking(message_id=row["message_id"], sent_at=row["sent_at"],
                                        delivered_at=row["delivered_at"], read_at=row["read_at"]) if row else None

    def update_delivery_tracking(self, tracking: MessageDeliveryTracking) -> MessageDeliveryTracking:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE com.message_delivery_tracking SET delivered_at = %(da)s, read_at = %(ra)s "
                    "WHERE message_id = %(m)s",
                    {"da": tracking.delivered_at, "ra": tracking.read_at, "m": tracking.message_id},
                )
        return tracking

    def insert_thread_link(self, link: MessageThreadLink) -> MessageThreadLink:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO com.message_thread_links (message_id, reply_to_message_id) VALUES (%(m)s, %(r)s)",
                {"m": link.message_id, "r": link.reply_to_message_id},
            )
        return link

    def insert_forward_record(self, record: ForwardRecord) -> ForwardRecord:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO com.forward_records (original_message_id, forwarded_message_id, "
                "forwarded_to_conversation_id) VALUES (%(o)s, %(f)s, %(c)s)",
                {"o": record.original_message_id, "f": record.forwarded_message_id,
                 "c": record.forwarded_to_conversation_id},
            )
        return record

    def insert_attachment(self, attachment: Attachment) -> Attachment:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO com.attachments (message_id, file_name, mime_type, size_bytes) "
                "VALUES (%(m)s, %(fn)s, %(mt)s, %(sz)s) RETURNING id",
                {"m": attachment.message_id, "fn": attachment.file_name,
                 "mt": attachment.mime_type, "sz": attachment.size_bytes},
            )
            attachment.id = cur.fetchone()["id"]
        return attachment

    def get_attachments_for_message(self, message_id: str) -> List[Attachment]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM com.attachments WHERE message_id = %(m)s", {"m": message_id})
            rows = cur.fetchall()
        return [Attachment(id=r["id"], message_id=r["message_id"], file_name=r["file_name"],
                            mime_type=r["mime_type"], size_bytes=r["size_bytes"]) for r in rows]

    def get_conversation_user_settings(self, conversation_id: str, user_ref_id: str) -> Optional[ConversationUserSettings]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM com.conversation_user_settings WHERE conversation_id = %(c)s AND user_ref_id = %(u)s",
                {"c": conversation_id, "u": user_ref_id},
            )
            row = cur.fetchone()
        return ConversationUserSettings(conversation_id=row["conversation_id"], user_ref_id=row["user_ref_id"],
                                         is_muted=row["is_muted"], is_archived=row["is_archived"]) if row else None

    def upsert_conversation_user_settings(self, settings: ConversationUserSettings) -> ConversationUserSettings:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO com.conversation_user_settings (conversation_id, user_ref_id, is_muted, is_archived) "
                    "VALUES (%(c)s, %(u)s, %(im)s, %(ia)s) "
                    "ON CONFLICT (conversation_id, user_ref_id) DO UPDATE SET is_muted = %(im)s, is_archived = %(ia)s",
                    {"c": settings.conversation_id, "u": settings.user_ref_id,
                     "im": settings.is_muted, "ia": settings.is_archived},
                )
        return settings


class InMemoryMessageExtendedRepository(MessageExtendedRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف هنا أيضًا، عمدًا."""

    def __init__(self):
        self._presence = {}
        self._tracking = {}
        self._thread_links = []
        self._forward_records = []
        self._attachments = {}
        self._conv_settings = {}
        self._next_attachment_seq = 1

    def get_presence(self, user_ref_id: str) -> Optional[UserPresence]:
        return self._presence.get(user_ref_id)

    def upsert_presence(self, presence: UserPresence) -> UserPresence:
        self._presence[presence.user_ref_id] = presence
        return presence

    def insert_delivery_tracking(self, tracking: MessageDeliveryTracking) -> MessageDeliveryTracking:
        self._tracking[tracking.message_id] = tracking
        return tracking

    def get_delivery_tracking(self, message_id: str) -> Optional[MessageDeliveryTracking]:
        return self._tracking.get(message_id)

    def update_delivery_tracking(self, tracking: MessageDeliveryTracking) -> MessageDeliveryTracking:
        self._tracking[tracking.message_id] = tracking
        return tracking

    def insert_thread_link(self, link: MessageThreadLink) -> MessageThreadLink:
        self._thread_links.append(link)
        return link

    def insert_forward_record(self, record: ForwardRecord) -> ForwardRecord:
        self._forward_records.append(record)
        return record

    def insert_attachment(self, attachment: Attachment) -> Attachment:
        attachment.id = f"attachment-{self._next_attachment_seq}"
        self._next_attachment_seq += 1
        self._attachments[attachment.id] = attachment
        return attachment

    def get_attachments_for_message(self, message_id: str) -> List[Attachment]:
        return [a for a in self._attachments.values() if a.message_id == message_id]

    def get_conversation_user_settings(self, conversation_id: str, user_ref_id: str) -> Optional[ConversationUserSettings]:
        return self._conv_settings.get((conversation_id, user_ref_id))

    def upsert_conversation_user_settings(self, settings: ConversationUserSettings) -> ConversationUserSettings:
        self._conv_settings[(settings.conversation_id, settings.user_ref_id)] = settings
        return settings
