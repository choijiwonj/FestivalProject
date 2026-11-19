"""Alembic 마이그레이션을 빈 DB에 적용하면 db.py 모델과 스키마가 같아야 한다(모델만 바꾸고 마이그레이션을 빠뜨리면 실패)."""
import os
import subprocess
import sys
from pathlib import Path


def test_migrations_match_models(tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{(tmp_path / 'm.db').as_posix()}", "PYTHONUTF8": "1"}
    run = lambda *args: subprocess.run([sys.executable, "-m", "alembic", *args], cwd=Path(__file__).parent,
                                       env=env, capture_output=True, text=True)
    upgrade = run("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    check = run("check")
    assert check.returncode == 0, check.stdout + check.stderr
