-- ============================================================
-- 007_cmp.sql — وحدة التوافق (CMP)
-- المرجع: DD الحزمة 1 (قسم CMP)؛ REQ-CMP-001..003
-- الاعتماديات: pct، vct (بالإشارة المرجعية لا بمفتاح أجنبي فعلي، اتساقًا مع مبدأ عدم عبور الوحدات)
-- ============================================================

CREATE TABLE cmp.compatibility_records (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_part_ref_id UUID NOT NULL,  -- إشارة مرجعية لـ pct.catalog_parts
    trim_ref_id         UUID NOT NULL,  -- إشارة مرجعية لـ vct.trims
    status              VARCHAR(16) NOT NULL DEFAULT 'active',  -- REQ-CMP-003
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- REQ-CMP-002: منع تكرار سجل التوافق لنفس الزوج
    CONSTRAINT uq_compatibility_part_trim UNIQUE (catalog_part_ref_id, trim_ref_id),
    CONSTRAINT chk_compatibility_status CHECK (status IN ('active', 'archived'))
);
COMMENT ON TABLE cmp.compatibility_records IS 'REQ-CMP-001..003: سجل التوافق بين قطعة الكتالوج وفئة السيارة';
CREATE INDEX idx_compatibility_part ON cmp.compatibility_records (catalog_part_ref_id);
CREATE INDEX idx_compatibility_trim ON cmp.compatibility_records (trim_ref_id);
