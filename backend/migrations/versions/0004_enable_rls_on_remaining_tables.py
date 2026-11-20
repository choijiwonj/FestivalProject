"""Enable RLS and revoke Supabase Data API roles on the remaining app tables.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

# 0003이 처리한 5개 외 앱 테이블. 앱은 서버에서만 DB에 접속하므로 Data API 역할은 막는다.
TABLES = ('users', 'sessions', 'user_settings', 'festivals', 'festival_programs',
          'places', 'trip_requests', 'jobs', 'trip_plans', 'plan_exports')


def _roles():
    return [role for role in ('anon', 'authenticated')
            if op.get_bind().scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role})]


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        roles = _roles()
        for table in TABLES:
            op.execute(sa.text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
            for role in roles:
                op.execute(sa.text(f'REVOKE ALL ON TABLE {table} FROM {role}'))


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        roles = _roles()
        for table in TABLES:
            for role in roles:
                op.execute(sa.text(f'GRANT ALL ON TABLE {table} TO {role}'))
            op.execute(sa.text(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY'))
