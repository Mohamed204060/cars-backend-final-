-- 025_sys_idempotency_keys.sql
-- Governance: يوثَّق كتحديث خفيف ضمن Store+Inventory Contract Extension.
-- الغرض: تنفيذ DD الحزمة 2، القسم 2.2 (سياسة عدم التكرار للعمليات الحساسة) —
-- إعادة إرسال نفس الطلب بنفس Idempotency-Key يجب أن تُعيد النتيجة الأصلية
-- دون تنفيذ العملية مرة ثانية.
--
-- لا تعديل على أي Migration تاريخية (000-024)؛ ملف جديد ومستقل بالكامل.

BEGIN;

CREATE TABLE sys.idempotency_keys (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key  VARCHAR(128) NOT NULL,
    user_ref_id      UUID NOT NULL,
    endpoint         VARCHAR(128) NOT NULL,
    response_status  INTEGER NOT NULL,
    response_body    JSONB NOT NULL,
    created_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),

    -- نفس المفتاح لنفس المستخدم على نفس العملية يجب أن يُطابَق لمحاولة واحدة فقط
    CONSTRAINT uq_idempotency_key_user_endpoint UNIQUE (idempotency_key, user_ref_id, endpoint)
);

COMMENT ON TABLE sys.idempotency_keys IS
    'DD الحزمة 2، القسم 2.2: تخزين نتيجة أول تنفيذ ناجح لكل (مفتاح، مستخدم، '
    'عملية)؛ أي إعادة إرسال بنفس المفتاح تُعيد هذا السجل حرفيًا دون تنفيذ '
    'العملية من جديد. لا يُخزَّن سوى الاستجابات الناجحة (2xx)، لا الأخطاء.';

CREATE INDEX idx_idempotency_keys_lookup ON sys.idempotency_keys (idempotency_key, user_ref_id, endpoint);

COMMIT;
