"""GitHub 추가 기능과 기존 화면·아이디 로그인 사이의 통합 검증."""
import io
import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from core import now
from db import BoardPost, LocalCredential, SessionLocal, User
from main import app
from password_auth import create_local_account
from test_user_features import user_client


@pytest.fixture(autouse=True)
def setup_app(monkeypatch):
    for key in ('ODSAY_API_KEY', 'GEMINI_API_KEY', 'KAKAO_MOBILITY_REST_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(app, base_url='https://testserver'):
        yield


def test_board_combines_existing_content_and_member_posts():
    client = user_client()
    with SessionLocal() as db:
        legacy = BoardPost(id='legacy_' + uuid.uuid4().hex[:10], festival_id='fst_demo_01',
                           festival_name='기존 축제', region='TAEAN', author_name='여행자',
                           title='기존 후기', body='기존 후기 본문', visited_on=date(2026, 5, 1),
                           is_sample=True, created_at=now())
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    result = client.post('/api/v1/posts', json={'festivalId': 'fst_demo_01', 'body': '즐거웠던 여행',
                         'title': '직접 작성한 후기', 'visitedOn': '2026-05-03'})
    assert result.status_code == 201, result.text
    post = result.json()['data']
    assert post['title'] == '직접 작성한 후기' and post['visitedOn'] == '2026-05-03'
    assert post['region'] and post['festivalName'] and post['excerpt']
    assert client.get('/api/v1/posts/' + legacy_id).json()['data']['title'] == '기존 후기'
    rows = client.get('/api/v1/posts', params={'festivalId': 'fst_demo_01'}).json()['data']
    assert {post['id'], legacy_id} <= {row['id'] for row in rows}
    for row in rows:
        assert isinstance(row['region'], str) and row['title'] and row['excerpt']
    assert all(row['region'] == 'TAEAN' for row in client.get('/api/v1/posts?region=TAEAN').json()['data'])
    assert client.post('/api/v1/posts', json={'festivalId': 'fst_demo_01', 'body': '본문', 'title': '   '}).status_code == 400


def test_local_account_deletion_removes_credentials_with_foreign_keys():
    with TestClient(app, base_url='https://testserver') as client:
        username = 'merge_' + uuid.uuid4().hex[:10]
        with SessionLocal() as db:
            user = create_local_account(db, username, 'merge-test-password!', '검증 계정')
            user_id = user.id
        login = client.post('/api/v1/auth/login', json={'username': username, 'password': 'merge-test-password!'})
        assert login.status_code == 200, login.text
        # PostgreSQL처럼 실제 FK 검사가 적용되도록 이 테스트의 모든 연결에서 활성화한다.
        from sqlalchemy import event
        from db import engine
        def enable_fk(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        event.listen(engine, 'connect', enable_fk)
        engine.dispose()
        try:
            response = client.delete('/api/v1/me', headers={'X-CSRF-Token': login.json()['data']['csrfToken']})
            assert response.status_code == 204, response.text
            with SessionLocal() as db:
                assert db.get(LocalCredential, user_id) is None and db.get(User, user_id) is None
        finally:
            event.remove(engine, 'connect', enable_fk)
            engine.dispose()


def test_reencoded_upload_stays_under_response_limit():
    client = user_client()
    raw = io.BytesIO()
    Image.effect_noise((3200, 3200), 90).convert('RGB').save(raw, 'JPEG', quality=5)
    response = client.post('/api/v1/uploads', files={'file': ('photo.jpg', raw.getvalue(), 'image/jpeg')})
    assert response.status_code == 201, response.text
    saved = response.json()['data']
    assert saved['size'] <= 4 * 1024 * 1024
    download = client.get(saved['url'])
    assert download.status_code == 200 and len(download.content) == saved['size']


def test_adopt_existing_db_preserves_local_content(tmp_path):
    # 재현용 DB는 별도 프로세스에서 만들고 실제 DB에는 연결하지 않는다.
    location = tmp_path / 'existing.db'
    env = {**os.environ, 'DATABASE_URL': 'sqlite:///' + location.as_posix(), 'PYTHONUTF8': '1'}
    cwd = Path(__file__).parent
    cli = [sys.executable, '-c', 'from alembic.config import main; main()']
    setup = subprocess.run([*cli, 'upgrade', '0001'], cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8')
    assert setup.returncode == 0, setup.stderr
    # create_all로 운영하던 DB처럼 이력 없이 기준 테이블과 로컬 테이블이 존재하는 상태.
    import sqlite3
    with sqlite3.connect(location) as connection:
        connection.execute('DROP TABLE alembic_version')
    script = """
from db import LocalCredential, BoardPost, engine
LocalCredential.__table__.create(engine)
BoardPost.__table__.create(engine)
from sqlalchemy import text
with engine.begin() as c:
 c.execute(text("INSERT INTO users(id,provider,provider_user_id,nickname,status,created_at) VALUES ('u_keep','local','keep','보존','ACTIVE',CURRENT_TIMESTAMP)"))
 c.execute(text("INSERT INTO local_credentials(user_id,password_hash,created_at) VALUES ('u_keep','hash-to-preserve',CURRENT_TIMESTAMP)"))
"""
    created = subprocess.run([sys.executable, '-c', script], cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8')
    assert created.returncode == 0, created.stderr
    migrated = subprocess.run([sys.executable, 'migrate.py', '--adopt-existing'], cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8')
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    with sqlite3.connect(location) as connection:
        assert connection.execute('SELECT password_hash FROM local_credentials').fetchone()[0] == 'hash-to-preserve'
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '0004'
