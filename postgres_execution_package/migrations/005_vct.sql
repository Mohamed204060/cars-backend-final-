-- ============================================================
-- 005_vct.sql — وحدة كتالوج السيارات (VCT)
-- المرجع: DD الحزمة 1 (قسم VCT)؛ REQ-VCT-001..007
-- الاعتماديات: ref (نوع الوقود/ناقل الحركة كمعرّف مرجعي)
-- ============================================================

CREATE TABLE vct.manufacturers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status      VARCHAR(16) NOT NULL DEFAULT 'proposed',  -- REQ-VCT-002
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_manufacturers_status CHECK (status IN ('proposed', 'approved', 'archived'))
);
COMMENT ON TABLE vct.manufacturers IS 'REQ-VCT-001، 002: الشركة المصنّعة مع دورة حوكمتها';
CREATE INDEX idx_manufacturers_status ON vct.manufacturers (status);

CREATE TABLE vct.models (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manufacturer_id  UUID NOT NULL REFERENCES vct.manufacturers(id),
    status           VARCHAR(16) NOT NULL DEFAULT 'proposed',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_models_status CHECK (status IN ('proposed', 'approved', 'archived'))
);
COMMENT ON TABLE vct.models IS 'REQ-VCT-003: الموديل، عضو في تجميع الشركة المصنّعة';
CREATE INDEX idx_models_manufacturer_id ON vct.models (manufacturer_id);
CREATE INDEX idx_models_status ON vct.models (status);

CREATE TABLE vct.generations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id    UUID NOT NULL REFERENCES vct.models(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE vct.generations IS 'REQ-VCT-004: الجيل، جزء تابع لتجميع الموديل';
CREATE INDEX idx_generations_model_id ON vct.generations (model_id);

CREATE TABLE vct.trims (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generation_id             UUID NOT NULL REFERENCES vct.generations(id),
    fuel_type_ref_id          UUID NOT NULL,  -- إشارة مرجعية لـ ref.ref_values (ref_type = fuel_type)
    transmission_type_ref_id  UUID NOT NULL,  -- إشارة مرجعية لـ ref.ref_values (ref_type = transmission_type)
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE vct.trims IS 'REQ-VCT-005: فئة السيارة، تشير لنوع الوقود وناقل الحركة كقيم مرجعية';
CREATE INDEX idx_trims_generation_id ON vct.trims (generation_id);

CREATE TABLE vct.localized_names (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_ref_id UUID NOT NULL,       -- يشير إلى manufacturers.id أو models.id
    owner_type   VARCHAR(16) NOT NULL,
    locale       VARCHAR(16) NOT NULL,
    name_value   VARCHAR(256) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_localized_names_owner_type CHECK (owner_type IN ('manufacturer', 'model'))
);
COMMENT ON TABLE vct.localized_names IS 'REQ-VCT-006: كائن القيمة المشترك "الاسم متعدد اللغات"';
CREATE INDEX idx_vct_localized_names_owner ON vct.localized_names (owner_ref_id, owner_type);
