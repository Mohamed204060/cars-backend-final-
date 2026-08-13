-- ============================================================
-- 027_com_conversation_participants.sql — عضوية صريحة في المحادثات (COM)
-- المرجع: CR-015 (Frontend Enablement APIs) — معالجة الفجوة الموثَّقة صراحةً
--         في تعليق message_api.py الأصلي (لا تتبُّع لأطراف المحادثة)
-- الاعتماديات: com (011_com.sql)، pur (010_pur.sql، بالإشارة فقط في الـBackfill)،
--              str (009_str.sql، بالإشارة فقط في الـBackfill)
-- قرار تصميم (موثَّق كما طُلب): تم فحص بديلين قبل الاختيار:
--   (1) عمودان مباشران على com.conversations (buyer_ref_id/seller_ref_id):
--       مرفوض — يفرض افتراض ثنائية الأطراف بصورة دائمة على مستوى البنية،
--       بينما الواقع الحالي فعليًا يسمح بأكثر من مُرسِل واحد لكل جهة على نفس
--       المحادثة (لا تقسيم للمحادثة حسب البائع على purchase_request)؛ تثبيت
--       عمودين يُخفي هذا الواقع بدل توثيقه، ويحتاج Migration إضافية لاحقًا
--       عند أي احتياج لأكثر من طرفين. كما يخالف نمط الجداول المرجعية
--       المنفصلة المتَّبع في كل الوحدات الأخرى (pur.offers، com.messages
--       نفسها) بدل حقول مباشرة.
--   (2) جدول علاقة مستقل (المُختار): com.conversation_participants —
--       يمثّل العضوية كسجل صريح قابل للاستعلام والتحقق مباشرة في طبقة
--       الـAuthorization (نفس ما تحتاجه send/list/delete)، يدعم أي عدد من
--       الأطراف بلا تغيير بنيوي إضافي مستقبلًا، ولا يكرر أي بيانات من
--       IAM/PUR/STR (إشارة مرجعية UUID فقط، اتساقًا صارمًا مع مبدأ SSOT
--       المتَّبع في كل migration سابقة).
-- تم رفض إعادة استخدام com.conversation_user_settings (020_com_extended.sql)
-- كجدول عضوية: غرضها التفضيلات الشخصية (كتم/أرشفة) لا إثبات العضوية؛ خلط
-- الدلالتين يجعل أي صف تفضيلات مستقبلي = ادّعاء عضوية ضمنيًا بلا قصد.
-- ============================================================

CREATE TABLE com.conversation_participants (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID NOT NULL REFERENCES com.conversations(id),
    user_ref_id      UUID NOT NULL,  -- SSOT: إشارة مرجعية فقط لـ iam.users، لا FK فعلي عبر حدود الوحدات
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_conversation_participants UNIQUE (conversation_id, user_ref_id)
);
COMMENT ON TABLE com.conversation_participants IS
    'CR-015: عضوية صريحة قابلة للتحقق من قاعدة البيانات؛ تُستخدَم في Authorization لـ list/send/delete الرسائل والمرفقات، لا فقط GET /conversations. عضو = أي مستخدم أرسل رسالة فعليًا في المحادثة، بالإضافة إلى الطرف القانوني الأحادي القابل للاشتقاق يقينًا من السياق عند إنشاء المحادثة (راجع منطق التطبيق: message_api.py resolve_canonical_participant).';
CREATE INDEX idx_conversation_participants_conversation_id ON com.conversation_participants (conversation_id);
CREATE INDEX idx_conversation_participants_user_ref_id ON com.conversation_participants (user_ref_id);

-- ------------------------------------------------------------
-- Backfill آمن للمحادثات الموجودة فعليًا — كل صف بمصدر يقيني فقط
-- (بلا أي طرف مفترَض صامتًا؛ إن تعذَّر التحديد اليقيني، لا يُدرَج شيء لتلك
-- الجهة، ولا يفشل الـMigration بسببها — يبقى قابلًا للمراجعة يدويًا لاحقًا،
-- راجع استعلام التحقق post-backfill أسفل الملف).
-- ------------------------------------------------------------

-- (أ) كل مُرسِلي الرسائل الفعليين في كل محادثة — مصدر يقيني 100% (حدث فعلي
--     وقع بالفعل، لا اشتقاق أو تخمين إطلاقًا).
INSERT INTO com.conversation_participants (conversation_id, user_ref_id)
SELECT DISTINCT m.conversation_id, m.sender_user_ref_id
FROM com.messages m
ON CONFLICT (conversation_id, user_ref_id) DO NOTHING;

-- (ب) الطرف القانوني الأحادي القابل للاشتقاق يقينًا من سياق purchase_request:
--     المشتري buyer_user_ref_id — كل طلب شراء له مشترٍ واحد إلزاميًا (NOT NULL
--     في 010_pur.sql)، فلا لبس هنا مطلقًا. لا إدراج لأي "بائع افتراضي" لأن
--     محادثة سياق purchase_request قد يشارك فيها أكثر من بائع واحد فعليًا
--     (لا تقسيم للمحادثة حسب مقدّم العرض في التصميم الحالي) — أي بائع
--     شارك فعليًا مشمول بالفعل عبر الخطوة (أ) أعلاه من سجل رسائله الحقيقي.
INSERT INTO com.conversation_participants (conversation_id, user_ref_id)
SELECT DISTINCT c.id, pr.buyer_user_ref_id
FROM com.conversations c
JOIN pur.purchase_requests pr ON pr.id = c.context_ref_id
WHERE c.context_type = 'purchase_request'
ON CONFLICT (conversation_id, user_ref_id) DO NOTHING;

-- (ج) الطرف القانوني الأحادي القابل للاشتقاق يقينًا من سياق inventory_item:
--     مالك المتجر صاحب العنصر — كل عنصر مخزون ينتمي لمتجر واحد إلزاميًا
--     (NOT NULL)، وكل متجر له مالك واحد إلزاميًا (NOT NULL) — لا لبس.
INSERT INTO com.conversation_participants (conversation_id, user_ref_id)
SELECT DISTINCT c.id, s.owner_user_ref_id
FROM com.conversations c
JOIN str.inventory_items ii ON ii.id = c.context_ref_id
JOIN str.stores s ON s.id = ii.store_id
WHERE c.context_type = 'inventory_item'
ON CONFLICT (conversation_id, user_ref_id) DO NOTHING;

-- ------------------------------------------------------------
-- استعلام تحقق بعد التنفيذ (توثيقي — لا يُنفَّذ تلقائيًا كجزء من الـMigration؛
-- يُشغَّل يدويًا لمراجعة أي محادثة بلا أي مشارك بعد الـBackfill: إما مرجع
-- سياق تالف/يتيم (context_ref_id لا يشير لسجل موجود)، أو محادثة أُنشئت
-- دون أي رسالة فيها إطلاقًا بعد. لا يُفترَض طرف لهذه الحالات؛ تُترَك بلا
-- مشاركين حتى تُراجَع يدويًا، وهذا سلوك آمن (لا أحد كان يراها أصلًا قبل
-- هذا الـMigration بحكم إغلاق الوصول القديم غير المقيَّد).
--
-- SELECT c.id, c.context_type, c.context_ref_id
-- FROM com.conversations c
-- LEFT JOIN com.conversation_participants cp ON cp.conversation_id = c.id
-- WHERE cp.id IS NULL;
-- ------------------------------------------------------------
