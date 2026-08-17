from datetime import datetime
from typing import Optional
from bson import ObjectId

VALID_ROLES = {"admin", "instructor", "student"}

def new_user_doc(name: str, email: str, password_hash: str, role: str = "student") -> dict:
    return {
        "name": name,
        "email": email.lower(),
        "passwordHash": password_hash,
        "role": role,
        "status": "active",
        "lastLoginAt": None,
        "createdAt": datetime.utcnow(),
    }

def user_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "email": doc.get("email", ""),
        "role": doc.get("role", "student"),
        "status": doc.get("status", "active"),
    }