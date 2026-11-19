"""Create one account. Never overwrite an existing ID or store a plaintext password."""
import argparse
import getpass
from password_auth import create_local_account
from db import LocalCredential, SessionLocal, engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--nickname", default="여행자")
    args = parser.parse_args()
    password = getpass.getpass("새 계정 비밀번호(8~128자): ")
    if password != getpass.getpass("비밀번호 확인: "):
        raise SystemExit("비밀번호가 일치하지 않습니다.")
    LocalCredential.__table__.create(engine, checkfirst=True)
    with SessionLocal() as db:
        create_local_account(db, args.username, password, args.nickname)
    print("계정을 생성했습니다. 비밀번호는 저장하거나 출력하지 않았습니다.")


if __name__ == "__main__":
    main()
