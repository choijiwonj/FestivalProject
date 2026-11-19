"""기존 create_all DB를 보존하면서 Alembic 관리로 전환한다. 실행할 때만 DB를 변경한다."""
import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from db import Base, engine

BASE_TABLES = {'users', 'sessions', 'user_settings', 'festivals', 'festival_programs',
               'places', 'trip_requests', 'jobs', 'trip_plans', 'plan_exports'}


def migrate(adopt_existing=False):
    config = Config(str(Path(__file__).with_name('alembic.ini')))
    config.set_main_option('script_location', str(Path(__file__).with_name('migrations')))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if 'alembic_version' not in tables and tables:
        if not adopt_existing:
            raise RuntimeError('기존 DB입니다. DB 백업 후 --adopt-existing 옵션으로 실행하세요.')
        if BASE_TABLES - tables:
            raise RuntimeError('기준 테이블이 누락되어 자동 전환을 중단했습니다.')
        for name in BASE_TABLES:
            # 0001의 기존 컬럼은 현재 모델에도 남아 있다.
            required = set(Base.metadata.tables[name].columns.keys())
            actual = {column['name'] for column in inspector.get_columns(name)}
            if required - actual:
                raise RuntimeError(f'{name}의 기준 컬럼이 누락되어 전환을 중단했습니다.')
        # 0002를 일부만 수동 적용한 DB는 먼저 상태를 조사해야 한다.
        if tables & {'posts', 'uploads', 'rate_limits'}:
            raise RuntimeError('추가 테이블이 이미 있습니다. 마이그레이션 이력을 먼저 확인하세요.')
        indexes = {'ix_festivals_start_date', 'ix_festivals_end_date', 'ix_festivals_region',
                   'ix_festival_programs_festival_id', 'ix_places_category'}
        if any(index['name'] in indexes for table in BASE_TABLES for index in inspector.get_indexes(table)):
            raise RuntimeError('추가 인덱스가 이미 있습니다. 마이그레이션 이력을 먼저 확인하세요.')
        command.stamp(config, '0001')
    command.upgrade(config, 'head')
    command.check(config)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adopt-existing', action='store_true')
    args = parser.parse_args()
    try:
        migrate(args.adopt_existing)
    except Exception as error:
        # 접속 문자열이나 SQL 파라미터가 포함될 수 있는 원문 예외는 출력하지 않는다.
        print('DB 전환 실패:', str(error) if isinstance(error, RuntimeError) else type(error).__name__)
        raise SystemExit(1)
    print('DB 마이그레이션 및 모델 일치 검사 완료')
