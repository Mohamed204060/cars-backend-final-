-- ============================================================
-- 002_ref.sql — وحدة البيانات المرجعية (REF)
-- المرجع: DD الحزمة 1 (قسم REF)؛ REQ-REF-001..009 (يشمل إطار الاستيراد الجماعي، CR-003)
-- الاعتماديات: لا شيء (وحدة جذرية)
-- ============================================================

CREATE TABLE ref.ref_values (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ref_type    VARCHAR(32) NOT NULL,  -- REQ-REF-001
    code        VARCHAR(64) NOT NULL,
    status      VARCHAR(16) NOT NULL DEFAULT 'active',  -- REQ-REF-002: أرشفة لا حذف
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_ref_values_type_code UNIQUE (ref_type, code),
    CONSTRAINT chk_ref_values_type CHECK (ref_type IN (
        'country', 'city', 'language', 'fuel_type', 'transmission_type',
        'engine_type', 'part_condition', 'subscription_type'
    )),
    CONSTRAINT chk_ref_values_status CHECK (status IN ('active', 'archived'))
);
COMMENT ON TABLE ref.ref_values IS 'REQ-REF-001..002: جدول موحّد لجميع أنواع القيم المرجعية الثمانية';
CREATE INDEX idx_ref_values_type ON ref.ref_values (ref_type);

-- جداول تتبّع الاستيراد الجماعي (CR-003) — نطاق REF فقط
CREATE TABLE ref.bulk_import_jobs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ref_type          VARCHAR(32) NOT NULL,             -- REQ-REF-004، 007
    imported_by_ref_id UUID NOT NULL,                   -- إشارة مرجعية لمستخدم في iam
    file_name         VARCHAR(256) NOT NULL,
    source_file_ref   TEXT,                              -- REQ-REF (الملف الأصلي، مقترح إضافي معتمَد)
    status            VARCHAR(24) NOT NULL DEFAULT 'validating', -- REQ-REF-005، 006
    new_count         INTEGER NOT NULL DEFAULT 0,
    updated_count     INTEGER NOT NULL DEFAULT 0,
    rejected_count    INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_bulk_import_jobs_type CHECK (ref_type IN (
        'country', 'city', 'language', 'fuel_type', 'transmission_type',
        'engine_type', 'part_condition', 'subscription_type'
    )),
    CONSTRAINT chk_bulk_import_jobs_status CHECK (status IN (
        'validating', 'preview_ready', 'committed', 'failed'
    ))
);
COMMENT ON TABLE ref.bulk_import_jobs IS 'CR-003 — REQ-REF-004..008: رأس عملية الاستيراد الجماعي';
CREATE INDEX idx_bulk_import_jobs_type ON ref.bulk_import_jobs (ref_type);
CREATE INDEX idx_bulk_import_jobs_status ON ref.bulk_import_jobs (status);

CREATE TABLE ref.bulk_import_job_rows (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id            UUID NOT NULL REFERENCES ref.bulk_import_jobs(id),
    row_number        INTEGER NOT NULL,
    outcome           VARCHAR(16) NOT NULL,  -- جديد/محدَّث/مرفوض
    rejection_reason  TEXT,
    raw_row_data      JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_bulk_import_job_rows_outcome CHECK (outcome IN ('new', 'updated', 'rejected'))
);
COMMENT ON TABLE ref.bulk_import_job_rows IS 'CR-003 — REQ-REF-006: نتيجة كل صف في عملية الاستيراد';
CREATE INDEX idx_bulk_import_job_rows_job_id ON ref.bulk_import_job_rows (job_id);
