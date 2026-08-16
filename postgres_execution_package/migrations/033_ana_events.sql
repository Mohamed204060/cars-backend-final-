-- ============================================================
-- 033_ana_events.sql — Batch 3A Slice 1: Analytics Event Foundation
-- المرجع: CarsMaint Reporting, Analytics, Intelligence & Regulatory
-- Reporting Catalog v1.0 §32 (Analytics Event Foundation)
-- الاعتماديات: لا شيء (نطاق مستقل بالكامل؛ actor_ref_id/session_ref_id/
-- context_ref_id إشارات مرجعية بلا FK لأي Domain آخر، نفس مبدأ SSOT
-- المعتمَد لكل حقول *_ref_id العابرة للنطاق في المشروع).
--
-- قرار حاكم صريح (مالك المشروع): خفيفة وموجَّهة للتقارير حصرًا — لا Event
-- Sourcing، لا إعادة تصميم لأي Domain حالي. هذه الهجرة تُنشئ الجدول فقط؛
-- ربط الأحداث الفعلية من order/search/inventory/إلخ مؤجَّل عمدًا لدفعة
-- تنفيذية لاحقة (Slice 2+) تتجنب تعديل أي وحدة مغلقة الآن.
--
-- ملاحظة: aud.events (سجل التدقيق الإداري/الأمني) موجود أصلًا منذ
-- 004_aud.sql — هذه الهجرة لا تلمسه إطلاقًا؛ ana.events منفصل تمامًا
-- (Analytics ≠ Audit): الأول لقياس سلوك الاستخدام لأغراض المنتج/التقارير،
-- والثاني لتتبع مسؤولية العمليات الحساسة/الإدارية.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS ana;

CREATE TABLE ana.events (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type        VARCHAR(64) NOT NULL,
    occurred_at_utc   TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_ref_id      UUID,        -- إشارة مرجعية لـ iam.users؛ NULL للتصفح المجهول قبل تسجيل الدخول
    session_ref_id    UUID,        -- إشارة مرجعية لـ iam.sessions عند توفرها؛ بلا FK
    context_type      VARCHAR(32), -- مثل 'purchase_request'/'inventory_item'/'catalog_part'؛ بلا Enum صارم عمدًا (نطاق تطوري)
    context_ref_id    UUID,
    correlation_id    UUID,
    metadata          JSONB,       -- حقول خاصة بالحدث فقط؛ Data Minimization — بلا PII حر (بريد/اسم/هاتف)

    CONSTRAINT chk_ana_events_type CHECK (event_type ~ '^[a-z][a-z0-9_]*$')
);
COMMENT ON TABLE ana.events IS 'Analytics Event Foundation (Batch 3A Slice 1): سجل أحداث خفيف موجَّه للتقارير والتحليلات فقط، وليس Event Sourcing لأي Domain. Append-only بنفس مبدأ aud.events (REQ-AUD-002/007 كمرجع تصميمي).';

CREATE INDEX idx_ana_events_type_time ON ana.events (event_type, occurred_at_utc);
CREATE INDEX idx_ana_events_context ON ana.events (context_type, context_ref_id);
CREATE INDEX idx_ana_events_actor ON ana.events (actor_ref_id);
CREATE INDEX idx_ana_events_correlation ON ana.events (correlation_id);

-- نفس نمط aud.events تمامًا: منع التعديل والحذف على مستوى قاعدة البيانات —
-- بيانات القياس نفسها يجب ألا تكون قابلة للتعديل التشغيلي بصمت.
REVOKE UPDATE, DELETE ON ana.events FROM PUBLIC;
