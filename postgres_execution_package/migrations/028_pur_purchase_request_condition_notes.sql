-- ============================================================
-- 028_pur_purchase_request_condition_notes.sql — CR-022
-- المرجع: CR-022 (النطاق المعتمَد حرفيًا: Condition + Buyer Notes فقط)
-- الاعتماديات: 010_pur.sql (pur.purchase_requests)، 002_ref.sql (ref.ref_values)
--
-- ملاحظة: لا FOREIGN KEY فعلي على condition_ref_id تجاه ref.ref_values،
-- بنفس القرار القائم فعليًا لكل حقول *_ref_id الأخرى في هذا المشروع
-- (fuel_type_ref_id، transmission_type_ref_id، condition_ref_id في
-- str.inventory_items، country_ref_id/city_ref_id) — إشارة مرجعية SSOT
-- بلا قيد قاعدة بيانات، والتحقق من صحة النوع (part_condition تحديدًا)
-- يقع بالكامل على طبقة الخدمة (order_service.py) عبر دالة محقونة، تمامًا
-- كنمط is_part_approved_checker القائم. لا Backfill: السجلات القديمة تبقى
-- NULL/NULL.
-- ============================================================

ALTER TABLE pur.purchase_requests
    ADD COLUMN condition_ref_id UUID NULL,  -- إشارة مرجعية لـ ref.ref_values (ref_type='part_condition')؛ NULL = بلا تفضيل
    ADD COLUMN notes TEXT NULL;              -- ملاحظات المشتري الاختيارية؛ حد 2000 حرف على مستوى API فقط (لا CHECK هنا، اتساقًا مع عدم وجود قيود طول نصية مماثلة على أي عمود TEXT آخر في المشروع)

COMMENT ON COLUMN pur.purchase_requests.condition_ref_id IS 'CR-022: إشارة مرجعية اختيارية لـ ref.ref_values(part_condition)؛ NULL = بلا تفضيل. تحقق النوع في order_service.py، لا قيد DB.';
COMMENT ON COLUMN pur.purchase_requests.notes IS 'CR-022: ملاحظات المشتري الاختيارية، نص عادي، حد 2000 حرف مُطبَّق في order_api.py.';
