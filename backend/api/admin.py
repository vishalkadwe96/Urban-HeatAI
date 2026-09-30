"""Admin-only, read-only endpoints for the local operations console."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from backend import database as db
from backend.api.auth import _bearer, _user_from_token
from backend.database import get_conn

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def require_admin(authorization: str | None = Header(default=None)):
    row = _user_from_token(_bearer(authorization))
    if not row:
        raise HTTPException(401, "Session expired — please log in again")
    if row["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return row


@router.get("/overview", dependencies=[Depends(require_admin)])
async def overview():
    with get_conn() as conn:
        users = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        sessions = conn.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE expires_at > ?", (int(time.time()),)
        ).fetchone()["count"]
    return {"users": users, "active_sessions": sessions, **db.activity_summary()}


@router.get("/users", dependencies=[Depends(require_admin)])
async def users(limit: int = Query(200, ge=1, le=1000)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, email, role, created_at FROM users ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "users": [
            {**dict(row), "user_id": f"UH-{row['id']:05d}"}
            for row in rows
        ]
    }


@router.get("/activity", dependencies=[Depends(require_admin)])
async def activity(limit: int = Query(50, ge=1, le=500)):
    return {"events": db.list_activity("all", limit)}