-- ============================================================
-- 035_login_security_history.sql — Admin Operational Completion:
-- Login/Security History (Gap Sweep v2.2 — بنود 1، 2، 5)
--
-- هذه Migration تنفّذ حصريًا ما حُسم صراحةً في المراجعات الأمنية المتتالية:
-- 1) دور تشغيل مخصَّص أقل امتيازًا (carsmaint_app) يثبت فعليًا (لا تعليقًا)
--    أن aud.events Append-Only حتى ضد الدور الذي يُفترَض أن التطبيق يعمل به.
-- 2) فهرس استعلام على IP لأحداث الدخول (بلا تخزين مضاعَف، JSONB الموجود
--    أصلًا هو SSOT الوحيد).
--
-- لا جداول جديدة. iam.sessions غير مُعدَّل إطلاقًا (aud.events هو الـSSOT
-- لتاريخ الدخول الأمني، وفق القرار المعتمَد في Gap Sweep v2.1/v2.2).
-- ============================================================

-- ------------------------------------------------------------
-- 1) دور تشغيل مخصَّص أقل امتيازًا
-- ------------------------------------------------------------
-- اكتُشِف أن بيئة CI/الاختبار الحالية تتصل كـpostgres (Superuser) — أي دور
-- Superuser يتجاوز كل صلاحيات GRANT/REVOKE بلا استثناء، فـ
-- REVOKE ... FROM PUBLIC وحدها لا تُثبِت شيئًا عمليًا ضد هذا الاتصال. الحل
-- الوحيد المثبِت للثبات الفعلي: دور منفصل غير Superuser، يُختبَر Privilege
-- الإدراج/المنع صراحةً عبر اتصال حقيقي به (لا افتراضًا من وجود REVOKE فقط).
--
-- كلمة المرور: لا تُضبَط هنا إطلاقًا (ممنوع تسجيل كلمات مرور في Migrations/
-- Source Control صراحةً). CREATE ROLE ... LOGIN بلا PASSWORD — تُضبَط لاحقًا
-- عبر ALTER ROLE carsmaint_app WITH PASSWORD '...' خارج قاعدة الكود، من إدارة
-- أسرار بيئة النشر (Deployment Secret Management)، أو عبر psql variable في
-- بيئة الاختبار فقط (سكربت الإعداد يمرِّرها كمتغيّر، لا نص ثابت في هذا الملف).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'carsmaint_app') THEN
        CREATE ROLE carsmaint_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

-- NOINHERIT أعلاه مقصودة: لا نريد هذا الدور يرث صلاحيات أي دور أوسع صامتًا
-- عبر عضوية مستقبلية بلا مراجعة صريحة لكل GRANT.

-- صلاحيات عامة على كل الـSchemas التطبيقية (بلا هذه، لن يعمل التطبيق إطلاقًا
-- بهذا الدور — الهدف تشغيل حقيقي، لا رمزي فقط على aud.events).
GRANT USAGE ON SCHEMA
    iam, str, pur, com, cnt, sup, sub, ref, trm, media, aud, ana, cmp, pct, vct, ntf, sys
TO carsmaint_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA
    iam, str, pur, com, cnt, sup, sub, ref, trm, media, ana, cmp, pct, vct, ntf, sys
TO carsmaint_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA
    iam, str, pur, com, cnt, sup, sub, ref, trm, media, aud, ana, cmp, pct, vct, ntf, sys
TO carsmaint_app;

-- ------------------------------------------------------------
-- الاستثناء الجوهري لكل هذه الـMigration: aud.events
-- ------------------------------------------------------------
-- SELECT + INSERT فقط. لا UPDATE، لا DELETE. هذا هو الغرض الأمني الوحيد من
-- إنشاء الدور بالكامل — إثبات المطلب الحقيقي:
-- "الدور التشغيلي قادر على الإضافة، غير قادر على التعديل أو المحو".
GRANT SELECT, INSERT ON aud.events TO carsmaint_app;
REVOKE UPDATE, DELETE ON aud.events FROM carsmaint_app;
-- تكرار صريح مقصود (Defense in Depth): REVOKE الأصلية من PUBLIC (004_aud.sql)
-- تبقى قائمة أيضًا؛ هذا السطر يضمن عدم وجود GRANT مباشر لاحق يتجاوزها لهذا
-- الدور تحديدًا حتى لو تغيّر ترتيب Migrations مستقبلًا.
REVOKE UPDATE, DELETE ON aud.events FROM PUBLIC;

-- ملاحظة حوكمة مهمة (ليست SQL قابلة للتنفيذ، توثيق فقط):
-- إثبات الصلاحية (Capability) عبر هذه الـMigration لا يعني تلقائيًا أن
-- التطبيق الفعلي في بيئة الإنتاج يتصل فعليًا بهذا الدور بدل postgres —
-- ذلك تغيير في إعدادات الاتصال/الأسرار خارج نطاق هذا المستودع، ومسؤولية
-- منفصلة يجب تأكيدها عمليًا قبل الادعاء بأن Append-Only مُطبَّق فعليًا في
-- الإنتاج (Gap Sweep v2.2، بند 5-B).

COMMENT ON ROLE carsmaint_app IS
    'الدور التشغيلي المخصَّص الأقل امتيازًا للتطبيق. SELECT/INSERT فقط على '
    'aud.events (لا UPDATE/DELETE) — يثبت Append-Only فعليًا، لا بالتعليق '
    'فقط. كلمة المرور تُضبَط خارج الكود عبر إدارة أسرار بيئة النشر.';

-- ------------------------------------------------------------
-- 2) فهرس استعلام لأحداث الدخول حسب IP
-- ------------------------------------------------------------
-- metadata JSONB هو المكان الموثَّق أصلًا لـIP/User-Agent لأحداث الدخول
-- (تعليق 004_aud.sql الأصلي). هذا فهرس تعبيري ضيّق النطاق (WHERE على
-- event_name فقط)، لا عمود IP مستقل جديد — يتجنّب توسيع الجدول العام بعمود
-- شبه فارغ لبقية أنواع الأحداث غير المرتبطة بالدخول.
CREATE INDEX idx_events_login_ip ON aud.events ((metadata ->> 'ip_address'))
    WHERE event_name IN ('login_success', 'login_failed');

COMMENT ON INDEX aud.idx_events_login_ip IS
    'REQ-AUD + Login/Security History: استعلام إداري فعّال بحسب IP لأحداث '
    'الدخول الناجحة/الفاشلة فقط، بلا فهرسة كل metadata لبقية أنواع الأحداث.';
