-- ============================================================
-- 021_trm_unified_ratings.sql — الترحيل الرسمي المعتمَد لنموذج TRM الموحَّد
-- الحالة: Prepared — معتمَد من المالك، لم يُطبَّق فعليًا بعد (لا اتصال DB هنا)
-- المرجع: CR-009 (اعتماد نموذج تقييم موحَّد)؛ يُلغي فعليًا تصميم trm.ratings
--         الأصلي في 012_trm.sql فيما يخص جدول التقييمات تحديدًا (لا reports
--         ولا disputes، تلك تبقى كما هي دون تغيير).
--
-- 012_trm.sql: Superseded (بخصوص جدول ratings فقط) — القرار موثَّق ومعتمَد.
-- ============================================================

-- -------------------------------------------------------------
-- حماية من فقدان البيانات: يفشل الترحيل صراحة إن وُجدت بيانات فعلية في
-- الجدول القديم لم تُرحَّل بعد، بدلاً من حذفها أو تجاهلها بصمت.
-- -------------------------------------------------------------
DO $$
DECLARE
    legacy_row_count INT;
BEGIN
    SELECT COUNT(*) INTO legacy_row_count FROM trm.ratings;
    IF legacy_row_count > 0 THEN
        RAISE EXCEPTION 'حماية بيانات: trm.ratings القديم يحتوي % صفًا فعليًا؛ '
            'يجب تشغيل سكربت ترحيل البيانات (data_migration/012_to_021_ratings.sql، '
            'غير مُضمَّن هنا لأنه يستوجب قرار تحويل صريحًا لكل صف) قبل تطبيق هذا '
            'الترحيل، لا تجاوز هذا الفحص بأي حال.', legacy_row_count;
    END IF;
END $$;

-- -------------------------------------------------------------
-- إعادة تسمية الجدول القديم (يُحفَظ تاريخيًا، لا يُحذَف فعليًا أبدًا،
-- اتساقًا مع No Hard Delete Policy حتى على مستوى تطوّر المخطط نفسه)
-- -------------------------------------------------------------
ALTER TABLE trm.ratings RENAME TO ratings_legacy_seller_only_v1;
COMMENT ON TABLE trm.ratings_legacy_seller_only_v1 IS
    'Superseded by CR-009 (نموذج التقييم الموحَّد، 021_trm_unified_ratings.sql). '
    'محفوظ تاريخيًا فقط؛ لا كتابة جديدة إليه إطلاقًا بعد هذا الترحيل.';

-- -------------------------------------------------------------
-- الجدول الموحَّد الجديد، باسم trm.ratings النهائي المعتمَد
-- -------------------------------------------------------------
CREATE TABLE trm.ratings (
    id                                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rated_by_user_ref_id              UUID NOT NULL,          -- إشارة مرجعية لـ iam.users
    target_type                      VARCHAR(24) NOT NULL,     -- seller | store | purchase_experience
    target_ref_id                    UUID NOT NULL,
    source_purchase_request_ref_id   UUID NOT NULL,             -- إشارة مرجعية لـ pur.purchase_requests
    score                             SMALLINT NOT NULL,
    comment                          TEXT,
    status                            VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_ratings_score CHECK (score BETWEEN 1 AND 5),
    CONSTRAINT chk_ratings_target_type CHECK (target_type IN ('seller', 'store', 'purchase_experience')),
    CONSTRAINT chk_ratings_status CHECK (status IN ('active', 'archived')),
    CONSTRAINT uq_ratings_rater_target_source
        UNIQUE (rated_by_user_ref_id, target_type, target_ref_id, source_purchase_request_ref_id)
);
COMMENT ON TABLE trm.ratings IS
    'النموذج الموحَّد المعتمَد رسميًا (CR-009)؛ يحل محل trm.ratings_legacy_seller_only_v1 بالكامل من هذا الترحيل فصاعدًا';
CREATE INDEX idx_ratings_target ON trm.ratings (target_type, target_ref_id);
CREATE INDEX idx_ratings_source ON trm.ratings (source_purchase_request_ref_id);

-- ملاحظة: trm.reports وtrm.disputes (من 012_trm.sql) غير متأثِّرَين إطلاقًا؛
-- التعارض والترحيل يخصان جدول ratings فقط.
