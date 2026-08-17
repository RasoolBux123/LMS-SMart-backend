from fastapi import APIRouter, HTTPException, Depends, status
from bson import ObjectId
from datetime import datetime
from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.models.user import new_user_doc, user_to_public, VALID_ROLES
from app.schemas.user import RegisterRequest, LoginRequest, UserPublic
from app.api.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register")
async def register(payload: RegisterRequest):
    db = get_db()
    existing = await db.users.find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    doc = new_user_doc(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id

    token = create_access_token({"sub": str(doc["_id"]), "role": doc["role"]})
    return {
        "success": True,
        "data": {"user": user_to_public(doc), "token": token},
        "message": "registered",
    }

@router.post("/login")
async def login(payload: LoginRequest):
    db = get_db()
    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not verify_password(payload.password, user["passwordHash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail="Account disabled")

    await db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"lastLoginAt": datetime.utcnow()}}
    )
    token = create_access_token({"sub": str(user["_id"]), "role": user["role"]})
    return {
        "success": True,
        "data": {"user": user_to_public(user), "token": token},
        "message": "logged in",
    }

@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"success": True, "data": user_to_public(user), "message": "ok"}