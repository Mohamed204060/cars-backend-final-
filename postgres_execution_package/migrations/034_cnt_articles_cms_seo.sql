-- ============================================================
-- 034_cnt_articles_cms_seo.sql — توسيع CNT إلى CMS خفيف + SEO Readiness
-- المرجع: Master Handoff §8 (Content Management/Articles)، §10-13 (SEO)
-- الاعتماديات: 013_cnt.sql (cnt.articles)، 002_ref.sql (ref.ref_values)،
--              032_media_foundation.sql (media.attachments)
--
-- أقل تغيير ممكن على وحدة غير مغلقة صراحة (CNT ليست ضمن Order/Store/
-- Search/AUD/ANA المحمية) + Impact Sweep: يعيد تسمية عمودين موجودين
-- (title→title_ar, body→body_ar) لتفادي تكرار بيانات، ويبقي البيانات
-- القديمة سليمة عبر الترحيل أدناه. لا حذف بيانات.
-- ============================================================

-- 1) إعادة تسمية الأعمدة العربية الأساسية (المحتوى القديم عربي افتراضًا)
ALTER TABLE cnt.articles RENAME COLUMN title TO title_ar;
ALTER TABLE cnt.articles RENAME COLUMN body TO body_ar;

-- 2) الحقول الإنجليزية الاختيارية (Parity بلا إلزام على المحتوى القديم)
ALTER TABLE cnt.articles ADD COLUMN title_en VARCHAR(256) NULL;
ALTER TABLE cnt.articles ADD COLUMN body_en TEXT NULL;

-- 3) Summary/Excerpt ثنائي اللغة
ALTER TABLE cnt.articles ADD COLUMN summary_ar VARCHAR(500) NULL;
ALTER TABLE cnt.articles ADD COLUMN summary_en VARCHAR(500) NULL;

-- 4) Slug فريد ومستقر (SEO-friendly URL) — يُملأ يدويًا أو يُشتق من العنوان
ALTER TABLE cnt.articles ADD COLUMN slug VARCHAR(180) NULL;

-- ترحيل: توليد slug أولي من title_ar لكل صف موجود (حروف/أرقام/شرطات فقط)،
-- مع لاحقة قصيرة من الـid لضمان التفرد بلا تصادم.
UPDATE cnt.articles
SET slug = regexp_replace(
              regexp_replace(lower(trim(title_ar)), '[^a-z0-9\u0621-\u064A0-9]+', '-', 'g'),
              '(^-+|-+$)', '', 'g'
           ) || '-' || substr(id::text, 1, 8)
WHERE slug IS NULL;

ALTER TABLE cnt.articles ALTER COLUMN slug SET NOT NULL;
ALTER TABLE cnt.articles ADD CONSTRAINT uq_articles_slug UNIQUE (slug);

-- 5) التصنيف — إعادة استخدام جدول ref.ref_values الموحّد (بلا FK فعلي،
-- نفس نمط SSOT العابر للنطاق المتبع في كل *_ref_id بالمشروع)
ALTER TABLE cnt.articles ADD COLUMN category_ref_id UUID NULL;

ALTER TABLE ref.ref_values DROP CONSTRAINT chk_ref_values_type;
ALTER TABLE ref.ref_values ADD CONSTRAINT chk_ref_values_type CHECK (ref_type IN (
    'country', 'city', 'language', 'fuel_type', 'transmission_type',
    'engine_type', 'part_condition', 'subscription_type', 'article_category'
));

-- 6) الصورة البارزة — إعادة استخدام media.attachments الموجودة (نفس نمط
-- purchase_request/offer/inventory_item)، لا عمود صورة منفصل على cnt.articles
ALTER TABLE media.attachments DROP CONSTRAINT chk_media_attachments_owner_type;
ALTER TABLE media.attachments ADD CONSTRAINT chk_media_attachments_owner_type
    CHECK (owner_type IN ('purchase_request', 'offer', 'inventory_item', 'article'));

-- 7) SEO metadata (طول متوافق مع حدود محركات البحث الشائعة)
ALTER TABLE cnt.articles ADD COLUMN seo_title_ar VARCHAR(70) NULL;
ALTER TABLE cnt.articles ADD COLUMN seo_title_en VARCHAR(70) NULL;
ALTER TABLE cnt.articles ADD COLUMN seo_description_ar VARCHAR(160) NULL;
ALTER TABLE cnt.articles ADD COLUMN seo_description_en VARCHAR(160) NULL;

-- 8) تاريخ النشر الفعلي (يختلف عن created_at/updated_at) + محرر آخر تعديل
ALTER TABLE cnt.articles ADD COLUMN published_at TIMESTAMPTZ NULL;

-- 9) توسيع Status State Machine: draft → published → archived
--    (unpublished القديمة → draft، لا فقدان بيانات)
UPDATE cnt.articles SET status = 'draft' WHERE status = 'unpublished';

ALTER TABLE cnt.articles DROP CONSTRAINT chk_articles_status;
ALTER TABLE cnt.articles ADD CONSTRAINT chk_articles_status
    CHECK (status IN ('draft', 'published', 'archived'));
ALTER TABLE cnt.articles ALTER COLUMN status SET DEFAULT 'draft';

-- 10) فهارس لدعم القراءة العامة (list/detail) والـSitemap
CREATE INDEX idx_articles_slug ON cnt.articles (slug);
CREATE INDEX idx_articles_category ON cnt.articles (category_ref_id);
CREATE INDEX idx_articles_published_at ON cnt.articles (published_at) WHERE status = 'published';

COMMENT ON COLUMN cnt.articles.slug IS 'REQ-CNT (Master Handoff §8): فريد، مستقر، SEO-friendly. لا يُعاد توليده تلقائيًا بعد أول حفظ.';
COMMENT ON COLUMN cnt.articles.category_ref_id IS 'يشير إلى ref.ref_values(ref_type=article_category). بلا FK فعلي (نمط SSOT العابر للنطاق).';
COMMENT ON TABLE cnt.articles IS 'REQ-CNT-001, 002 + Master Handoff §8: مقالة CMS خفيفة، ثنائية اللغة، Draft→Published→Archived، مع SEO metadata.';
