#!/usr/bin/env bash
# setup_test_database.sh
# الحالة: Prepared — لم يُشغَّل فعليًا في هذه الجلسة (لا اتصال شبكة/PostgreSQL هنا)
#
# الغرض: إنشاء قاعدة بيانات اختبار فارغة تمامًا، ثم تطبيق كل ملفات
# الترحيل (000 حتى 033) بالترتيب الرقمي من الصفر. الحزمة مكتفية ذاتيًا
# بالكامل: لا اعتماد على أي ملف خارج مجلد migrations/ المرفَق هنا.
#
# الاستخدام (في بيئة خارجية فعلية تدعم PostgreSQL):
#   export PGHOST=localhost PGPORT=5432 PGUSER=postgres PGPASSWORD=postgres
#   ./setup_test_database.sh

set -euo pipefail

DB_NAME="${DB_NAME:-carparts_test}"
MIGRATIONS_DIR="$(cd "$(dirname "$0")/../migrations" && pwd)"

REQUIRED_FILES=(
  "000_extensions_and_schemas.sql" "001_iam.sql" "002_ref.sql" "003_sys.sql" "004_aud.sql"
  "005_vct.sql" "006_pct.sql" "007_cmp.sql" "008_sub.sql" "009_str.sql" "010_pur.sql"
  "011_com.sql" "012_trm.sql" "013_cnt.sql" "014_sup.sql"
  "015_cr005_phase1_identity_providers.sql" "016_cr005_phase2_drop_password_column.sql"
  "017_add_store_location.sql" "018_ntf.sql" "019_sys_scheduler.sql" "020_com_extended.sql"
  "021_trm_unified_ratings.sql" "022_postgresql_validation_runtime_fixes.sql"
  "023_iam_sessions.sql" "025_sys_idempotency_keys.sql" "026_sub_free_plan.sql"
  "027_com_conversation_participants.sql" "028_pur_purchase_request_condition_notes.sql"
  "029_vct_trim_model_years_and_market_availability.sql" "030_cmp_year_specific_compatibility.sql"
  "031_pur_purchase_request_trim_model_year.sql"
  "032_media_foundation.sql"
  "033_ana_events.sql"
)

echo "=== التحقق من اكتمال جميع ملفات الترحيل المطلوبة (000-033) قبل أي تنفيذ ==="
missing=0
for f in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$MIGRATIONS_DIR/$f" ]; then
    echo "!! ملف مفقود: $f"
    missing=1
  fi
done
if [ "$missing" -eq 1 ]; then
  echo "=== فشل التحقق: ملفات ترحيل مطلوبة مفقودة؛ توقُّف قبل أي تنفيذ فعلي على قاعدة البيانات ==="
  exit 1
fi
echo "=== نجاح: جميع ملفات الترحيل الـ33 (000-033، بلا 024) موجودة فعليًا في الحزمة — Batch 2 Unit 1 ==="

echo "=== إسقاط قاعدة الاختبار إن وُجدت (لضمان بداية نظيفة فعلية) ==="
dropdb --if-exists "$DB_NAME"

echo "=== إنشاء قاعدة اختبار فارغة تمامًا ==="
createdb "$DB_NAME"

echo "=== تطبيق كل ملفات الترحيل بالترتيب الرقمي (000 حتى 033) ==="
for f in "${REQUIRED_FILES[@]}"; do
  echo "--- تطبيق: $f ---"
  psql -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$MIGRATIONS_DIR/$f"
done

echo "=== التحقق السريع: عدد المخططات المُنشَأة ==="
psql -d "$DB_NAME" -c "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT LIKE 'pg_%' AND schema_name != 'information_schema' ORDER BY schema_name;"

echo "=== نجاح: قاعدة الاختبار '$DB_NAME' جاهزة بكل الجداول من الصفر (33 ملف ترحيل مُطبَّق) ==="
