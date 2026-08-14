"""
message_repository.py — طبقة الوصول للبيانات لخدمة التواصل (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.7؛ 011_com.sql

لا دالة حذف فعلي في هذا الملف؛ update_message فقط، يعكس تبديل أعلام
الحذف النسبي لكل طرف (is_deleted_by_sender/recipient).
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from message_service import Conversation, Message


class MessageRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه message_service.py. لا دالة حذف عمدًا."""

    @abstractmethod
    def insert_conversation(self, conversation: Conversation) -> Conversation:
        raise NotImplementedError

    @abstractmethod
    def get_conversation_by_context(self, context_type: str, context_ref_id: str) -> Optional[Conversation]:
        raise NotImplementedError

    @abstractmethod
    def insert_message(self, message: Message) -> Message:
        raise NotImplementedError

    @abstractmethod
    def get_messages_for_conversation(self, conversation_id: str) -> List[Message]:
        raise NotImplementedError

    @abstractmethod
    def update_message(self, message: Message) -> Message:
        raise NotImplementedError

    # -----------------------------------------------------------------
    # CR-015: عضوية صريحة (027_com_conversation_participants.sql)
    # -----------------------------------------------------------------

    @abstractmethod
    def add_participant_if_missing(self, conversation_id: str, user_ref_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_participant(self, conversation_id: str, user_ref_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_participant_ids(self, conversation_id: str) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def list_conversations_for_user(self, user_ref_id: str, page: int, page_size: int) -> "tuple[List[Conversation], int]":
        """يعيد (العناصر، إجمالي العدد) — الفرز الافتراضي: الأحدث نشاطًا أولًا."""
        raise NotImplementedError

    @abstractmethod
    def get_last_message_for_conversation(self, conversation_id: str) -> Optional[Message]:
        raise NotImplementedError


class PostgresMessageRepository(MessageRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 011_com.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_conversation(self, conversation: Conversation) -> Conversation:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO com.conversations (context_type, context_ref_id) "
                "VALUES (%(context_type)s, %(context_ref_id)s) RETURNING id, created_at",
                {"context_type": conversation.context_type, "context_ref_id": conversation.context_ref_id},
            )
            row = cur.fetchone()
            conversation.id = row["id"]
            conversation.created_at = row["created_at"]
        return conversation

    def get_conversation_by_context(self, context_type: str, context_ref_id: str) -> Optional[Conversation]:
        # يعتمد على idx_conversations_context
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, context_type, context_ref_id, created_at FROM com.conversations "
                "WHERE context_type = %(context_type)s AND context_ref_id = %(context_ref_id)s",
                {"context_type": context_type, "context_ref_id": context_ref_id},
            )
            row = cur.fetchone()
        return (Conversation(id=row["id"], context_type=row["context_type"], context_ref_id=row["context_ref_id"],
                              created_at=row["created_at"]) if row else None)

    def insert_message(self, message: Message) -> Message:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO com.messages (conversation_id, sender_user_ref_id, body, "
                "is_deleted_by_sender, is_deleted_by_recipient) VALUES (%(conversation_id)s, "
                "%(sender_user_ref_id)s, %(body)s, %(is_deleted_by_sender)s, %(is_deleted_by_recipient)s) "
                "RETURNING id, created_at",
                {"conversation_id": message.conversation_id, "sender_user_ref_id": message.sender_user_ref_id,
                 "body": message.body, "is_deleted_by_sender": message.is_deleted_by_sender,
                 "is_deleted_by_recipient": message.is_deleted_by_recipient},
            )
            row = cur.fetchone()
            message.id = row["id"]
            message.created_at = row["created_at"]
        return message

    def get_messages_for_conversation(self, conversation_id: str) -> List[Message]:
        # يعتمد على idx_messages_conversation_id
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, conversation_id, sender_user_ref_id, body, is_deleted_by_sender, "
                "is_deleted_by_recipient, created_at FROM com.messages WHERE conversation_id = %(cid)s "
                "ORDER BY created_at ASC",
                {"cid": conversation_id},
            )
            rows = cur.fetchall()
        return [self._row_to_message(r) for r in rows]

    @staticmethod
    def _row_to_message(row) -> Message:
        return Message(id=row["id"], conversation_id=row["conversation_id"], sender_user_ref_id=row["sender_user_ref_id"],
                        body=row["body"], is_deleted_by_sender=row["is_deleted_by_sender"],
                        is_deleted_by_recipient=row["is_deleted_by_recipient"], created_at=row["created_at"])

    def update_message(self, message: Message) -> Message:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE com.messages SET is_deleted_by_sender = %(is_deleted_by_sender)s, "
                    "is_deleted_by_recipient = %(is_deleted_by_recipient)s WHERE id = %(id)s",
                    {"is_deleted_by_sender": message.is_deleted_by_sender,
                     "is_deleted_by_recipient": message.is_deleted_by_recipient, "id": message.id},
                )
        return message

    # -----------------------------------------------------------------
    # CR-015: عضوية صريحة (027_com_conversation_participants.sql)
    # -----------------------------------------------------------------

    def add_participant_if_missing(self, conversation_id: str, user_ref_id: str) -> None:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO com.conversation_participants (conversation_id, user_ref_id) "
                    "VALUES (%(cid)s, %(uid)s) ON CONFLICT (conversation_id, user_ref_id) DO NOTHING",
                    {"cid": conversation_id, "uid": user_ref_id},
                )

    def is_participant(self, conversation_id: str, user_ref_id: str) -> bool:
        # يعتمد على uq_conversation_participants (فهرس التفرّد يخدم البحث أيضًا)
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM com.conversation_participants "
                "WHERE conversation_id = %(cid)s AND user_ref_id = %(uid)s",
                {"cid": conversation_id, "uid": user_ref_id},
            )
            return cur.fetchone() is not None

    def get_participant_ids(self, conversation_id: str) -> List[str]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT user_ref_id FROM com.conversation_participants WHERE conversation_id = %(cid)s",
                {"cid": conversation_id},
            )
            return [r["user_ref_id"] for r in cur.fetchall()]

    def list_conversations_for_user(self, user_ref_id: str, page: int, page_size: int):
        # يعتمد على idx_conversation_participants_user_ref_id؛ الأحدث نشاطًا
        # (بحسب آخر رسالة فعلية، لا وقت إنشاء المحادثة) أولًا.
        offset = (page - 1) * page_size
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM com.conversation_participants WHERE user_ref_id = %(uid)s",
                {"uid": user_ref_id},
            )
            total = cur.fetchone()["total"]
            cur.execute(
                "SELECT c.id, c.context_type, c.context_ref_id, c.created_at "
                "FROM com.conversations c "
                "JOIN com.conversation_participants cp ON cp.conversation_id = c.id "
                "LEFT JOIN LATERAL ("
                "  SELECT created_at FROM com.messages m "
                "  WHERE m.conversation_id = c.id ORDER BY m.created_at DESC LIMIT 1"
                ") last_msg ON true "
                "WHERE cp.user_ref_id = %(uid)s "
                "ORDER BY last_msg.created_at DESC NULLS LAST, c.created_at DESC "
                "LIMIT %(limit)s OFFSET %(offset)s",
                {"uid": user_ref_id, "limit": page_size, "offset": offset},
            )
            rows = cur.fetchall()
        items = [Conversation(id=r["id"], context_type=r["context_type"], context_ref_id=r["context_ref_id"],
                               created_at=r["created_at"]) for r in rows]
        return items, total

    def get_last_message_for_conversation(self, conversation_id: str) -> Optional[Message]:
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, conversation_id, sender_user_ref_id, body, is_deleted_by_sender, "
                "is_deleted_by_recipient, created_at FROM com.messages "
                "WHERE conversation_id = %(cid)s ORDER BY created_at DESC LIMIT 1",
                {"cid": conversation_id},
            )
            row = cur.fetchone()
        return self._row_to_message(row) if row else None


class InMemoryMessageRepository(MessageRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف هنا أيضًا، عمدًا."""

    def __init__(self):
        self._conversations = {}
        self._messages = {}
        self._participants = set()  # {(conversation_id, user_ref_id)} — يحاكي uq_conversation_participants
        self._seq = {"conv": 1, "msg": 1}
        # ترتيب زمني تصاعدي حقيقي بديل عن DEFAULT now() في Postgres — datetime
        # فعلي، لا int. الخطأ الجذري (Gate 1) كان هنا تحديدًا: النسخة السابقة
        # أعادت عدادًا صحيحًا (int) وأسندته مباشرة إلى created_at، بينما العقد
        # المعلَن Optional[datetime] في message_service.py وسلوك Postgres
        # الفعلي (row["created_at"] من psycopg2) كلاهما datetime دائمًا. لم
        # تكن المشكلة في _to_response() ولا في .isoformat() — ذاك السلوك
        # صحيح ومطلوب؛ المشكلة في مصدر القيمة هنا فقط.
        self._clock_base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self._clock_ticks = 0

    def _next_tick(self) -> datetime:
        self._clock_ticks += 1
        return self._clock_base + timedelta(microseconds=self._clock_ticks)

    def insert_conversation(self, conversation: Conversation) -> Conversation:
        conversation.id = f"conv-{self._seq['conv']}"
        self._seq["conv"] += 1
        self._conversations[conversation.id] = conversation
        conversation.created_at = self._next_tick()
        return conversation

    def get_conversation_by_context(self, context_type: str, context_ref_id: str) -> Optional[Conversation]:
        for conv in self._conversations.values():
            if conv.context_type == context_type and conv.context_ref_id == context_ref_id:
                return conv
        return None

    def insert_message(self, message: Message) -> Message:
        message.id = f"msg-{self._seq['msg']}"
        self._seq["msg"] += 1
        message.created_at = self._next_tick()
        self._messages[message.id] = message
        return message

    def get_messages_for_conversation(self, conversation_id: str) -> List[Message]:
        msgs = [m for m in self._messages.values() if m.conversation_id == conversation_id]
        # created_at مضمون الوجود دائمًا لأي رسالة أُدرجت عبر insert_message
        # أعلاه؛ لا حاجة لقيمة بديلة عند الفرز (والبديل السابق "or 0" كان
        # سيكسر المقارنة بين datetime وint أصلًا لو تحقّق فعليًا).
        return sorted(msgs, key=lambda m: m.created_at)

    def update_message(self, message: Message) -> Message:
        self._messages[message.id] = message
        return message

    # -----------------------------------------------------------------
    # CR-015: عضوية صريحة — محاكاة في الذاكرة للاختبارات
    # -----------------------------------------------------------------

    def add_participant_if_missing(self, conversation_id: str, user_ref_id: str) -> None:
        self._participants.add((conversation_id, user_ref_id))

    def is_participant(self, conversation_id: str, user_ref_id: str) -> bool:
        return (conversation_id, user_ref_id) in self._participants

    def get_participant_ids(self, conversation_id: str) -> List[str]:
        return [uid for (cid, uid) in self._participants if cid == conversation_id]

    def list_conversations_for_user(self, user_ref_id: str, page: int, page_size: int):
        conv_ids = {cid for (cid, uid) in self._participants if uid == user_ref_id}
        convs = [self._conversations[cid] for cid in conv_ids if cid in self._conversations]

        def _last_activity(conv):
            last = self.get_last_message_for_conversation(conv.id)
            return last.created_at if last else conv.created_at

        convs.sort(key=_last_activity, reverse=True)
        total = len(convs)
        start = (page - 1) * page_size
        return convs[start:start + page_size], total

    def get_last_message_for_conversation(self, conversation_id: str) -> Optional[Message]:
        msgs = self.get_messages_for_conversation(conversation_id)
        return msgs[-1] if msgs else None
