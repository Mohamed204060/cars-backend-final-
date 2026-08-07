-- ============================================================
-- 003_sys.sql — وحدة إعدادات النظام (SYS)
-- المرجع: DD الحزمة 1 (قسم SYS)؛ REQ-SYS-001، 002
-- الاعتماديات: لا شيء (وحدة جذرية)
-- ============================================================

CREATE TABLE sys.settings (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    setting_key    VARCHAR(128) NOT NULL,
    setting_value  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_settings_key UNIQUE (setting_key)
);
COMMENT ON TABLE sys.settings IS 'REQ-SYS-001، 002: قيم كائنات السياسة والإعدادات التشغيلية العامة';
