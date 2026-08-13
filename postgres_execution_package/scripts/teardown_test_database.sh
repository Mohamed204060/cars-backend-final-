#!/usr/bin/env bash
# teardown_test_database.sh
# الحالة: Prepared — لم يُشغَّل فعليًا في هذه الجلسة
# الغرض: إسقاط قاعدة الاختبار بالكامل بعد انتهاء التنفيذ، لضمان عدم بقاء أثر بين دورات الاختبار.

set -euo pipefail
DB_NAME="${DB_NAME:-carparts_test}"

echo "=== إسقاط قاعدة الاختبار '$DB_NAME' ==="
dropdb --if-exists "$DB_NAME"
echo "=== تم ==="
