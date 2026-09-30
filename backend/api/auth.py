# Urban Heat AI v2 — Auth API (register / login / Google / me / logout)
#
# Users + sessions live in the same SQLite file as the activity log
# (backend/data/urban_heat_ai.db). Passwords are stored as salted PBKDF2
# hashes (stdlib only — no new dependency). Sessions are random tokens;
# only their SHA-256 hash is stored in the DB.

from __future__ import annotations
import hashlib
import hmac
import os
import re
import secrets
import time

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.database import get_conn

router = APIRouter(prefix="/api/auth", tags=["Auth"])

SESSION_TTL_SECONDS = 30 * 24 * 3600   # 30 days
PBKDF2_ROUNDS = 200_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Schemas ──────────────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class GoogleIn(BaseModel):
    credential: str   # ID token from Google Identity Services


# ── Helpers ──────────────────────────────────────────────────────────────────
def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"{salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ROUNDS)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def _public_user(row) -> dict:
    return {
        "id": row["id"],
        "user_id": f"UH-{row['id']:05d}",   # display ID shown on the dashboard
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
    }


def _new_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, now, now + SESSION_TTL_SECONDS),
        )
    return token


def _user_from_token(token: str):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_conn() as conn:
        return conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?""",
            (token_hash, int(time.time())),
        ).fetchone()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not logged in")
    return authorization.split(" ", 1)[1].strip()


# ── Routes ───────────────────────────────────────────────────────────────────
@router.get("/config")
async def auth_config():
    """Tells the login page whether Google sign-in is configured."""
    return {"google_client_id": os.environ.get("GOOGLE_CLIENT_ID", "").strip()}


@router.post("/register")
async def register(body: RegisterIn):
    name = body.name.strip()
    email = body.email.strip().lower()
    if len(name) < 2:
        raise HTTPException(400, "Please enter your name")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists")
        cur = conn.execute(
            "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (name, email, _hash_password(body.password), int(time.time())),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"token": _new_session(row["id"]), "user": _public_user(row)}


@router.post("/login")
async def login(body: LoginIn):
    email = body.email.strip().lower()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    # Same message for "no such user" and "wrong password" (don't leak which emails exist)
    if not row or not row["password_hash"] or not _verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Incorrect email or password")
    return {"token": _new_session(row["id"]), "user": _public_user(row)}


@router.post("/google")
async def google_login(body: GoogleIn):
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(503, "Google sign-in is not configured on this server")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": body.credential})
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach Google to verify sign-in")
    if r.status_code != 200:
        raise HTTPException(401, "Google sign-in could not be verified")

    info = r.json()
    if info.get("aud") != client_id or str(info.get("email_verified")).lower() != "true":
        raise HTTPException(401, "Google sign-in could not be verified")

    email = (info.get("email") or "").lower()
    name = info.get("name") or email.split("@")[0]
    sub = info.get("sub")

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO users (name, email, password_hash, google_sub, created_at) VALUES (?, ?, NULL, ?, ?)",
                (name, email, sub, int(time.time())),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        elif not row["google_sub"]:
            conn.execute("UPDATE users SET google_sub = ? WHERE id = ?", (sub, row["id"]))
    return {"token": _new_session(row["id"]), "user": _public_user(row)}


@router.get("/me")
async def me(authorization: str | None = Header(default=None)):
    row = _user_from_token(_bearer(authorization))
    if not row:
        raise HTTPException(401, "Session expired — please log in again")
    return {"user": _public_user(row)}


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None)):
    token_hash = hashlib.sha256(_bearer(authorization).encode()).hexdigest()
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    return {"ok": True}
