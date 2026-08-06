"""
conftest.py — إعداد تلقائي لمسارات الاستيراد (sys.path) لكل الخدمات الثلاث عشرة
=================================================================================
يُحمَّل هذا الملف تلقائيًا بواسطة pytest قبل أي اختبار؛ لا حاجة لأي إعداد
يدوي إضافي. يُضيف مجلد src/ لكل خدمة إلى sys.path، بحيث تعمل عبارات
الاستيراد المضمَّنة داخل دوال الاختبار (كـ"from ntf_repository import
PostgresNtfRepository") مباشرة دون أي تكوين خارجي.
"""

import os
import sys

_PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
_SERVICES_DIR = os.path.join(_PACKAGE_ROOT, "..", "services")

_SERVICE_NAMES = [
    "search", "auth", "store", "inventory_item", "pct", "vct", "cmp",
    "order", "message", "ntf", "scheduler", "trm", "message_extended",
    "ref", "sub", "cnt", "sup", "shared",
]

for _name in _SERVICE_NAMES:
    _src_path = os.path.abspath(os.path.join(_SERVICES_DIR, _name, "src"))
    if os.path.isdir(_src_path) and _src_path not in sys.path:
        sys.path.insert(0, _src_path)
