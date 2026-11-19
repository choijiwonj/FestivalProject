from alembic import context

from db import Base, engine

# db.py가 DATABASE_URL을 읽어 만든 engine을 그대로 쓴다(Supabase URL 변환·풀 설정 포함).
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata,
                      render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()
