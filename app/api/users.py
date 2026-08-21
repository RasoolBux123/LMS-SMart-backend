from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, Field

from app.core.database import database
from app.core.security import get_password_hash
from app.api.deps import require_roles
from app.models.user import new_user_doc, user_to_public
from bson import ObjectId
router = APIRouter(prefix="/users", tags=["users"])


class CreateUserRequest(BaseModel):
    name: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=6)
    role: Literal["instructor", "student", "admin"]


@router.get("")
async def list_users(
    role: Optional[str] = Query(None),
    user=Depends(require_roles("admin", "instructor")),
):
    db = database.db
    # Instructors may only list students (for enrollment)
    if user["role"] == "instructor":
        query = {"role": "student"}
    else:
        query = (
            {"role": role}
            if role in ("instructor", "student", "admin")
            else {"role": {"$in": ["instructor", "student", "admin"]}}
        )
    cursor = db.users.find(query).sort("createdAt", -1)
    users = [user_to_public(u) async for u in cursor]
    return {"success": True, "data": users, "message": "ok"}


@router.post("")
async def create_user(
    payload: CreateUserRequest,
    admin=Depends(require_roles("admin")),
):
    db = database.db
    existing = await db.users.find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    doc = new_user_doc(
        name=payload.name,
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        role=payload.role,
    )
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Credentials vault — fail hone pe bhi user create rahe
    try:
        from app.api.credentials import save_credential

        await save_credential(
            user_id=str(result.inserted_id),
            name=payload.name,
            email=payload.email,
            password=payload.password,
            role=payload.role,
        )
    except Exception as exc:
        print(f"[credentials] save failed (user still created): {exc}")

    # Notify new instructor that their account is ready
    if payload.role == "instructor":
        try:
            from app.api.notifications import notify_user

            await notify_user(
                str(result.inserted_id),
                title="Welcome to SmartLMS",
                body=f"Admin created your instructor account. You can log in with {payload.email}.",
                kind="system",
                link="/instructor/courses",
            )
        except Exception as exc:
            print(f"[notifications] instructor welcome failed: {exc}")

    return {
        "success": True,
        "data": user_to_public(doc),
        "message": "user created",
    }



@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    admin=Depends(require_roles("admin")),
):
    db = database.db

    # Validate MongoDB ObjectId
    try:
        object_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid user ID"
        )

    # Find user
    user = await db.users.find_one({"_id": object_id})

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # Delete user
    result = await db.users.delete_one({"_id": object_id})

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail="User could not be deleted"
        )

    return {
        "success": True,
        "data": None,
        "message": "User deleted successfully"
    }



# from fastapi import APIRouter, Depends, HTTPException, Query
# from typing import Optional, Literal
# from pydantic import BaseModel, EmailStr, Field

# from app.core.database import database
# from app.core.security import get_password_hash
# from app.api.deps import require_roles
# from app.models.user import new_user_doc, user_to_public

# router = APIRouter(prefix="/users", tags=["users"])


# class CreateUserRequest(BaseModel):
#     name: str = Field(min_length=2)
#     email: EmailStr
#     password: str = Field(min_length=6)
#     role: Literal["instructor", "student", "admin"]


# @router.get("")
# async def list_users(
#     role: Optional[str] = Query(None),
#     user=Depends(require_roles("admin", "instructor")),
# ):
#     db = database.db
#     # Instructors may only list students (for enrollment)
#     if user["role"] == "instructor":
#         query = {"role": "student"}
#     else:
#         query = (
#             {"role": role}
#             if role in ("instructor", "student", "admin")
#             else {"role": {"$in": ["instructor", "student", "admin"]}}
#         )
#     cursor = db.users.find(query).sort("createdAt", -1)
#     users = [user_to_public(u) async for u in cursor]
#     return {"success": True, "data": users, "message": "ok"}


# @router.post("")
# async def create_user(
#     payload: CreateUserRequest,
#     admin=Depends(require_roles("admin")),
# ):
#     db = database.db
#     existing = await db.users.find_one({"email": payload.email.lower()})
#     if existing:
#         raise HTTPException(status_code=400, detail="Email already registered")

#     doc = new_user_doc(
#         name=payload.name,
#         email=payload.email,
#         password_hash=get_password_hash(payload.password),
#         role=payload.role,
#     )
#     result = await db.users.insert_one(doc)
#     doc["_id"] = result.inserted_id

#     # Credentials vault — fail hone pe bhi user create rahe
#     try:
#         from app.api.credentials import save_credential

#         await save_credential(
#             user_id=str(result.inserted_id),
#             name=payload.name,
#             email=payload.email,
#             password=payload.password,
#             role=payload.role,
#         )
        
#     except Exception as exc:
#         print(f"[credentials] save failed (user still created): {exc}")

#     return {
#         "success": True,
#         "data": user_to_public(doc),
#         "message": "user created",
#     }




