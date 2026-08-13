-- ============================================================
-- 029_vct_trim_model_years_and_market_availability.sql — Batch 1
-- المرجع: Approved VCT Design Baseline (المصدر التنفيذي المعتمَد لهذه
-- الدفعة) — الأقسام 1-9، 22.
-- الاعتماديات: 005_vct.sql (vct.generations، vct.trims، vct.localized_names)
-- ============================================================

-- ---------------------------------------------------------------
-- القسم 2: Generation — بيانات وصفية فقط، ليست مصدر الحقيقة النهائي
-- للتوافق. Nullable بالكامل؛ لا Backfill تخميني لسنوات تاريخية (القسم 22).
-- ---------------------------------------------------------------
ALTER TABLE vct.generations
    ADD COLUMN start_year INT NULL,
    ADD COLUMN end_year   INT NULL,
    ADD CONSTRAINT chk_generations_year_range
        CHECK (start_year IS NULL OR end_year IS NULL OR start_year <= end_year);
COMMENT ON COLUMN vct.generations.start_year IS 'وصفي فقط؛ لا يمثل مصدر الحقيقة للتوافق (Compatibility هو المصدر الفعلي).';
COMMENT ON COLUMN vct.generations.end_year IS 'وصفي فقط؛ لا يمثل مصدر الحقيقة للتوافق.';

-- ---------------------------------------------------------------
-- القسم 3: Trim Model Years — الكيان الجديد المعتمَد (لا ModelYear عام).
-- التسلسل: Manufacturer → Model → Generation → Trim → Trim Model Year.
-- FK داخلي حقيقي داخل نطاق VCT نفسه (بخلاف الإشارات العابرة للنطاق).
-- ---------------------------------------------------------------
CREATE TABLE vct.trim_model_years (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trim_ref_id  UUID NOT NULL REFERENCES vct.trims(id),
    year         INT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_trim_model_years_trim_year UNIQUE (trim_ref_id, year)
);
COMMENT ON TABLE vct.trim_model_years IS
    'Approved VCT Design Baseline §3-4: سنة موديل محدَّدة تابعة لفئة (Trim)؛ '
    'يجب أن تقع ضمن [generation.start_year, generation.end_year] الوصفيين '
    'عند تحديدهما (يُتحقَّق منه في vct_service.py قبل الإدراج، لا CHECK هنا '
    'لأن التحقق يستوجب قراءة صف الجيل الأب عبر trim، وهو ما لا يدعمه CHECK '
    'بسيط داخل نفس الجدول).';
CREATE INDEX idx_trim_model_years_trim_id ON vct.trim_model_years (trim_ref_id);

-- ---------------------------------------------------------------
-- القسم 5: توسيع Localized Names ليشمل generation/trim (لا نظام تسمية
-- منفصل). استبدال CHECK القائم صراحةً بدل تركه جنبًا لآخر جديد.
-- ---------------------------------------------------------------
ALTER TABLE vct.localized_names
    DROP CONSTRAINT chk_localized_names_owner_type;
ALTER TABLE vct.localized_names
    ADD CONSTRAINT chk_localized_names_owner_type
        CHECK (owner_type IN ('manufacturer', 'model', 'generation', 'trim'));

-- ---------------------------------------------------------------
-- القسم 6-9: Market Availability. Exactly-one-target عبر CHECK + Partial
-- Unique Indexes (CHECK وحده لا يكفي — القسم 9). country_ref_id يبقى
-- إشارة عابرة للنطاق بلا FK فعلي (نفس نمط كل *_ref_id العابرة في المشروع).
-- ---------------------------------------------------------------
CREATE TABLE vct.trim_market_availability (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trim_ref_id             UUID NULL REFERENCES vct.trims(id),
    trim_model_year_ref_id  UUID NULL REFERENCES vct.trim_model_years(id),
    country_ref_id          UUID NOT NULL,  -- إشارة مرجعية لـ ref.ref_values (ref_type='country')؛ بلا FK فعلي
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_market_availability_exactly_one_target CHECK (
        (trim_ref_id IS NOT NULL AND trim_model_year_ref_id IS NULL)
        OR
        (trim_ref_id IS NULL AND trim_model_year_ref_id IS NOT NULL)
    )
);
COMMENT ON TABLE vct.trim_market_availability IS
    'Approved VCT Design Baseline §6-9: غياب أي صف لهدف معيَّن = Global '
    'Availability (بلا قيد سوق). وجود صف واحد أو أكثر = Whitelist صارمة. '
    'Trim-level وYear-specific لنفس Trim ممنوعان من التعايش (يُفرَض '
    'Transactionally عبر Advisory Lock في vct_service.py، §17 — القيود هنا '
    'تمنع فقط تكرار الصف الواحد، لا التعايش بين المستويين).';
-- القسم 9: منع تكرار (trim, country) و(trim_model_year, country) كل على حدة
CREATE UNIQUE INDEX uq_market_availability_trim_country
    ON vct.trim_market_availability (trim_ref_id, country_ref_id)
    WHERE trim_ref_id IS NOT NULL;
CREATE UNIQUE INDEX uq_market_availability_trim_year_country
    ON vct.trim_market_availability (trim_model_year_ref_id, country_ref_id)
    WHERE trim_model_year_ref_id IS NOT NULL;
CREATE INDEX idx_market_availability_country ON vct.trim_market_availability (country_ref_id);
