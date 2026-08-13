-- ============================================================
-- 030_cmp_year_specific_compatibility.sql — Batch 1
-- المرجع: Approved VCT Design Baseline — الأقسام 10-14، 22.
-- الاعتماديات: 007_cmp.sql، 029_vct_trim_model_years_and_market_availability.sql
--
-- توافقية خلفية (§22): السجلات الحالية (trim_ref_id NOT NULL دائمًا في
-- المخطط القديم) تصبح تلقائيًا General Compatibility بعد هذه الترحيلة —
-- بلا Backfill تخميني، trim_model_year_ref_id يبدأ NULL لكل السجلات
-- القديمة والجديدة على حد سواء ما لم يُحدَّد صراحة.
--
-- بلا FK عابر للنطاق من cmp إلى vct لـtrim_model_year_ref_id (بنفس مبدأ
-- trim_ref_id الحالي أصلًا: "بالإشارة المرجعية لا بمفتاح أجنبي فعلي،
-- اتساقًا مع مبدأ عدم عبور الوحدات" — تعليق 007_cmp.sql الأصلي).
-- ============================================================

-- §14: إزالة القيد القديم صراحةً — لم يعد يمثل النموذج الجديد (لا يفرّق
-- بين General وYear-specific)، ويتعارض مع الفهارس الجزئية الجديدة إن تُرك.
ALTER TABLE cmp.compatibility_records
    DROP CONSTRAINT uq_compatibility_part_trim;

-- trim_ref_id يصبح اختياريًا (Year-specific records لا تحدِّده)
ALTER TABLE cmp.compatibility_records
    ALTER COLUMN trim_ref_id DROP NOT NULL;

ALTER TABLE cmp.compatibility_records
    ADD COLUMN trim_model_year_ref_id UUID NULL;  -- إشارة مرجعية لـ vct.trim_model_years؛ بلا FK عابر للنطاق (بنفس نمط trim_ref_id)

COMMENT ON COLUMN cmp.compatibility_records.trim_ref_id IS
    'NULL يعني سجل Year-specific (انظر trim_model_year_ref_id). غير NULL يعني General Compatibility لكل سنوات الفئة.';
COMMENT ON COLUMN cmp.compatibility_records.trim_model_year_ref_id IS
    'Approved VCT Design Baseline §10-13: إشارة مرجعية اختيارية لـ vct.trim_model_years؛ '
    'NOT NULL هنا يعني سجل Year-specific (trim_ref_id يكون NULL حينها). '
    'لا تعايش General/Year-specific لنفس (قطعة، فئة) — يُفرَض Transactionally '
    'عبر Advisory Lock في cmp_service.py (§13، §16)، لا عبر قيد بنيوي وحده.';

-- §10: Exactly-one Compatibility Target — إما trim_ref_id أو trim_model_year_ref_id، حرفيًا
ALTER TABLE cmp.compatibility_records
    ADD CONSTRAINT chk_compatibility_exactly_one_target CHECK (
        (trim_ref_id IS NOT NULL AND trim_model_year_ref_id IS NULL)
        OR
        (trim_ref_id IS NULL AND trim_model_year_ref_id IS NOT NULL)
    );

-- §14: بدائل جزئية (Partial) محل القيد القديم — كل نوع على حدة
CREATE UNIQUE INDEX uq_compatibility_general
    ON cmp.compatibility_records (catalog_part_ref_id, trim_ref_id)
    WHERE trim_ref_id IS NOT NULL;
CREATE UNIQUE INDEX uq_compatibility_year_specific
    ON cmp.compatibility_records (catalog_part_ref_id, trim_model_year_ref_id)
    WHERE trim_model_year_ref_id IS NOT NULL;

CREATE INDEX idx_compatibility_trim_model_year ON cmp.compatibility_records (trim_model_year_ref_id);
