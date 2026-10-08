"""baseline：Alembic 接管前的既有结构（create_all 时代的全量 schema）

Revision ID: 0001
Revises:
Create Date: 2026-10-09

本文件是 autogenerate 从 app/models 反射出来的**全量初始结构**（18 张表），即
"引入 Alembic 之前 create_all + 幂等 ALTER 拼出来的那份库"。它有两个用途：
①全新库：`upgrade head` 一次建好（此前靠 create_all）；②既有库：`stamp head` 盖章
接管，不重跑 DDL（见 app/main.py 的 run_migrations）。

两条约束：
- 前置条件是 pgvector 扩展已安装（chunks.embedding 是 VECTOR(1024)）——`ensure_vector_extension`
  必须跑在本迁移之前，否则 `type "vector" does not exist`（真机踩过）。
- 生成物里出现的 pgvector 类型需要显式 import（autogenerate 不会自动补这一行，
  它在离线/在线模式都会在 upgrade 时求值）。**这一条是"在全新库上真跑一次 upgrade"才发现的**：
  只看生成物以为没事，一跑就 NameError。

此后所有结构变更都必须是**新增一条 revision**，不再手写幂等 ALTER。
"""
from alembic import op
import pgvector.sqlalchemy  # noqa: F401  VECTOR 列类型（见文件头说明）
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_by', sa.String(length=255), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('user_email', sa.String(length=255), nullable=True),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('target_type', sa.String(length=32), nullable=True),
    sa.Column('target_id', sa.Integer(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_tenant_id'), 'audit_logs', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_user_email'), 'audit_logs', ['user_email'], unique=False)
    op.create_table('background_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='running', nullable=False),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('done', sa.Integer(), nullable=False),
    sa.Column('failed', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_background_jobs_kind'), 'background_jobs', ['kind'], unique=False)
    op.create_index(op.f('ix_background_jobs_status'), 'background_jobs', ['status'], unique=False)
    op.create_index(op.f('ix_background_jobs_tenant_id'), 'background_jobs', ['tenant_id'], unique=False)
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('user_email', sa.String(length=255), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('kb_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_tenant_id'), 'conversations', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_conversations_user_email'), 'conversations', ['user_email'], unique=False)
    op.create_table('eval_questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('expect_all', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('expect_any', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('cites', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('category', sa.String(length=64), server_default='', nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eval_questions_tenant_id'), 'eval_questions', ['tenant_id'], unique=False)
    op.create_table('eval_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='running', nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('passed', sa.Integer(), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('kb_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('judge', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('chat_model', sa.String(length=128), nullable=True),
    sa.Column('embedding_model', sa.String(length=128), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eval_runs_status'), 'eval_runs', ['status'], unique=False)
    op.create_index(op.f('ix_eval_runs_tenant_id'), 'eval_runs', ['tenant_id'], unique=False)
    op.create_table('knowledge_bases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('embedding_model', sa.String(length=128), nullable=True),
    sa.Column('chunk_target', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_knowledge_bases_tenant_id'), 'knowledge_bases', ['tenant_id'], unique=False)
    op.create_table('licenses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('license_key', sa.String(length=128), nullable=False),
    sa.Column('machine_fingerprint', sa.String(length=255), nullable=True),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('license_key')
    )
    op.create_index(op.f('ix_licenses_tenant_id'), 'licenses', ['tenant_id'], unique=False)
    op.create_table('model_configs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('scenario', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('base_url', sa.Text(), nullable=False),
    sa.Column('encrypted_api_key', sa.Text(), nullable=False),
    sa.Column('model_name', sa.String(length=128), nullable=False),
    sa.Column('capabilities', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('fallback_rank', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_model_configs_scenario'), 'model_configs', ['scenario'], unique=False)
    op.create_index(op.f('ix_model_configs_tenant_id'), 'model_configs', ['tenant_id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('hashed_password', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=128), server_default='', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('daily_token_limit', sa.Integer(), nullable=True),
    sa.Column('monthly_token_limit', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'email')
    )
    op.create_index(op.f('ix_users_tenant_id'), 'users', ['tenant_id'], unique=False)
    op.create_table('api_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('key_prefix', sa.String(length=24), server_default='', nullable=False),
    sa.Column('kb_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('monthly_token_quota', sa.Integer(), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key_hash')
    )
    op.create_index(op.f('ix_api_keys_tenant_id'), 'api_keys', ['tenant_id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('kb_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=512), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('storage_path', sa.String(length=1024), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('mime', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_documents_kb_id'), 'documents', ['kb_id'], unique=False)
    op.create_index(op.f('ix_documents_status'), 'documents', ['status'], unique=False)
    op.create_index(op.f('ix_documents_tenant_id'), 'documents', ['tenant_id'], unique=False)
    op.create_table('eval_item_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('category', sa.String(length=64), server_default='', nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('expect_all', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('expect_any', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('cites', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('cited_docs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('top_docs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('judge', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['question_id'], ['eval_questions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['eval_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eval_item_results_run_id'), 'eval_item_results', ['run_id'], unique=False)
    op.create_table('messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('citations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_messages_tenant_id'), 'messages', ['tenant_id'], unique=False)
    op.create_table('usage_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('user_email', sa.String(length=255), nullable=False),
    sa.Column('kb_id', sa.Integer(), nullable=True),
    sa.Column('scenario', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_usage_records_created_at'), 'usage_records', ['created_at'], unique=False)
    op.create_index(op.f('ix_usage_records_tenant_id'), 'usage_records', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_usage_records_user_email'), 'usage_records', ['user_email'], unique=False)
    op.create_table('user_kb_grants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kb_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'kb_id')
    )
    op.create_index(op.f('ix_user_kb_grants_user_id'), 'user_kb_grants', ['user_id'], unique=False)
    op.create_table('user_sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_index(op.f('ix_user_sessions_expires_at'), 'user_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)
    op.create_table('chunks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), server_default='default', nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('kb_id', sa.Integer(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('acl', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chunks_document_id'), 'chunks', ['document_id'], unique=False)
    op.create_index(op.f('ix_chunks_kb_id'), 'chunks', ['kb_id'], unique=False)
    op.create_index(op.f('ix_chunks_tenant_id'), 'chunks', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_chunks_tenant_id'), table_name='chunks')
    op.drop_index(op.f('ix_chunks_kb_id'), table_name='chunks')
    op.drop_index(op.f('ix_chunks_document_id'), table_name='chunks')
    op.drop_table('chunks')
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_index(op.f('ix_user_sessions_expires_at'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_index(op.f('ix_user_kb_grants_user_id'), table_name='user_kb_grants')
    op.drop_table('user_kb_grants')
    op.drop_index(op.f('ix_usage_records_user_email'), table_name='usage_records')
    op.drop_index(op.f('ix_usage_records_tenant_id'), table_name='usage_records')
    op.drop_index(op.f('ix_usage_records_created_at'), table_name='usage_records')
    op.drop_table('usage_records')
    op.drop_index(op.f('ix_messages_tenant_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_conversation_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index(op.f('ix_eval_item_results_run_id'), table_name='eval_item_results')
    op.drop_table('eval_item_results')
    op.drop_index(op.f('ix_documents_tenant_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_status'), table_name='documents')
    op.drop_index(op.f('ix_documents_kb_id'), table_name='documents')
    op.drop_table('documents')
    op.drop_index(op.f('ix_api_keys_tenant_id'), table_name='api_keys')
    op.drop_table('api_keys')
    op.drop_index(op.f('ix_users_tenant_id'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_model_configs_tenant_id'), table_name='model_configs')
    op.drop_index(op.f('ix_model_configs_scenario'), table_name='model_configs')
    op.drop_table('model_configs')
    op.drop_index(op.f('ix_licenses_tenant_id'), table_name='licenses')
    op.drop_table('licenses')
    op.drop_index(op.f('ix_knowledge_bases_tenant_id'), table_name='knowledge_bases')
    op.drop_table('knowledge_bases')
    op.drop_index(op.f('ix_eval_runs_tenant_id'), table_name='eval_runs')
    op.drop_index(op.f('ix_eval_runs_status'), table_name='eval_runs')
    op.drop_table('eval_runs')
    op.drop_index(op.f('ix_eval_questions_tenant_id'), table_name='eval_questions')
    op.drop_table('eval_questions')
    op.drop_index(op.f('ix_conversations_user_email'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_tenant_id'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_index(op.f('ix_background_jobs_tenant_id'), table_name='background_jobs')
    op.drop_index(op.f('ix_background_jobs_status'), table_name='background_jobs')
    op.drop_index(op.f('ix_background_jobs_kind'), table_name='background_jobs')
    op.drop_table('background_jobs')
    op.drop_index(op.f('ix_audit_logs_user_email'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_tenant_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('app_settings')
