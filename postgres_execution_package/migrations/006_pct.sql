-- ============================================================
-- 006_pct.sql — وحدة كتالوج قطع الغيار (PCT)
-- المرجع: DD الحزمة 1 (قسم PCT)؛ REQ-PCT-001..007
-- الاعتماديات: لا شيء إضافي (يشارك بنية "الاسم متعدد اللغات" مفاهيميًا مع VCT دون اعتمادية استدعاء)
-- ============================================================

CREATE TABLE pct.categories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE pct.categories IS 'REQ-PCT-007: تصنيف قطعة الكتالوج';

CREATE TABLE pct.catalog_parts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id  UUID NOT NULL REFERENCES pct.categories(id),
    status       VARCHAR(16) NOT NULL DEFAULT 'proposed',  -- REQ-PCT-002
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_catalog_parts_status CHECK (status IN ('proposed', 'approved', 'archived'))
);
COMMENT ON TABLE pct.catalog_parts IS 'REQ-PCT-001، 002، 007: قطعة الكتالوج المرجعية، مستقلة عن أي بائع';
CREATE INDEX idx_catalog_parts_status ON pct.catalog_parts (status);
CREATE INDEX idx_catalog_parts_category_id ON pct.catalog_parts (category_id);

CREATE TABLE pct.localized_names (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_part_id  UUID NOT NULL REFERENCES pct.catalog_parts(id),
    locale           VARCHAR(16),                 -- فارغ للاسم القياسي/المرادف العام
    name_value       VARCHAR(256) NOT NULL,
    name_kind        VARCHAR(16) NOT NULL,        -- قياسي/محلي/إنجليزي/مرادف
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_pct_localized_names_kind CHECK (name_kind IN ('canonical', 'local', 'english', 'synonym'))
);
COMMENT ON TABLE pct.localized_names IS 'REQ-PCT-003: الاسم القياسي والمحلي والإنجليزي والمرادفات';
CREATE INDEX idx_pct_localized_names_part_id ON pct.localized_names (catalog_part_id);
-- REQ-SRC-001/PCT-003: البحث بأي اسم يُرجِع نفس القطعة القياسية
CREATE INDEX idx_pct_localized_names_value ON pct.localized_names (name_value);

CREATE TABLE pct.oem_numbers (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_part_id  UUID NOT NULL REFERENCES pct.catalog_parts(id),
    manufacturer_ref_id UUID NOT NULL,  -- إشارة مرجعية للشركة المصنّعة في vct
    oem_number       VARCHAR(64) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- REQ-PCT-005: منع تكرار رقم OEM لنفس الشركة المصنّعة
    CONSTRAINT uq_oem_numbers_manufacturer_number UNIQUE (manufacturer_ref_id, oem_number)
);
COMMENT ON TABLE pct.oem_numbers IS 'REQ-PCT-004، 005: رقم أو أرقام OEM لكل قطعة، بلا تكرار ضمن نفس الشركة';
CREATE INDEX idx_oem_numbers_part_id ON pct.oem_numbers (catalog_part_id);

CREATE TABLE pct.aftermarket_numbers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_part_id     UUID NOT NULL REFERENCES pct.catalog_parts(id),
    aftermarket_number  VARCHAR(64) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE pct.aftermarket_numbers IS 'REQ-PCT-006: رقم القطعة البديلة، مرجع بديل لرقم OEM';
CREATE INDEX idx_aftermarket_numbers_part_id ON pct.aftermarket_numbers (catalog_part_id);
