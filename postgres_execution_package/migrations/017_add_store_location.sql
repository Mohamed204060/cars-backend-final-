-- ============================================================
-- 017_add_store_location.sql
-- إكمال فجوة اكتُشفت أثناء تصميم طبقة Repository لخدمة البحث:
--   REQ-SRC-006 (معتمد قبل CR-004 وCR-006) يتطلب تصفية نتائج البحث حسب
--   دولة/مدينة البائع؛ لم يُضِف التصميم الفيزيائي الأصلي (الحزمة التنفيذية
--   الأولى) عمودَي الدولة/المدينة لجدول str.stores رغم أن البيانات يجب أن
--   تُخزَّن في مكان ما. هذا استكمال لتنفيذ متطلب معتمَد أصلاً، لا نطاقًا
--   جديدًا؛ يُسجَّل صراحة هنا بدلاً من دمجه ضمنيًا في حزمة أخرى.
-- المرجع: REQ-SRC-006، REQ-SRC-006-C..E (CR-004)
-- الاعتماديات: str.stores (009_str.sql)، ref.ref_values (002_ref.sql)
-- ============================================================

ALTER TABLE str.stores
    ADD COLUMN country_ref_id UUID,  -- إشارة مرجعية لـ ref.ref_values (ref_type='country')
    ADD COLUMN city_ref_id    UUID;  -- إشارة مرجعية لـ ref.ref_values (ref_type='city')

COMMENT ON COLUMN str.stores.country_ref_id IS 'REQ-SRC-006: دولة البائع، أساس تصفية البحث الجغرافية';
COMMENT ON COLUMN str.stores.city_ref_id IS 'REQ-SRC-006: مدينة البائع';

CREATE INDEX idx_stores_country ON str.stores (country_ref_id);
CREATE INDEX idx_stores_city ON str.stores (city_ref_id);

-- ملاحظة: العمودان قابلان لقيمة NULL مبدئيًا تجنبًا لكسر أي بيانات متجر قائمة
-- بلا موقع مسجَّل بعد؛ إلزامية تعبئتهما عند إنشاء متجر جديد قرار على مستوى
-- التحقق التطبيقي (Application-level validation) لا قيد قاعدة بيانات صارم،
-- لتفادي مشاكل ترحيل غير متوقَّعة على بيانات قائمة محتملة مستقبلاً.
