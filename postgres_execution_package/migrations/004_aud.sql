-- ============================================================
-- 004_aud.sql — وحدة سجلات التدقيق (AUD)
-- المرجع: DD الحزمة 1 (قسم AUD)؛ REQ-AUD-001..012
-- الاعتماديات: لا شيء (تُستقبَل الأحداث من جميع الوحدات، لا تعتمد عليها)
-- ============================================================

CREATE TABLE aud.events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_type         VARCHAR(16) NOT NULL,   -- عام/أمني/إداري REQ-AUD-001/004/009
    correlation_id   UUID NOT NULL,          -- SAD الحزمة 4: معرّف التتبع الموحّد
    actor_ref_id     UUID,                   -- إشارة مرجعية لمستخدم في iam (قد تكون فارغة لأحداث نظامية)
    event_name       VARCHAR(128) NOT NULL,
    occurred_at_utc  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- REQ-AUD-012: UTC داخليًا
    before_value     JSONB,                  -- REQ-AUD-011
    after_value      JSONB,                  -- REQ-AUD-011
    reason           TEXT,                   -- REQ-AUD-010
    metadata         JSONB,                  -- حقول إضافية خاصة بالحدث (IP، User-Agent، إلخ لأحداث الدخول)

    CONSTRAINT chk_events_log_type CHECK (log_type IN ('general', 'security', 'administrative'))
);
COMMENT ON TABLE aud.events IS 'REQ-AUD-001..012: سجل تدقيق موحّد (Append-Only)؛ لا تعديل ولا حذف مسموح (REQ-AUD-002)';
CREATE INDEX idx_events_type_time ON aud.events (log_type, occurred_at_utc);
CREATE INDEX idx_events_correlation ON aud.events (correlation_id);
CREATE INDEX idx_events_actor ON aud.events (actor_ref_id);

-- REQ-AUD-002/007: منع التعديل والحذف على مستوى قاعدة البيانات
REVOKE UPDATE, DELETE ON aud.events FROM PUBLIC;
