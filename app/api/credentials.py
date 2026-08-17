"""
Admin-only credentials vault.

When an admin creates (or resets) an instructor/student account, the
plaintext password is stored here so the admin can share login details.
Login still uses the hashed password on the users collection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_roles
from app.core.database import database

router = APIRouter(prefix="/credentials", tags=["credentials"])


def credential_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "userId": doc.get("userId", ""),
        "name": doc.get("name", ""),
        "email": doc.get("email", ""),
        "password": doc.get("password", ""),
        "role": doc.get("role", "student"),
        "createdAt": (
            doc["createdAt"].isoformat()
            if hasattr(doc.get("createdAt"), "isoformat")
            else str(doc.get("createdAt") or "")
        ),
        "updatedAt": (
            doc["updatedAt"].isoformat()
            if hasattr(doc.get("updatedAt"), "isoformat")
            else str(doc.get("updatedAt") or "")
        ),
    }


async def save_credential(
    *,
    user_id: str,
    name: str,
    email: str,
    password: str,
    role: str,
) -> None:
    """Upsert plaintext credential for admin viewing."""
    db = database.db
    now = datetime.utcnow()
    await db.credentials.update_one(
        {"userId": str(user_id)},
        {
            "$set": {
                "userId": str(user_id),
                "name": name,
                "email": email.lower(),
                "password": password,
                "role": role,
                "updatedAt": now,
            },
            "$setOnInsert": {"createdAt": now},
        },
        upsert=True,
    )


@router.get("")
async def list_credentials(
    role: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    admin=Depends(require_roles("admin")),
):
    db = database.db
    query: dict = {}
    if role in ("instructor", "student"):
        query["role"] = role
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
        ]

    cursor = db.credentials.find(query).sort("updatedAt", -1)
    rows = [credential_to_public(doc) async for doc in cursor]
    return {"success": True, "data": rows, "message": "ok"}


@router.delete("/{credential_id}")
async def delete_credential(
    credential_id: str,
    admin=Depends(require_roles("admin")),
):
    db = database.db
    try:
        oid = ObjectId(credential_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")

    result = await db.credentials.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"success": True, "data": None, "message": "deleted"}