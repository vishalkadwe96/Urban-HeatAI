"""Create the first local admin account and print its one-time credentials."""

from __future__ import annotations

import secrets
import sys
import time

from backend.api.auth import EMAIL_RE, _hash_password
from backend.database import get_conn, init_db

DEFAULT_ADMIN_EMAIL = "admin@urbanheat.local"


def create_admin(email: str = DEFAULT_ADMIN_EMAIL) -> tuple[str, str]:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid admin email address")

    init_db()
    password = secrets.token_urlsafe(18)
    with get_conn() as conn:
        existing = conn.execute("SELECT id, role FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise ValueError("That email already exists; no account or role was changed")
        conn.execute(
            "INSERT INTO users (name, email, password_hash, created_at, role) VALUES (?, ?, ?, ?, 'admin')",
            ("Urban Heat Admin", email, _hash_password(password), int(time.time())),
        )
    return email, password


if __name__ == "__main__":
    try:
        email, password = create_admin(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ADMIN_EMAIL)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("Admin account created. Save this password; it is only shown once.")
    print(f"Admin ID: {email}")
    print(f"Admin password: {password}")