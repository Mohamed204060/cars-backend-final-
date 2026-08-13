-- ============================================================
-- 026_sub_free_plan.sql — خطة Free نظامية دائمة (CR-014)
-- المرجع: قرار الحوكمة CR-014 — عضوية Free متاحة دومًا لكل بائع
-- الاعتماديات: 008_sub.sql، 002_ref.sql
-- ============================================================

-- expires_at يجب أن يقبل NULL: خطة Free لا تنتهي أبدًا (لا مدة لها)
ALTER TABLE sub.seller_subscriptions ALTER COLUMN expires_at DROP NOT NULL;

-- علامة تمييز خطة Free النظامية عن بقية الخطط المدفوعة
ALTER TABLE sub.plans ADD COLUMN is_free BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN sub.plans.is_free IS 'CR-014: خطة Free نظامية واحدة فقط، لا تُنشأ عبر REQ-SUB-001 اليدوي';

-- خطة واحدة فقط يجوز أن تكون Free
CREATE UNIQUE INDEX uq_sub_plans_single_free ON sub.plans (is_free) WHERE is_free = true;

-- بذر نوع مرجعي 'free' ضمن subscription_type إن لم يكن موجودًا
INSERT INTO ref.ref_values (ref_type, code, status)
SELECT 'subscription_type', 'free', 'active'
WHERE NOT EXISTS (
    SELECT 1 FROM ref.ref_values WHERE ref_type = 'subscription_type' AND code = 'free'
);

-- بذر خطة Free النظامية الوحيدة، مرتبطة بالقيمة المرجعية أعلاه
INSERT INTO sub.plans (plan_type_ref_id, is_free)
SELECT rv.id, true
FROM ref.ref_values rv
WHERE rv.ref_type = 'subscription_type' AND rv.code = 'free'
  AND NOT EXISTS (SELECT 1 FROM sub.plans WHERE is_free = true);
