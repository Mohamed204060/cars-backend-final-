-- ============================================================
-- 000_extensions_and_schemas.sql
-- الغرض: تفعيل الامتدادات اللازمة وإنشاء مخططات (Schemas) كل وحدة
-- المرجع: DD الحزمة 1 (بنية الوحدات)، SAD الحزمة 4 (قاعدة بيانات مقسَّمة منطقيًا)
-- ملاحظة تقنية (قرار تنفيذي أول): PostgreSQL كمحرك قاعدة البيانات؛
--   UUID كمفتاح أساسي عبر gen_random_uuid() (يتطلب امتداد pgcrypto)؛
--   TIMESTAMPTZ لتخزين جميع الطوابع الزمنية بصيغة UTC (REQ-AUD-012).
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS iam;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS sys;
CREATE SCHEMA IF NOT EXISTS aud;
CREATE SCHEMA IF NOT EXISTS vct;
CREATE SCHEMA IF NOT EXISTS pct;
CREATE SCHEMA IF NOT EXISTS cmp;
CREATE SCHEMA IF NOT EXISTS sub;
CREATE SCHEMA IF NOT EXISTS str;
CREATE SCHEMA IF NOT EXISTS pur;
CREATE SCHEMA IF NOT EXISTS com;
CREATE SCHEMA IF NOT EXISTS trm;
CREATE SCHEMA IF NOT EXISTS cnt;
CREATE SCHEMA IF NOT EXISTS sup;
