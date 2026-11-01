"""Tài khoản và phiên đăng nhập: băm mật khẩu bằng scrypt, token phiên ngẫu nhiên lưu phía server."""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass

from .store import Database, User

SESSION_COOKIE = "ragbot_session"
SESSION_DAYS = 30
MIN_PASSWORD = 8
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")

# scrypt n=2^14, r=8, p=1: khoảng 16 MB RAM và vài chục ms mỗi lần băm.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


class AuthError(ValueError):
    pass


class TooManyAttempts(AuthError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Băm sẵn một mật khẩu giả: tên đăng nhập không tồn tại vẫn tốn thời gian như sai mật khẩu,
# để không đoán được tài khoản nào có thật qua thời gian phản hồi.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_new_account(username: str, password: str) -> str:
    username = normalize_username(username)
    if not USERNAME_RE.match(username):
        raise AuthError("Tên đăng nhập dài 2–32 ký tự, chỉ gồm chữ không dấu, số, dấu chấm, gạch dưới, gạch ngang")
    validate_password(password)
    return username


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Mật khẩu phải có ít nhất {MIN_PASSWORD} ký tự")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginThrottle:
    """Sai mật khẩu 5 lần liên tiếp thì khóa tạm (1 phút, nhân đôi mỗi lần, tối đa 15 phút)."""

    MAX_FAILURES = 5
    BASE_LOCK_S = 60
    MAX_LOCK_S = 15 * 60

    def __init__(self):
        self._state: dict[str, tuple[int, float, int]] = {}  # key -> (số lần sai, khóa tới lúc, số lần bị khóa)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            _, locked_until, _ = self._state.get(key, (0, 0.0, 0))
        wait = locked_until - time.time()
        if wait > 0:
            raise TooManyAttempts(f"Đăng nhập sai quá nhiều lần. Thử lại sau {int(wait) + 1} giây.")

    def failure(self, key: str) -> None:
        with self._lock:
            failures, locked_until, lockouts = self._state.get(key, (0, 0.0, 0))
            failures += 1
            if failures >= self.MAX_FAILURES:
                locked_until = time.time() + min(self.BASE_LOCK_S * 2**lockouts, self.MAX_LOCK_S)
                failures, lockouts = 0, lockouts + 1
            self._state[key] = (failures, locked_until, lockouts)

    def success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)


@dataclass
class Session:
    token: str
    user: User
    max_age: int


class Accounts:
    def __init__(self, db: Database):
        self.db = db
        self.throttle = LoginThrottle()

    def setup_required(self) -> bool:
        return self.db.count_users() == 0

    def create(self, username: str, password: str, is_admin: bool = False) -> User:
        username = validate_new_account(username, password)
        if self.db.get_user_with_hash(username):
            raise AuthError(f"Tên đăng nhập '{username}' đã có người dùng")
        return self.db.create_user(username, hash_password(password), is_admin)

    def authenticate(self, username: str, password: str, client: str) -> User:
        username = normalize_username(username)
        key = f"{client}|{username}"
        self.throttle.check(key)
        found = self.db.get_user_with_hash(username)
        if not verify_password(password, found[1] if found else _DUMMY_HASH) or not found:
            self.throttle.failure(key)
            raise AuthError("Sai tên đăng nhập hoặc mật khẩu")
        self.throttle.success(key)
        return found[0]

    def start_session(self, user: User) -> Session:
        now = time.time()
        self.db.purge_expired_sessions(now)
        token = secrets.token_urlsafe(32)
        max_age = SESSION_DAYS * 86400
        self.db.create_session(token_hash(token), user.id, now + max_age)
        return Session(token, user, max_age)

    def user_for_token(self, token: str | None) -> User | None:
        return self.db.get_session_user(token_hash(token), time.time()) if token else None

    def end_session(self, token: str | None) -> None:
        if token:
            self.db.delete_session(token_hash(token))

    def change_password(self, user: User, current: str, new: str, keep_token: str | None) -> None:
        found = self.db.get_user_with_hash(user.username)
        if not found or not verify_password(current, found[1]):
            raise AuthError("Mật khẩu hiện tại không đúng")
        self.reset_password(user.id, new, keep_token)

    def reset_password(self, user_id: int, new: str, keep_token: str | None = None) -> None:
        """Đặt mật khẩu mới và đăng xuất mọi phiên khác của tài khoản đó."""
        validate_password(new)
        self.db.set_password_hash(user_id, hash_password(new))
        self.db.delete_user_sessions(user_id, keep=token_hash(keep_token) if keep_token else None)
