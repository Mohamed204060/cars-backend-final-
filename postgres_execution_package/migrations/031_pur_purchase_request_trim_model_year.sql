-- ============================================================
-- 031_pur_purchase_request_trim_model_year.sql — Batch 1
-- المرجع: Approved VCT Design Baseline §23 (Purchase Requests داخل Batch 1)
-- الاعتماديات: 010_pur.sql، 029_vct_trim_model_years_and_market_availability.sql
--
-- ملاحظة: trim_ref_id في pur.purchase_requests ليس له FK فعلي أصلًا (SSOT
-- بلا قيد DB، بنفس نمط catalog_part_ref_id/buyer_user_ref_id في هذا الجدول
-- منذ 010_pur.sql)؛ trim_model_year_ref_id الجديد يتبع نفس المبدأ حرفيًا —
-- التحقق الحقيقي (وجود الفئة، وانتماء السنة لنفس الفئة) في order_service.py
-- عبر دوال محقونة من VCT، لا قيد بنيوي هنا. لا Backfill: السجلات القديمة
-- تبقى trim_model_year_ref_id=NULL (يعني "أي سنة ضمن هذه الفئة"، بلا تحديد).
-- ============================================================

ALTER TABLE pur.purchase_requests
    ADD COLUMN trim_model_year_ref_id UUID NULL;

COMMENT ON COLUMN pur.purchase_requests.trim_model_year_ref_id IS
    'Batch 1 (Approved VCT Design Baseline §23): سنة موديل دقيقة اختيارية '
    'ضمن trim_ref_id (يجب أن تنتمي لنفس trim_ref_id — يُتحقَّق في '
    'order_service.py). NULL = لم يحدِّد المشتري سنة دقيقة (أي سنة ضمن '
    'الفئة). لا FK فعلي، بنفس نمط trim_ref_id في هذا الجدول.';
