"""
message_repository.py — طبقة الوصول للبيانات لخدمة التواصل (Repository Pattern)
المرجع: دليل حوكمة التنفيذ v1.7؛ 011_com.sql

لا دالة حذف فعلي في هذا الملف؛ update_message فقط، يعكس تبديل أعلام
الحذف النسبي لكل طرف (is_deleted_by_sender/recipient).
"""

from abc import ABC, abstractmethod
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


class PostgresMessageRepository(MessageRepository):
    """تنفيذ فعلي عبر PostgreSQL وفق مخطط 011_com.sql. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_conversation(self, conversation: Conversation) -> Conversation:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO com.conversations (context_type, context_ref_id) "
                "VALUES (%(context_type)s, %(context_ref_id)s) RETURNING id",
                {"context_type": conversation.context_type, "context_ref_id": conversation.context_ref_id},
            )
            conversation.id = cur.fetchone()["id"]
        return conversation

    def get_conversation_by_context(self, context_type: str, context_ref_id: str) -> Optional[Conversation]:
        # يعتمد على idx_conversations_context
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, context_type, context_ref_id FROM com.conversations "
                "WHERE context_type = %(context_type)s AND context_ref_id = %(context_ref_id)s",
                {"context_type": context_type, "context_ref_id": context_ref_id},
            )
            row = cur.fetchone()
        return Conversation(id=row["id"], context_type=row["context_type"], context_ref_id=row["context_ref_id"]) if row else None

    def insert_message(self, message: Message) -> Message:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO com.messages (conversation_id, sender_user_ref_id, body, "
                "is_deleted_by_sender, is_deleted_by_recipient) VALUES (%(conversation_id)s, "
                "%(sender_user_ref_id)s, %(body)s, %(is_deleted_by_sender)s, %(is_deleted_by_recipient)s) RETURNING id",
                {"conversation_id": message.conversation_id, "sender_user_ref_id": message.sender_user_ref_id,
                 "body": message.body, "is_deleted_by_sender": message.is_deleted_by_sender,
                 "is_deleted_by_recipient": message.is_deleted_by_recipient},
            )
            message.id = cur.fetchone()["id"]
        return message

    def get_messages_for_conversation(self, conversation_id: str) -> List[Message]:
        # يعتمد على idx_messages_conversation_id
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT id, conversation_id, sender_user_ref_id, body, is_deleted_by_sender, "
                "is_deleted_by_recipient FROM com.messages WHERE conversation_id = %(cid)s",
                {"cid": conversation_id},
            )
            rows = cur.fetchall()
        return [Message(id=r["id"], conversation_id=r["conversation_id"], sender_user_ref_id=r["sender_user_ref_id"],
                         body=r["body"], is_deleted_by_sender=r["is_deleted_by_sender"],
                         is_deleted_by_recipient=r["is_deleted_by_recipient"]) for r in rows]

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


class InMemoryMessageRepository(MessageRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف هنا أيضًا، عمدًا."""

    def __init__(self):
        self._conversations = {}
        self._messages = {}
        self._seq = {"conv": 1, "msg": 1}

    def insert_conversation(self, conversation: Conversation) -> Conversation:
        conversation.id = f"conv-{self._seq['conv']}"
        self._seq["conv"] += 1
        self._conversations[conversation.id] = conversation
        return conversation

    def get_conversation_by_context(self, context_type: str, context_ref_id: str) -> Optional[Conversation]:
        for conv in self._conversations.values():
            if conv.context_type == context_type and conv.context_ref_id == context_ref_id:
                return conv
        return None

    def insert_message(self, message: Message) -> Message:
        message.id = f"msg-{self._seq['msg']}"
        self._seq["msg"] += 1
        self._messages[message.id] = message
        return message

    def get_messages_for_conversation(self, conversation_id: str) -> List[Message]:
        return [m for m in self._messages.values() if m.conversation_id == conversation_id]

    def update_message(self, message: Message) -> Message:
        self._messages[message.id] = message
        return message
