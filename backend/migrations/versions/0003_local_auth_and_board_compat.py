"""Local password accounts and compatibility with existing public board data.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    # 이전 로컬 배포가 create_all로 만든 두 테이블은 내용을 보존한다.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if 'local_credentials' not in existing:
        op.create_table('local_credentials',
                        sa.Column('user_id', sa.String(40), sa.ForeignKey('users.id'), primary_key=True),
                        sa.Column('password_hash', sa.Text(), nullable=False),
                        sa.Column('created_at', sa.DateTime(), nullable=False))
    if 'board_posts' not in existing:
        op.create_table('board_posts',
                        sa.Column('id', sa.String(40), primary_key=True),
                        sa.Column('festival_id', sa.String(40), sa.ForeignKey('festivals.id'), nullable=True),
                        sa.Column('festival_name', sa.String(200), nullable=False),
                        sa.Column('region', sa.String(20), nullable=False),
                        sa.Column('author_name', sa.String(40), nullable=False),
                        sa.Column('title', sa.String(200), nullable=False),
                        sa.Column('body', sa.Text(), nullable=False),
                        sa.Column('visited_on', sa.Date(), nullable=False),
                        sa.Column('is_sample', sa.Boolean(), nullable=False),
                        sa.Column('created_at', sa.DateTime(), nullable=False))
    with op.batch_alter_table('posts') as batch:
        batch.add_column(sa.Column('title', sa.String(200), nullable=True))
        batch.add_column(sa.Column('visited_on', sa.Date(), nullable=True))
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('local_credentials', 'board_posts', 'posts', 'uploads', 'rate_limits'):
            op.execute(sa.text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
            for role in ('anon', 'authenticated'):
                if op.get_bind().scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
                    op.execute(sa.text(f'REVOKE ALL ON TABLE {table} FROM {role}'))


def downgrade():
    # 이전부터 존재한 로그인 정보와 공개 후기를 보존한다.
    with op.batch_alter_table('posts') as batch:
        batch.drop_column('visited_on')
        batch.drop_column('title')
