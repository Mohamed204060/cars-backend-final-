-- ============================================================
-- 020_com_extended.sql — توسعة مجال التواصل (Messaging Extended)
-- المرجع: CR-007 v1.4؛ CR-010؛ SRS الحزمة D v1.6
-- الاعتماديات: com.conversations، com.messages (011_com.sql)، iam (بالإشارة)
-- الحالة: Prepared — لم يُطبَّق على أي قاعدة بيانات فعلية بعد
-- ============================================================

CREATE TABLE com.user_presence (
    user_ref_id   UUID PRIMARY KEY,      -- إشارة مرجعية لـ iam.users
    is_online     BOOLEAN NOT NULL DEFAULT false,
    last_seen_at  TIMESTAMPTZ
);
COMMENT ON TABLE com.user_presence IS 'REQ-COM-028, 029: آخر ظهور وحالة الاتصال';

CREATE TABLE com.message_delivery_tracking (
    message_id    UUID PRIMARY KEY REFERENCES com.messages(id),
    sent_at       TIMESTAMPTZ NOT NULL,
    delivered_at  TIMESTAMPTZ,
    read_at       TIMESTAMPTZ
);
COMMENT ON TABLE com.message_delivery_tracking IS 'REQ-COM-015, 031: طوابع زمنية للتسليم والقراءة؛ سجل مرتبط لا حقل مباشر على الرسالة';

CREATE TABLE com.message_thread_links (
    message_id           UUID PRIMARY KEY REFERENCES com.messages(id),
    reply_to_message_id  UUID NOT NULL REFERENCES com.messages(id)
);
COMMENT ON TABLE com.message_thread_links IS 'REQ-COM-024: الرد على رسالة سابقة';

CREATE TABLE com.forward_records (
    id                             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_message_id           UUID NOT NULL REFERENCES com.messages(id),
    forwarded_message_id          UUID NOT NULL REFERENCES com.messages(id),
    forwarded_to_conversation_id  UUID NOT NULL REFERENCES com.conversations(id)
);
COMMENT ON TABLE com.forward_records IS 'REQ-COM-025: إعادة توجيه رسالة لمحادثة أخرى';

CREATE TABLE com.attachments (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id   UUID NOT NULL REFERENCES com.messages(id),
    file_name    VARCHAR(255) NOT NULL,
    mime_type    VARCHAR(64) NOT NULL,
    size_bytes   BIGINT NOT NULL,

    CONSTRAINT chk_attachments_size CHECK (size_bytes > 0 AND size_bytes <= 10485760)  -- REQ-COM-016: 10 ميغابايت
);
COMMENT ON TABLE com.attachments IS 'REQ-COM-016: حد أدنى أمني (نوع/حجم) مطبَّق أيضًا في طبقة منطق الأعمال قبل الوصول هنا';
CREATE INDEX idx_attachments_message_id ON com.attachments (message_id);

CREATE TABLE com.conversation_user_settings (
    conversation_id  UUID NOT NULL REFERENCES com.conversations(id),
    user_ref_id      UUID NOT NULL,        -- إشارة مرجعية لـ iam.users
    is_muted         BOOLEAN NOT NULL DEFAULT false,
    is_archived      BOOLEAN NOT NULL DEFAULT false,

    PRIMARY KEY (conversation_id, user_ref_id)
);
COMMENT ON TABLE com.conversation_user_settings IS 'REQ-COM-021, 023: كتم/أرشفة مستقلَّان لكل مستخدم على حدة، لا يؤثران على الطرف الآخر';
