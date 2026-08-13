-- ============================================================
-- 011_com.sql — وحدة التواصل (COM)
-- المرجع: DD الحزمة 1 (قسم COM)؛ REQ-COM-001..010
-- الاعتماديات: iam، pur — بالإشارة المرجعية
-- ============================================================

CREATE TABLE com.conversations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_type     VARCHAR(24) NOT NULL,  -- طلب شراء / عنصر مخزون REQ-COM-002
    context_ref_id   UUID NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_conversations_context_type CHECK (context_type IN ('purchase_request', 'inventory_item'))
);
COMMENT ON TABLE com.conversations IS 'REQ-COM-001, 002, 010: محادثة مرتبطة بسياق محدد، تبقى مفتوحة بعد إغلاق الطلب';
CREATE INDEX idx_conversations_context ON com.conversations (context_type, context_ref_id);

CREATE TABLE com.messages (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id           UUID NOT NULL REFERENCES com.conversations(id),
    sender_user_ref_id        UUID NOT NULL,  -- إشارة مرجعية لـ iam.users
    body                      TEXT NOT NULL,
    is_deleted_by_sender      BOOLEAN NOT NULL DEFAULT false,  -- REQ-COM-007: حذف نسبي لا حالة عامة
    is_deleted_by_recipient   BOOLEAN NOT NULL DEFAULT false,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE com.messages IS 'REQ-COM-001, 007: الرسالة، بحذف منطقي نسبي لكل طرف';
CREATE INDEX idx_messages_conversation_id ON com.messages (conversation_id);
