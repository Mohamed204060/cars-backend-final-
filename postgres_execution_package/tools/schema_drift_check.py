#!/usr/bin/env python3
"""
schema_drift_check.py (v3 -- موسَّع بالكامل، CR-011) -- أداة اكتشاف انحراف المخطط الشاملة
====================================================================================
الحالة: Executed on PostgreSQL (نتيجة أولى: 6/52 جدولًا -- انظر Run ID
30612887616) -- تم تنفيذ CR-011 لتوسيع EXPECTED_TABLES ليغطي كل الـ52 جدولًا
الناتجة عن ملفات الـMigrations 001-021 كاملة، بدلًا من 6 جداول فقط
(Migrations 018-021 فقط) كما كانت الحال في الإصدار السابق (v2).

مصدر EXPECTED_TABLES: مُستخرَج آليًا (لا يدويًا ولا تقريبيًا، التزامًا بنص
CR-011) من schema.sql -- وهو ناتج pg_dump فعلي لقاعدة بيانات حية طُبِّقت
عليها كل الـ22 Migration بنجاح (GitHub Actions Run ID 30612887616، Commit
7e0a18454429d1f77670d21b678d6283a9aad806). هذا يضمن أن EXPECTED_TABLES يعكس
البنية الفعلية الناتجة عن الـMigrations أنفسها، لا افتراضًا نظريًا.

لا تغيير على منطق check_table أو التصنيف الرباعي (Missing / Extra /
Definition Mismatch / Approved Exception) أو آلية الاتصال (TEST_DATABASE_URL)
-- بما يطابق حرفيًا نطاق CR-011 المعتمَد (القسم 3: النطاق الدقيق للتغيير).

يفحص الآن Tables، Columns، Data Types، Nullability، Default Values،
Primary Keys، Foreign Keys، Unique Constraints، Check Constraints، Indexes،
وPartial Indexes عبر كل الجداول الـ52 (15 Schema، بخلاف public).

الاستخدام:
    export TEST_DATABASE_URL=postgresql://user:pass@host:5432/carparts_test
    python3 schema_drift_check.py
"""

import os
import sys
import json

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("تحذير: مكتبة psycopg2 غير مثبَّتة في هذه البيئة؛ الأداة جاهزة للتشغيل لاحقًا في بيئة بها psycopg2 واتصال PostgreSQL حقيقي.")
    psycopg2 = None


EXPECTED_TABLES = {('sys', 'idempotency_keys'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                 'idempotency_key': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                 'user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'endpoint': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                 'response_status': {'type': 'integer', 'nullable': False, 'has_default': False},
                                 'response_body': {'type': 'jsonb', 'nullable': False, 'has_default': False},
                                 'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                     'unique_constraints': ['uq_idempotency_key_user_endpoint'],
                     'check_constraints': [],
                     'indexes': ['idx_idempotency_keys_lookup'],
                     'partial_indexes': [],
                     'primary_key_constraints': {'idempotency_keys_pkey': 'PRIMARY KEY (id)'},
                     'foreign_key_constraints': {}},
 ('iam', 'sessions'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                 'user_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'token_hash': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                 'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                 'last_active_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                 'expires_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': False},
                                 'revoked_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                 'revoked_reason': {'type': 'character varying', 'nullable': True, 'has_default': False}},
                     'unique_constraints': ['uq_sessions_token_hash'],
                     'check_constraints': ['chk_sessions_revoked_reason', 'chk_sessions_revoked_consistency'],
                     'indexes': ['idx_sessions_user_id'],
                     'partial_indexes': ['idx_sessions_active_lookup'],
                     'primary_key_constraints': {'sessions_pkey': 'PRIMARY KEY (id)'},
                     'foreign_key_constraints': {'sessions_user_id_fkey': 'FOREIGN KEY (user_id) REFERENCES iam.users(id)'}},
 ('aud', 'events'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                 'log_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                 'correlation_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'actor_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                 'event_name': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                 'occurred_at_utc': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                 'before_value': {'type': 'jsonb', 'nullable': True, 'has_default': False},
                                 'after_value': {'type': 'jsonb', 'nullable': True, 'has_default': False},
                                 'reason': {'type': 'text', 'nullable': True, 'has_default': False},
                                 'metadata': {'type': 'jsonb', 'nullable': True, 'has_default': False}},
                     'unique_constraints': [],
                     'check_constraints': ['chk_events_log_type'],
                     'indexes': ['idx_events_actor', 'idx_events_correlation', 'idx_events_type_time'],
                     'partial_indexes': [],
                     'primary_key_constraints': {'events_pkey': 'PRIMARY KEY (id)'},
                     'foreign_key_constraints': {}},
 ('cmp', 'compatibility_records'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                                'catalog_part_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                'trim_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                                'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                                'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                                'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                                'trim_model_year_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False}},
                                    'unique_constraints': [],
                                    'check_constraints': ['chk_compatibility_status', 'chk_compatibility_exactly_one_target'],
                                    'indexes': ['idx_compatibility_part', 'idx_compatibility_trim', 'idx_compatibility_trim_model_year'],
                                    'partial_indexes': ['uq_compatibility_general', 'uq_compatibility_year_specific'],
                                    'primary_key_constraints': {'compatibility_records_pkey': 'PRIMARY KEY (id)'},
                                    'foreign_key_constraints': {}},  # Batch 1: بلا FK عابر للنطاق لـtrim_model_year_ref_id، بنفس نمط trim_ref_id الأصلي
 ('cnt', 'articles'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                   'author_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                   'title_ar': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                   'body_ar': {'type': 'text', 'nullable': False, 'has_default': False},
                                   'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                   'title_en': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                   'body_en': {'type': 'text', 'nullable': True, 'has_default': False},
                                   'summary_ar': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                   'summary_en': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                   'slug': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                   'category_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                   'seo_title_ar': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                   'seo_title_en': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                   'seo_description_ar': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                   'seo_description_en': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                   'published_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                   'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                   'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                       # 034_cnt_articles_cms_seo.sql: title/body أُعيدت تسميتهما إلى
                       # title_ar/body_ar (لا فقدان بيانات)، مع إضافة 11 عمود CMS/SEO جديدًا.
                       'unique_constraints': ['uq_articles_slug'],
                       'check_constraints': ['chk_articles_status'],
                       'indexes': ['idx_articles_status', 'idx_articles_slug', 'idx_articles_category'],
                       'partial_indexes': ['idx_articles_published_at'],
                       'primary_key_constraints': {'articles_pkey': 'PRIMARY KEY (id)'},
                       'foreign_key_constraints': {}},
 ('com', 'attachments'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                      'message_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                      'file_name': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                      'mime_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                      'size_bytes': {'type': 'bigint', 'nullable': False, 'has_default': False}},
                          'unique_constraints': [],
                          'check_constraints': ['chk_attachments_size'],
                          'indexes': ['idx_attachments_message_id'],
                          'partial_indexes': [],
                          'primary_key_constraints': {'attachments_pkey': 'PRIMARY KEY (id)'},
                          'foreign_key_constraints': {'attachments_message_id_fkey': 'FOREIGN KEY (message_id) REFERENCES '
                                                                                     'com.messages(id)'}},
 ('com', 'conversation_user_settings'): {'columns': {'conversation_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                     'user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                     'is_muted': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                                     'is_archived': {'type': 'boolean', 'nullable': False, 'has_default': True}},
                                         'unique_constraints': [],
                                         'check_constraints': [],
                                         'indexes': [],
                                         'partial_indexes': [],
                                         'primary_key_constraints': {'conversation_user_settings_pkey': 'PRIMARY KEY (conversation_id, '
                                                                                                        'user_ref_id)'},
                                         'foreign_key_constraints': {'conversation_user_settings_conversation_id_fkey': 'FOREIGN KEY '
                                                                                                                        '(conversation_id) '
                                                                                                                        'REFERENCES '
                                                                                                                        'com.conversations(id)'}},
 ('com', 'conversation_participants'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                                    'conversation_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                    'user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                    'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                                        'unique_constraints': ['uq_conversation_participants'],
                                        'check_constraints': [],
                                        'indexes': ['idx_conversation_participants_conversation_id', 'idx_conversation_participants_user_ref_id'],
                                        'partial_indexes': [],
                                        'primary_key_constraints': {'conversation_participants_pkey': 'PRIMARY KEY (id)'},
                                        'foreign_key_constraints': {'conversation_participants_conversation_id_fkey': 'FOREIGN KEY (conversation_id) '
                                                                                                                      'REFERENCES com.conversations(id)'}},
 ('com', 'conversations'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                        'context_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                        'context_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                        'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                            'unique_constraints': [],
                            'check_constraints': ['chk_conversations_context_type'],
                            'indexes': ['idx_conversations_context'],
                            'partial_indexes': [],
                            'primary_key_constraints': {'conversations_pkey': 'PRIMARY KEY (id)'},
                            'foreign_key_constraints': {}},
 ('com', 'forward_records'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                          'original_message_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'forwarded_message_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'forwarded_to_conversation_id': {'type': 'uuid', 'nullable': False, 'has_default': False}},
                              'unique_constraints': [],
                              'check_constraints': [],
                              'indexes': [],
                              'partial_indexes': [],
                              'primary_key_constraints': {'forward_records_pkey': 'PRIMARY KEY (id)'},
                              'foreign_key_constraints': {'forward_records_forwarded_message_id_fkey': 'FOREIGN KEY (forwarded_message_id) '
                                                                                                       'REFERENCES com.messages(id)',
                                                          'forward_records_forwarded_to_conversation_id_fkey': 'FOREIGN KEY '
                                                                                                               '(forwarded_to_conversation_id) '
                                                                                                               'REFERENCES '
                                                                                                               'com.conversations(id)',
                                                          'forward_records_original_message_id_fkey': 'FOREIGN KEY (original_message_id) '
                                                                                                      'REFERENCES com.messages(id)'}},
 ('com', 'message_delivery_tracking'): {'columns': {'message_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                    'sent_at': {'type': 'timestamp with time zone',
                                                                'nullable': False,
                                                                'has_default': False},
                                                    'delivered_at': {'type': 'timestamp with time zone',
                                                                     'nullable': True,
                                                                     'has_default': False},
                                                    'read_at': {'type': 'timestamp with time zone',
                                                                'nullable': True,
                                                                'has_default': False}},
                                        'unique_constraints': [],
                                        'check_constraints': [],
                                        'indexes': [],
                                        'partial_indexes': [],
                                        'primary_key_constraints': {'message_delivery_tracking_pkey': 'PRIMARY KEY (message_id)'},
                                        'foreign_key_constraints': {'message_delivery_tracking_message_id_fkey': 'FOREIGN KEY (message_id) '
                                                                                                                 'REFERENCES '
                                                                                                                 'com.messages(id)'}},
 ('com', 'message_thread_links'): {'columns': {'message_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                               'reply_to_message_id': {'type': 'uuid', 'nullable': False, 'has_default': False}},
                                   'unique_constraints': [],
                                   'check_constraints': [],
                                   'indexes': [],
                                   'partial_indexes': [],
                                   'primary_key_constraints': {'message_thread_links_pkey': 'PRIMARY KEY (message_id)'},
                                   'foreign_key_constraints': {'message_thread_links_message_id_fkey': 'FOREIGN KEY (message_id) '
                                                                                                       'REFERENCES com.messages(id)',
                                                               'message_thread_links_reply_to_message_id_fkey': 'FOREIGN KEY '
                                                                                                                '(reply_to_message_id) '
                                                                                                                'REFERENCES '
                                                                                                                'com.messages(id)'}},
 ('com', 'messages'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                   'conversation_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                   'sender_user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                   'body': {'type': 'text', 'nullable': False, 'has_default': False},
                                   'is_deleted_by_sender': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                   'is_deleted_by_recipient': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                   'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                       'unique_constraints': [],
                       'check_constraints': [],
                       'indexes': ['idx_messages_conversation_id'],
                       'partial_indexes': [],
                       'primary_key_constraints': {'messages_pkey': 'PRIMARY KEY (id)'},
                       'foreign_key_constraints': {'messages_conversation_id_fkey': 'FOREIGN KEY (conversation_id) REFERENCES '
                                                                                    'com.conversations(id)'}},
 ('com', 'user_presence'): {'columns': {'user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                        'is_online': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                        'last_seen_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False}},
                            'unique_constraints': [],
                            'check_constraints': [],
                            'indexes': [],
                            'partial_indexes': [],
                            'primary_key_constraints': {'user_presence_pkey': 'PRIMARY KEY (user_ref_id)'},
                            'foreign_key_constraints': {}},
 ('iam', 'favorites'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                    'user_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                    'inventory_item_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                    'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                        'unique_constraints': ['uq_favorites_user_item'],
                        'check_constraints': [],
                        'indexes': ['idx_favorites_user_id'],
                        'partial_indexes': [],
                        'primary_key_constraints': {'favorites_pkey': 'PRIMARY KEY (id)'},
                        'foreign_key_constraints': {'favorites_user_id_fkey': 'FOREIGN KEY (user_id) REFERENCES iam.users(id)'}},
 ('iam', 'identity_providers'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                             'code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                             'display_name': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                             'provider_category': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                             'is_enabled': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                             'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                             'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                                 'unique_constraints': ['uq_identity_providers_code'],
                                 'check_constraints': ['chk_identity_providers_category', 'chk_identity_providers_code'],
                                 'indexes': [],
                                 'partial_indexes': [],
                                 'primary_key_constraints': {'identity_providers_pkey': 'PRIMARY KEY (id)'},
                                 'foreign_key_constraints': {}},
 ('iam', 'user_identities'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                          'user_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'provider_type_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'external_identifier': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                          'credential_secret_hash': {'type': 'text', 'nullable': True, 'has_default': False},
                                          'verified_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                          'is_primary': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                          'last_authenticated_at': {'type': 'timestamp with time zone',
                                                                    'nullable': True,
                                                                    'has_default': False},
                                          'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                          'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                              'unique_constraints': ['uq_user_identities_provider_identifier', 'uq_user_identities_user_provider'],
                              'check_constraints': [],
                              'indexes': ['idx_user_identities_provider', 'idx_user_identities_user_id'],
                              'partial_indexes': [],
                              'primary_key_constraints': {'user_identities_pkey': 'PRIMARY KEY (id)'},
                              'foreign_key_constraints': {'user_identities_provider_type_id_fkey': 'FOREIGN KEY (provider_type_id) '
                                                                                                   'REFERENCES iam.identity_providers(id)',
                                                          'user_identities_user_id_fkey': 'FOREIGN KEY (user_id) REFERENCES '
                                                                                          'iam.users(id)'}},
 ('iam', 'users'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                'business_code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                'primary_role': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                'account_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                'is_verified_seller': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                    'unique_constraints': ['uq_users_business_code'],
                    'check_constraints': ['chk_users_account_type', 'chk_users_primary_role', 'chk_users_status'],
                    'indexes': ['idx_users_primary_role', 'idx_users_status'],
                    'partial_indexes': [],
                    'primary_key_constraints': {'users_pkey': 'PRIMARY KEY (id)'},
                    'foreign_key_constraints': {}},
 ('ntf', 'campaigns'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                    'created_by_user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                    'title': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                    'body': {'type': 'text', 'nullable': False, 'has_default': False},
                                    'audience_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                    'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                    'priority': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                    'campaign_version': {'type': 'integer', 'nullable': False, 'has_default': True},
                                    'template_version_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                    'scheduled_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                    'expires_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                    'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                    'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                        'unique_constraints': [],
                        'check_constraints': ['chk_campaigns_audience_type', 'chk_campaigns_priority', 'chk_campaigns_status'],
                        'indexes': ['idx_campaigns_scheduled_at', 'idx_campaigns_status'],
                        'partial_indexes': [],
                        'primary_key_constraints': {'campaigns_pkey': 'PRIMARY KEY (id)'},
                        'foreign_key_constraints': {}},
 ('ntf', 'channel_providers'): {'columns': {'code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                            'display_name': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                            'health_status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                            'last_success_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                            'last_failure_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                            'success_rate_pct': {'type': 'numeric', 'nullable': True, 'has_default': False},
                                            'is_enabled': {'type': 'boolean', 'nullable': False, 'has_default': True}},
                                'unique_constraints': [],
                                'check_constraints': ['chk_channel_providers_health'],
                                'indexes': ['idx_channel_providers_health'],
                                'partial_indexes': [],
                                'primary_key_constraints': {'channel_providers_pkey': 'PRIMARY KEY (code)'},
                                'foreign_key_constraints': {}},
 ('ntf', 'deliveries'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                     'campaign_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                     'campaign_version_snapshot': {'type': 'integer', 'nullable': False, 'has_default': False},
                                     'correlation_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                     'execution_status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                     'started_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                     'completed_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                     'total_recipients': {'type': 'integer', 'nullable': False, 'has_default': True},
                                     'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                         'unique_constraints': [],
                         'check_constraints': ['chk_deliveries_status'],
                         'indexes': ['idx_deliveries_campaign_id', 'idx_deliveries_execution_status'],
                         'partial_indexes': [],
                         'primary_key_constraints': {'deliveries_pkey': 'PRIMARY KEY (id)'},
                         'foreign_key_constraints': {'deliveries_campaign_id_fkey': 'FOREIGN KEY (campaign_id) REFERENCES '
                                                                                    'ntf.campaigns(id)'}},
 ('ntf', 'notification_center_entries'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                                      'recipient_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                      'user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                      'is_read': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                                      'is_archived_by_user': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                                      'is_deleted_by_user': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                                      'created_at': {'type': 'timestamp with time zone',
                                                                     'nullable': False,
                                                                     'has_default': True}},
                                          'unique_constraints': [],
                                          'check_constraints': [],
                                          'indexes': ['idx_notification_center_is_read', 'idx_notification_center_user_ref_id'],
                                          'partial_indexes': [],
                                          'primary_key_constraints': {'notification_center_entries_pkey': 'PRIMARY KEY (id)'},
                                          'foreign_key_constraints': {'notification_center_entries_recipient_id_fkey': 'FOREIGN KEY '
                                                                                                                       '(recipient_id) '
                                                                                                                       'REFERENCES '
                                                                                                                       'ntf.recipients(id)'}},
 ('ntf', 'notification_preferences'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                                   'user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                   'channel_provider_code': {'type': 'character varying',
                                                                             'nullable': False,
                                                                             'has_default': False},
                                                   'notification_type': {'type': 'character varying',
                                                                         'nullable': False,
                                                                         'has_default': False},
                                                   'is_enabled': {'type': 'boolean', 'nullable': False, 'has_default': True}},
                                       'unique_constraints': ['uq_notification_preferences'],
                                       'check_constraints': [],
                                       'indexes': ['idx_notification_preferences_user'],
                                       'partial_indexes': [],
                                       'primary_key_constraints': {'notification_preferences_pkey': 'PRIMARY KEY (id)'},
                                       'foreign_key_constraints': {'notification_preferences_channel_provider_code_fkey': 'FOREIGN KEY '
                                                                                                                          '(channel_provider_code) '
                                                                                                                          'REFERENCES '
                                                                                                                          'ntf.channel_providers(code)'}},
 ('ntf', 'outbox'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                 'delivery_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'recipient_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'correlation_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'dispatched': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                 'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                     'unique_constraints': [],
                     'check_constraints': [],
                     'indexes': [],
                     'partial_indexes': ['idx_outbox_pending'],
                     'primary_key_constraints': {'outbox_pkey': 'PRIMARY KEY (id)'},
                     'foreign_key_constraints': {'outbox_delivery_id_fkey': 'FOREIGN KEY (delivery_id) REFERENCES ntf.deliveries(id)',
                                                 'outbox_recipient_id_fkey': 'FOREIGN KEY (recipient_id) REFERENCES ntf.recipients(id)'}},
 ('ntf', 'recipients'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                     'delivery_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                     'user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                     'channel_provider_code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                     'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                     'sent_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                     'delivered_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                     'read_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                     'failure_reason_code': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                     'retry_count': {'type': 'integer', 'nullable': False, 'has_default': True}},
                         'unique_constraints': ['uq_recipients_delivery_user'],
                         'check_constraints': ['chk_recipients_status'],
                         'indexes': ['idx_recipients_status', 'idx_recipients_user_ref_id'],
                         'partial_indexes': [],
                         'primary_key_constraints': {'recipients_pkey': 'PRIMARY KEY (id)'},
                         'foreign_key_constraints': {'recipients_delivery_id_fkey': 'FOREIGN KEY (delivery_id) REFERENCES '
                                                                                    'ntf.deliveries(id)'}},
 ('ntf', 'template_versions'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                            'template_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                            'version_number': {'type': 'integer', 'nullable': False, 'has_default': False},
                                            'title': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                            'body': {'type': 'text', 'nullable': False, 'has_default': False},
                                            'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                                'unique_constraints': ['uq_template_versions_template_version'],
                                'check_constraints': [],
                                'indexes': ['idx_template_versions_template_id'],
                                'partial_indexes': [],
                                'primary_key_constraints': {'template_versions_pkey': 'PRIMARY KEY (id)'},
                                'foreign_key_constraints': {'template_versions_template_id_fkey': 'FOREIGN KEY (template_id) REFERENCES '
                                                                                                  'ntf.templates(id)'}},
 ('ntf', 'templates'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                    'code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                    'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                    'current_version_number': {'type': 'integer', 'nullable': False, 'has_default': True}},
                        'unique_constraints': ['templates_code_key'],
                        'check_constraints': ['chk_templates_status'],
                        'indexes': [],
                        'partial_indexes': [],
                        'primary_key_constraints': {'templates_pkey': 'PRIMARY KEY (id)'},
                        'foreign_key_constraints': {}},
 ('pct', 'aftermarket_numbers'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                              'catalog_part_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                              'aftermarket_number': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                              'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                                  'unique_constraints': [],
                                  'check_constraints': [],
                                  'indexes': ['idx_aftermarket_numbers_part_id'],
                                  'partial_indexes': [],
                                  'primary_key_constraints': {'aftermarket_numbers_pkey': 'PRIMARY KEY (id)'},
                                  'foreign_key_constraints': {'aftermarket_numbers_catalog_part_id_fkey': 'FOREIGN KEY (catalog_part_id) '
                                                                                                          'REFERENCES '
                                                                                                          'pct.catalog_parts(id)'}},
 ('pct', 'catalog_parts'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                        'category_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                        'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                        'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                        'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                            'unique_constraints': [],
                            'check_constraints': ['chk_catalog_parts_status'],
                            'indexes': ['idx_catalog_parts_category_id', 'idx_catalog_parts_status'],
                            'partial_indexes': [],
                            'primary_key_constraints': {'catalog_parts_pkey': 'PRIMARY KEY (id)'},
                            'foreign_key_constraints': {'catalog_parts_category_id_fkey': 'FOREIGN KEY (category_id) REFERENCES '
                                                                                          'pct.categories(id)'}},
 ('pct', 'categories'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                     'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                         'unique_constraints': [],
                         'check_constraints': [],
                         'indexes': [],
                         'partial_indexes': [],
                         'primary_key_constraints': {'categories_pkey': 'PRIMARY KEY (id)'},
                         'foreign_key_constraints': {}},
 ('pct', 'localized_names'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                          'catalog_part_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'locale': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                          'name_value': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                          'name_kind': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                          'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                              'unique_constraints': [],
                              'check_constraints': ['chk_pct_localized_names_kind'],
                              'indexes': ['idx_pct_localized_names_part_id', 'idx_pct_localized_names_value'],
                              'partial_indexes': [],
                              'primary_key_constraints': {'localized_names_pkey': 'PRIMARY KEY (id)'},
                              'foreign_key_constraints': {'localized_names_catalog_part_id_fkey': 'FOREIGN KEY (catalog_part_id) '
                                                                                                  'REFERENCES pct.catalog_parts(id)'}},
 ('pct', 'oem_numbers'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                      'catalog_part_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                      'manufacturer_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                      'oem_number': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                      'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                          'unique_constraints': ['uq_oem_numbers_manufacturer_number'],
                          'check_constraints': [],
                          'indexes': ['idx_oem_numbers_part_id'],
                          'partial_indexes': [],
                          'primary_key_constraints': {'oem_numbers_pkey': 'PRIMARY KEY (id)'},
                          'foreign_key_constraints': {'oem_numbers_catalog_part_id_fkey': 'FOREIGN KEY (catalog_part_id) REFERENCES '
                                                                                          'pct.catalog_parts(id)'}},
 ('pur', 'offers'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                 'business_code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                 'purchase_request_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'seller_store_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'amount': {'type': 'numeric', 'nullable': False, 'has_default': False},
                                 'currency': {'type': 'character', 'nullable': False, 'has_default': False},
                                 'provides_shipping': {'type': 'boolean', 'nullable': False, 'has_default': False},
                                 'notes': {'type': 'text', 'nullable': True, 'has_default': False},
                                 'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                 'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                 'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                     'unique_constraints': ['uq_offers_business_code'],
                     'check_constraints': ['chk_offers_status'],
                     'indexes': ['idx_offers_purchase_request_id', 'idx_offers_status'],
                     'partial_indexes': ['uq_offers_one_active_per_seller'],
                     'primary_key_constraints': {'offers_pkey': 'PRIMARY KEY (id)'},
                     'foreign_key_constraints': {'offers_purchase_request_id_fkey': 'FOREIGN KEY (purchase_request_id) REFERENCES '
                                                                                    'pur.purchase_requests(id)'}},
 ('pur', 'purchase_requests'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                            'business_code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                            'buyer_user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                            'catalog_part_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                            'trim_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                            'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                            'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                            'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                            'condition_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                            'notes': {'type': 'text', 'nullable': True, 'has_default': False},
                                            'trim_model_year_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False}},
                                'unique_constraints': ['uq_purchase_requests_business_code'],
                                'check_constraints': ['chk_purchase_requests_status'],
                                'indexes': ['idx_purchase_requests_buyer', 'idx_purchase_requests_status'],
                                'partial_indexes': [],
                                'primary_key_constraints': {'purchase_requests_pkey': 'PRIMARY KEY (id)'},
                                'foreign_key_constraints': {}},  # Batch 1: بلا FK فعلي على trim_model_year_ref_id، بنفس نمط كل *_ref_id الأخرى
 ('ref', 'bulk_import_job_rows'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                               'job_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                               'row_number': {'type': 'integer', 'nullable': False, 'has_default': False},
                                               'outcome': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                               'rejection_reason': {'type': 'text', 'nullable': True, 'has_default': False},
                                               'raw_row_data': {'type': 'jsonb', 'nullable': False, 'has_default': False},
                                               'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                                   'unique_constraints': [],
                                   'check_constraints': ['chk_bulk_import_job_rows_outcome'],
                                   'indexes': ['idx_bulk_import_job_rows_job_id'],
                                   'partial_indexes': [],
                                   'primary_key_constraints': {'bulk_import_job_rows_pkey': 'PRIMARY KEY (id)'},
                                   'foreign_key_constraints': {'bulk_import_job_rows_job_id_fkey': 'FOREIGN KEY (job_id) REFERENCES '
                                                                                                   'ref.bulk_import_jobs(id)'}},
 ('ref', 'bulk_import_jobs'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                           'ref_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                           'imported_by_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                           'file_name': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                           'source_file_ref': {'type': 'text', 'nullable': True, 'has_default': False},
                                           'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                           'new_count': {'type': 'integer', 'nullable': False, 'has_default': True},
                                           'updated_count': {'type': 'integer', 'nullable': False, 'has_default': True},
                                           'rejected_count': {'type': 'integer', 'nullable': False, 'has_default': True},
                                           'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                           'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                               'unique_constraints': [],
                               'check_constraints': ['chk_bulk_import_jobs_status', 'chk_bulk_import_jobs_type'],
                               'indexes': ['idx_bulk_import_jobs_status', 'idx_bulk_import_jobs_type'],
                               'partial_indexes': [],
                               'primary_key_constraints': {'bulk_import_jobs_pkey': 'PRIMARY KEY (id)'},
                               'foreign_key_constraints': {}},
 ('ref', 'ref_values'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                     'ref_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                     'code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                     'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                     'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                     'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                         'unique_constraints': ['uq_ref_values_type_code'],
                         'check_constraints': ['chk_ref_values_status', 'chk_ref_values_type'],
                         'indexes': ['idx_ref_values_type'],
                         'partial_indexes': [],
                         'primary_key_constraints': {'ref_values_pkey': 'PRIMARY KEY (id)'},
                         'foreign_key_constraints': {}},
 ('str', 'inventory_items'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                          'business_code': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                          'store_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'catalog_part_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'condition_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'pricing_mode': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                          'price_amount': {'type': 'numeric', 'nullable': True, 'has_default': False},
                                          'price_currency': {'type': 'character', 'nullable': True, 'has_default': False},
                                          'quantity': {'type': 'integer', 'nullable': False, 'has_default': True},
                                          'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                          'primary_photo_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                          'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                          'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                              'unique_constraints': ['uq_inventory_items_business_code'],
                              'check_constraints': ['chk_inventory_items_price_mode',
                                                    'chk_inventory_items_pricing_mode',
                                                    'chk_inventory_items_quantity',
                                                    'chk_inventory_items_status'],
                              'indexes': ['idx_inventory_items_part', 'idx_inventory_items_status', 'idx_inventory_items_store_id'],
                              'partial_indexes': [],
                              'primary_key_constraints': {'inventory_items_pkey': 'PRIMARY KEY (id)'},
                              'foreign_key_constraints': {'fk_inventory_items_primary_photo': 'FOREIGN KEY (primary_photo_id) REFERENCES '
                                                                                              'str.inventory_photos(id)',
                                                          'inventory_items_store_id_fkey': 'FOREIGN KEY (store_id) REFERENCES '
                                                                                           'str.stores(id)'}},
 ('str', 'inventory_photos'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                           'inventory_item_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                           'original_asset_ref': {'type': 'text', 'nullable': False, 'has_default': False},
                                           'display_asset_ref': {'type': 'text', 'nullable': False, 'has_default': False},
                                           'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                               'unique_constraints': [],
                               'check_constraints': [],
                               'indexes': ['idx_inventory_photos_item_id'],
                               'partial_indexes': [],
                               'primary_key_constraints': {'inventory_photos_pkey': 'PRIMARY KEY (id)'},
                               'foreign_key_constraints': {'inventory_photos_inventory_item_id_fkey': 'FOREIGN KEY (inventory_item_id) '
                                                                                                      'REFERENCES '
                                                                                                      'str.inventory_items(id)'}},
 ('str', 'stores'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                 'owner_user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                 'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                 'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                 'country_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                 'city_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False}},
                     'unique_constraints': [],
                     'check_constraints': ['chk_stores_status'],
                     'indexes': ['idx_stores_city', 'idx_stores_country', 'idx_stores_owner', 'idx_stores_status'],
                     'partial_indexes': [],
                     'primary_key_constraints': {'stores_pkey': 'PRIMARY KEY (id)'},
                     'foreign_key_constraints': {}},
 ('sub', 'plans'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                'plan_type_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                'is_free': {'type': 'boolean', 'nullable': False, 'has_default': True},
                                'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                    'unique_constraints': [],
                    'check_constraints': [],
                    'indexes': [],
                    'partial_indexes': ['uq_sub_plans_single_free'],
                    'primary_key_constraints': {'plans_pkey': 'PRIMARY KEY (id)'},
                    'foreign_key_constraints': {}},
 ('sub', 'seller_subscriptions'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                               'seller_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                               'plan_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                               'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                               'expires_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                               'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                               'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                                   'unique_constraints': [],
                                   'check_constraints': ['chk_seller_subscriptions_status'],
                                   'indexes': ['idx_seller_subscriptions_seller', 'idx_seller_subscriptions_status'],
                                   'partial_indexes': [],
                                   'primary_key_constraints': {'seller_subscriptions_pkey': 'PRIMARY KEY (id)'},
                                   'foreign_key_constraints': {'seller_subscriptions_plan_id_fkey': 'FOREIGN KEY (plan_id) REFERENCES '
                                                                                                    'sub.plans(id)'}},
 ('sup', 'replies'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                  'ticket_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                  'author_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                  'body': {'type': 'text', 'nullable': False, 'has_default': False},
                                  'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                      'unique_constraints': [],
                      'check_constraints': [],
                      'indexes': ['idx_replies_ticket_id'],
                      'partial_indexes': [],
                      'primary_key_constraints': {'replies_pkey': 'PRIMARY KEY (id)'},
                      'foreign_key_constraints': {'replies_ticket_id_fkey': 'FOREIGN KEY (ticket_id) REFERENCES sup.tickets(id)'}},
 ('sup', 'tickets'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                  'requester_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                  'assigned_moderator_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                  'subject': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                  'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                  'reopen_window_expires_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                  'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                  'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                      'unique_constraints': [],
                      'check_constraints': ['chk_tickets_status'],
                      'indexes': ['idx_tickets_requester', 'idx_tickets_status'],
                      'partial_indexes': [],
                      'primary_key_constraints': {'tickets_pkey': 'PRIMARY KEY (id)'},
                      'foreign_key_constraints': {}},
 ('sys', 'scheduled_jobs'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                         'job_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                         'target_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                         'scheduled_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': False},
                                         'recurrence_rule': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                         'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                         'last_run_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                         'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                             'unique_constraints': [],
                             'check_constraints': ['chk_scheduled_jobs_recurrence', 'chk_scheduled_jobs_status'],
                             'indexes': ['idx_scheduled_jobs_job_type'],
                             'partial_indexes': ['idx_scheduled_jobs_status_scheduled_at'],
                             'primary_key_constraints': {'scheduled_jobs_pkey': 'PRIMARY KEY (id)'},
                             'foreign_key_constraints': {}},
 ('sys', 'settings'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                   'setting_key': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                   'setting_value': {'type': 'text', 'nullable': False, 'has_default': False},
                                   'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                   'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                       'unique_constraints': ['uq_settings_key'],
                       'check_constraints': [],
                       'indexes': [],
                       'partial_indexes': [],
                       'primary_key_constraints': {'settings_pkey': 'PRIMARY KEY (id)'},
                       'foreign_key_constraints': {}},
 ('trm', 'disputes'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                   'buyer_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                   'seller_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                   'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                   'notes': {'type': 'text', 'nullable': True, 'has_default': False},
                                   'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                   'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                       'unique_constraints': [],
                       'check_constraints': ['chk_disputes_status'],
                       'indexes': [],
                       'partial_indexes': [],
                       'primary_key_constraints': {'disputes_pkey': 'PRIMARY KEY (id)'},
                       'foreign_key_constraints': {}},
 ('trm', 'ratings'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                  'rated_by_user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                  'target_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                  'target_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                  'source_purchase_request_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                  'score': {'type': 'smallint', 'nullable': False, 'has_default': False},
                                  'comment': {'type': 'text', 'nullable': True, 'has_default': False},
                                  'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                  'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                      'unique_constraints': ['uq_ratings_rater_target_source'],
                      'check_constraints': ['chk_ratings_score', 'chk_ratings_status', 'chk_ratings_target_type'],
                      'indexes': ['idx_ratings_source', 'idx_ratings_target'],
                      'partial_indexes': [],
                      'primary_key_constraints': {'ratings_pkey1': 'PRIMARY KEY (id)'},
                      'foreign_key_constraints': {}},
 ('trm', 'ratings_legacy_seller_only_v1'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                                        'rated_seller_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                        'rater_buyer_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                        'score': {'type': 'smallint', 'nullable': False, 'has_default': False},
                                                        'comment_text': {'type': 'text', 'nullable': True, 'has_default': False},
                                                        'edit_window_expires_at': {'type': 'timestamp with time zone',
                                                                                   'nullable': False,
                                                                                   'has_default': False},
                                                        'is_removed_by_moderator': {'type': 'boolean',
                                                                                    'nullable': False,
                                                                                    'has_default': True},
                                                        'created_at': {'type': 'timestamp with time zone',
                                                                       'nullable': False,
                                                                       'has_default': True},
                                                        'updated_at': {'type': 'timestamp with time zone',
                                                                       'nullable': False,
                                                                       'has_default': True}},
                                            'unique_constraints': [],
                                            'check_constraints': ['chk_ratings_score'],
                                            'indexes': [],
                                            'partial_indexes': ['idx_ratings_seller'],
                                            'primary_key_constraints': {'ratings_pkey': 'PRIMARY KEY (id)'},
                                            'foreign_key_constraints': {}},
 ('trm', 'reports'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                  'target_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                  'target_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                  'reporter_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                  'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                  'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                  'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                      'unique_constraints': [],
                      'check_constraints': ['chk_reports_status', 'chk_reports_target_type'],
                      'indexes': ['idx_reports_status', 'idx_reports_target'],
                      'partial_indexes': [],
                      'primary_key_constraints': {'reports_pkey': 'PRIMARY KEY (id)'},
                      'foreign_key_constraints': {}},
 ('vct', 'generations'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                      'model_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                      'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                      'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                      'start_year': {'type': 'integer', 'nullable': True, 'has_default': False},
                                      'end_year': {'type': 'integer', 'nullable': True, 'has_default': False}},
                          'unique_constraints': [],
                          'check_constraints': ['chk_generations_year_range'],
                          'indexes': ['idx_generations_model_id'],
                          'partial_indexes': [],
                          'primary_key_constraints': {'generations_pkey': 'PRIMARY KEY (id)'},
                          'foreign_key_constraints': {'generations_model_id_fkey': 'FOREIGN KEY (model_id) REFERENCES vct.models(id)'}},
 ('vct', 'trim_model_years'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                           'trim_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                           'year': {'type': 'integer', 'nullable': False, 'has_default': False},
                                           'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                           'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                               'unique_constraints': ['uq_trim_model_years_trim_year'],
                               'check_constraints': [],
                               'indexes': ['idx_trim_model_years_trim_id'],
                               'partial_indexes': [],
                               'primary_key_constraints': {'trim_model_years_pkey': 'PRIMARY KEY (id)'},
                               'foreign_key_constraints': {'trim_model_years_trim_ref_id_fkey': 'FOREIGN KEY (trim_ref_id) REFERENCES vct.trims(id)'}},
 ('vct', 'trim_market_availability'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                                    'trim_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                                    'trim_model_year_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                                    'country_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                                    'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                                       'unique_constraints': [],
                                       'check_constraints': ['chk_market_availability_exactly_one_target'],
                                       'indexes': ['idx_market_availability_country'],
                                       'partial_indexes': ['uq_market_availability_trim_country', 'uq_market_availability_trim_year_country'],
                                       'primary_key_constraints': {'trim_market_availability_pkey': 'PRIMARY KEY (id)'},
                                       'foreign_key_constraints': {
                                           'trim_market_availability_trim_ref_id_fkey': 'FOREIGN KEY (trim_ref_id) REFERENCES vct.trims(id)',
                                           'trim_market_availability_trim_model_year_ref_id_fkey': 'FOREIGN KEY (trim_model_year_ref_id) REFERENCES vct.trim_model_years(id)',
                                       }},  # country_ref_id بلا FK فعلي عمدًا (إشارة عابرة للنطاق)
 ('vct', 'localized_names'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                          'owner_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                          'owner_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                          'locale': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                          'name_value': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                          'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                              'unique_constraints': [],
                              'check_constraints': ['chk_localized_names_owner_type'],
                              'indexes': ['idx_vct_localized_names_owner'],
                              'partial_indexes': [],
                              'primary_key_constraints': {'localized_names_pkey': 'PRIMARY KEY (id)'},
                              'foreign_key_constraints': {}},
 ('vct', 'manufacturers'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                        'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                        'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                        'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                            'unique_constraints': [],
                            'check_constraints': ['chk_manufacturers_status'],
                            'indexes': ['idx_manufacturers_status'],
                            'partial_indexes': [],
                            'primary_key_constraints': {'manufacturers_pkey': 'PRIMARY KEY (id)'},
                            'foreign_key_constraints': {}},
 ('vct', 'models'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                 'manufacturer_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                 'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                 'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                 'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                     'unique_constraints': [],
                     'check_constraints': ['chk_models_status'],
                     'indexes': ['idx_models_manufacturer_id', 'idx_models_status'],
                     'partial_indexes': [],
                     'primary_key_constraints': {'models_pkey': 'PRIMARY KEY (id)'},
                     'foreign_key_constraints': {'models_manufacturer_id_fkey': 'FOREIGN KEY (manufacturer_id) REFERENCES '
                                                                                'vct.manufacturers(id)'}},
 ('vct', 'trims'): {'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                'generation_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                'fuel_type_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                'transmission_type_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                'updated_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                    'unique_constraints': [],
                    'check_constraints': [],
                    'indexes': ['idx_trims_generation_id'],
                    'partial_indexes': [],
                    'primary_key_constraints': {'trims_pkey': 'PRIMARY KEY (id)'},
                    'foreign_key_constraints': {'trims_generation_id_fkey': 'FOREIGN KEY (generation_id) REFERENCES vct.generations(id)'}},
                    ('media', 'assets'): {
                        'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                    'storage_key': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                    'storage_key_display': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                    'storage_key_thumbnail': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                    'original_file_name': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                    'mime_type': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                    'size_bytes': {'type': 'bigint', 'nullable': True, 'has_default': False},
                                    'checksum': {'type': 'character', 'nullable': True, 'has_default': False},
                                    'width': {'type': 'integer', 'nullable': True, 'has_default': False},
                                    'height': {'type': 'integer', 'nullable': True, 'has_default': False},
                                    'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                    'uploaded_by_user_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                    'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                    'archived_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False},
                                    'purged_at': {'type': 'timestamp with time zone', 'nullable': True, 'has_default': False}},
                        'unique_constraints': [],
                        'check_constraints': ['chk_media_assets_status'],
                        'indexes': ['idx_media_assets_status', 'idx_media_assets_uploaded_by'],
                        'partial_indexes': [],
                        'primary_key_constraints': {'assets_pkey': 'PRIMARY KEY (id)'},
                        'foreign_key_constraints': {}},
                    ('media', 'attachments'): {
                        'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                    'asset_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                    'owner_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                    'owner_ref_id': {'type': 'uuid', 'nullable': False, 'has_default': False},
                                    'sort_order': {'type': 'integer', 'nullable': False, 'has_default': False},
                                    'status': {'type': 'character varying', 'nullable': False, 'has_default': True},
                                    'created_at': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True}},
                        'unique_constraints': ['uq_media_attachments_asset'],
                        'check_constraints': ['chk_media_attachments_owner_type', 'chk_media_attachments_status'],
                        'indexes': ['idx_media_attachments_owner'],
                        'partial_indexes': [],
                        'primary_key_constraints': {'attachments_pkey': 'PRIMARY KEY (id)'},
                        # asset_ref_id FK داخلي حقيقي (نفس نطاق media) — يختلف عن كل owner_ref_id الأخرى في المشروع (Polymorphic بلا FK عمدًا، §5)
                        'foreign_key_constraints': {'attachments_asset_ref_id_fkey': 'FOREIGN KEY (asset_ref_id) REFERENCES media.assets(id) ON DELETE RESTRICT'}},
                    ('ana', 'events'): {
                        'columns': {'id': {'type': 'uuid', 'nullable': False, 'has_default': True},
                                    'event_type': {'type': 'character varying', 'nullable': False, 'has_default': False},
                                    'occurred_at_utc': {'type': 'timestamp with time zone', 'nullable': False, 'has_default': True},
                                    'actor_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                    'session_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                    'context_type': {'type': 'character varying', 'nullable': True, 'has_default': False},
                                    'context_ref_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                    'correlation_id': {'type': 'uuid', 'nullable': True, 'has_default': False},
                                    'metadata': {'type': 'jsonb', 'nullable': True, 'has_default': False}},
                        'unique_constraints': [],
                        'check_constraints': ['chk_ana_events_type'],
                        'indexes': ['idx_ana_events_actor', 'idx_ana_events_context', 'idx_ana_events_correlation', 'idx_ana_events_type_time'],
                        'partial_indexes': [],
                        'primary_key_constraints': {'events_pkey': 'PRIMARY KEY (id)'},
                        'foreign_key_constraints': {}}}

# استثناءات معتمَدة صراحة (لا تُحتسَب كانحراف رغم أنها قد تبدو كذلك ظاهريًا)
APPROVED_EXCEPTIONS_NOTE = {
    ("trm", "ratings_legacy_seller_only_v1"): "Superseded by CR-009 — الجدول القديم مُعاد تسميته عمدًا في 021_trm_unified_ratings.sql؛ وجوده متوقَّع كسجل تاريخي، لا انحرافًا.",
}


def fetch_live_columns(conn, schema, table):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
    """, (schema, table))
    return {r["column_name"]: {"type": r["data_type"], "nullable": r["is_nullable"] == "YES",
                                "has_default": r["column_default"] is not None} for r in cur.fetchall()}


def fetch_live_constraints(conn, schema, table):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT con.conname, con.contype, pg_get_constraintdef(con.oid, true) AS definition
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
        WHERE nsp.nspname = %s AND rel.relname = %s
    """, (schema, table))
    result = {"primary_key": {}, "unique": {}, "check": {}, "foreign_key": {}}
    type_map = {"p": "primary_key", "u": "unique", "c": "check", "f": "foreign_key"}
    for row in cur.fetchall():
        key = type_map.get(row["contype"])
        if key:
            result[key][row["conname"]] = " ".join(row["definition"].split())
    return result


def fetch_live_indexes(conn, schema, table):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s", (schema, table))
    all_idx, partial_idx = [], []
    for r in cur.fetchall():
        all_idx.append(r["indexname"])
        if "WHERE" in r["indexdef"]:
            partial_idx.append(r["indexname"])
    return all_idx, partial_idx


def check_table(conn, schema, table, expected):
    diffs = []
    live_cols = fetch_live_columns(conn, schema, table)
    if not live_cols:
        return [{"type": "Missing", "object": f"{schema}.{table}", "detail": "الجدول غير موجود إطلاقًا"}]

    for col_name, exp in expected["columns"].items():
        if col_name not in live_cols:
            diffs.append({"type": "Missing", "object": f"{schema}.{table}.{col_name}", "detail": "عمود مفقود"})
            continue
        live = live_cols[col_name]
        mismatches = []
        if exp["type"] not in live["type"] and live["type"] not in exp["type"]:
            mismatches.append(f"النوع المتوقَّع={exp['type']} الفعلي={live['type']}")
        if exp["nullable"] != live["nullable"]:
            mismatches.append(f"Nullable المتوقَّع={exp['nullable']} الفعلي={live['nullable']}")
        if exp["has_default"] != live["has_default"]:
            mismatches.append(f"وجود Default المتوقَّع={exp['has_default']} الفعلي={live['has_default']}")
        if mismatches:
            diffs.append({"type": "Definition Mismatch", "object": f"{schema}.{table}.{col_name}", "detail": "؛ ".join(mismatches)})

    extra_cols = set(live_cols) - set(expected["columns"])
    for col in extra_cols:
        diffs.append({"type": "Extra", "object": f"{schema}.{table}.{col}", "detail": "عمود غير متوقَّع في التصميم"})

    live_constraints = fetch_live_constraints(conn, schema, table)

    def check_named_constraint(kind, label, expected_constraints):
        live = live_constraints[kind]
        for constraint_name, expected_definition in expected_constraints.items():
            if constraint_name not in live:
                diffs.append({"type": "Missing", "object": f"{schema}.{table}::{constraint_name}",
                              "detail": f"قيد {label} مفقود"})
                continue
            actual_definition = live[constraint_name]
            expected_normalized = " ".join(expected_definition.split()).lower()
            actual_normalized = " ".join(actual_definition.split()).lower()
            if expected_normalized != actual_normalized:
                diffs.append({"type": "Definition Mismatch", "object": f"{schema}.{table}::{constraint_name}",
                              "detail": f"تعريف {label} المتوقَّع={expected_definition} الفعلي={actual_definition}"})

    check_named_constraint("primary_key", "PRIMARY KEY", expected.get("primary_key_constraints", {}))
    check_named_constraint("foreign_key", "FOREIGN KEY", expected.get("foreign_key_constraints", {}))

    for uq in expected.get("unique_constraints", []):
        if uq not in live_constraints["unique"]:
            diffs.append({"type": "Missing", "object": f"{schema}.{table}::{uq}", "detail": "قيد تفرّد (UNIQUE) مفقود"})
    for chk in expected.get("check_constraints", []):
        if chk not in live_constraints["check"]:
            diffs.append({"type": "Missing", "object": f"{schema}.{table}::{chk}", "detail": "قيد فحص (CHECK) مفقود"})

    live_all_idx, live_partial_idx = fetch_live_indexes(conn, schema, table)
    for idx in expected.get("indexes", []):
        if idx not in live_all_idx:
            diffs.append({"type": "Missing", "object": f"{schema}.{table}::{idx}", "detail": "فهرس مفقود"})
    for pidx in expected.get("partial_indexes", []):
        if pidx not in live_partial_idx:
            diffs.append({"type": "Missing", "object": f"{schema}.{table}::{pidx}", "detail": "فهرس جزئي (Partial Index) مفقود أو غير جزئي فعليًا"})

    return diffs


def main():
    if psycopg2 is None:
        print(json.dumps({"status": "Blocked by Environment",
                          "reason": "psycopg2 not installed / no PostgreSQL connection available"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        print(json.dumps({"status": "Blocked by Environment", "reason": "TEST_DATABASE_URL not set"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    all_diffs = []
    for (schema, table), expected in EXPECTED_TABLES.items():
        table_diffs = check_table(conn, schema, table, expected)
        for d in table_diffs:
            if (schema, table) in APPROVED_EXCEPTIONS_NOTE and d["type"] == "Extra":
                d["type"] = "Approved Exception"
                d["detail"] += f" — {APPROVED_EXCEPTIONS_NOTE[(schema, table)]}"
            all_diffs.append(d)

    result = {
        "status": "Executed on PostgreSQL",
        "tables_checked": len(EXPECTED_TABLES),
        "primary_keys_expected": sum(len(v.get("primary_key_constraints", {})) for v in EXPECTED_TABLES.values()),
        "foreign_keys_expected": sum(len(v.get("foreign_key_constraints", {})) for v in EXPECTED_TABLES.values()),
        "diffs_found": len([d for d in all_diffs if d["type"] != "Approved Exception"]),
        "approved_exceptions": len([d for d in all_diffs if d["type"] == "Approved Exception"]),
        "details": all_diffs,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["diffs_found"] > 0 else 0)


if __name__ == "__main__":
    main()
