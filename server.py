"""Chạy giao diện web của RAG Bot và quản lý tài khoản.

    .venv/bin/python server.py                          # mở http://localhost:8000
    .venv/bin/python server.py --port 9000 --host 0.0.0.0
    .venv/bin/python server.py create-user an --admin   # tạo tài khoản (hỏi mật khẩu)
    .venv/bin/python server.py reset-password an        # đặt lại mật khẩu (khi quên)
    .venv/bin/python server.py list-users
"""

import argparse
import getpass
import logging
import sys

from ragbot.auth import Accounts, AuthError, normalize_username
from ragbot.config import get_settings
from ragbot.store import Database


def ask_password() -> str:
    password = getpass.getpass("Mật khẩu: ")
    if password != getpass.getpass("Nhập lại mật khẩu: "):
        sys.exit("Hai lần nhập mật khẩu không khớp")
    return password


def accounts() -> Accounts:
    return Accounts(Database(get_settings().data_dir / "ragbot.db"))


def create_user(args) -> None:
    acc = accounts()
    first = acc.setup_required()
    try:
        user = acc.create(args.username, ask_password(), is_admin=args.admin or first)
    except AuthError as e:
        sys.exit(str(e))
    if first:
        # Tài khoản đầu tiên luôn là admin và nhận các bộ tài liệu tạo trước khi có đăng nhập.
        acc.db.claim_orphan_collections(user.id)
    print(f"Đã tạo tài khoản '{user.username}'{' (admin)' if user.is_admin else ''}")


def reset_password(args) -> None:
    acc = accounts()
    found = acc.db.get_user_with_hash(normalize_username(args.username))
    if not found:
        sys.exit(f"Không có tài khoản '{args.username}'")
    try:
        acc.reset_password(found[0].id, ask_password())
    except AuthError as e:
        sys.exit(str(e))
    print(f"Đã đặt lại mật khẩu cho '{found[0].username}' và đăng xuất mọi phiên của tài khoản này")


def list_users(_args) -> None:
    db = accounts().db
    for u in db.list_users():
        print(f"{u.username:24} {'admin' if u.is_admin else 'user':6} {len(db.list_collections(u.id))} bộ tài liệu")


def serve(args) -> None:
    import uvicorn

    from ragbot.api import create_app

    # Hiện log chuyển model dự phòng (ragbot.providers) ra terminal.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s: %(message)s")
    print(f"RAG Bot: http://{args.host}:{args.port}")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG Bot")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.set_defaults(func=serve)
    sub = parser.add_subparsers(title="lệnh quản lý tài khoản")

    p = sub.add_parser("create-user", help="tạo tài khoản")
    p.add_argument("username")
    p.add_argument("--admin", action="store_true", help="cấp quyền quản lý tài khoản")
    p.set_defaults(func=create_user)

    p = sub.add_parser("reset-password", help="đặt lại mật khẩu")
    p.add_argument("username")
    p.set_defaults(func=reset_password)

    sub.add_parser("list-users", help="liệt kê tài khoản").set_defaults(func=list_users)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
