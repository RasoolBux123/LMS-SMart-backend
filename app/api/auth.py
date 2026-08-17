from fastapi import APIRouter, HTTPException, Depends, status
from datetime import datetime
from app.core.database import database
from app.core.security import verify_password, create_access_token, get_password_hash
from app.models.user import user_to_public
from app.schemas.user import LoginRequest, ChangePasswordRequest
from app.api.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login")
async def login(payload: LoginRequest):
    db = database.db
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
        "data": {"user": user_to_public(user), "access_token": token},
        "message": "logged in",
    }

@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"success": True, "data": user_to_public(user), "message": "ok"}


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
):
    """
    Authenticated user changes their own password.
    Also updates the admin credentials vault so the new password is visible there.
    """
    db = database.db

    # Verify current password
    if not verify_password(payload.current_password, user["passwordHash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password",
        )

    # Update hashed password on users collection
    new_hash = get_password_hash(payload.new_password)
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"passwordHash": new_hash}},
    )

    # Keep admin credentials vault in sync (plaintext for admin viewing)
    try:
        from app.api.credentials import save_credential

        await save_credential(
            user_id=str(user["_id"]),
            name=user.get("name", ""),
            email=user.get("email", ""),
            password=payload.new_password,
            role=user.get("role", "student"),
        )
    except Exception as exc:
        print(f"[credentials] update after password change failed: {exc}")

    return {
        "success": True,
        "data": None,
        "message": "Password changed successfully",
    }




# from fastapi import APIRouter, HTTPException, Depends, status
# from datetime import datetime
# from app.core.database import database
# from app.core.security import verify_password, create_access_token, get_password_hash
# from app.models.user import user_to_public
# from app.schemas.user import LoginRequest
# from app.api.deps import get_current_user

# router = APIRouter(prefix="/auth", tags=["auth"])

# @router.post("/login")
# async def login(payload: LoginRequest):
#     db = database.db
#     user = await db.users.find_one({"email": payload.email.lower()})
#     if not user or not verify_password(payload.password, user["passwordHash"]):
#         raise HTTPException(status_code=401, detail="Invalid email or password")
#     if user.get("status") != "active":
#         raise HTTPException(status_code=403, detail="Account disabled")

#     await db.users.update_one(
#         {"_id": user["_id"]}, {"$set": {"lastLoginAt": datetime.utcnow()}}
#     )
#     token = create_access_token({"sub": str(user["_id"]), "role": user["role"]})
#     return {
#         "success": True,
#         "data": {"user": user_to_public(user), "access_token": token},
#         # "data": {"user": user_to_public(user), "token": token},
#         "message": "logged in",
#     }

# @router.get("/me")
# async def me(user: dict = Depends(get_current_user)):
#     return {"success": True, "data": user_to_public(user), "message": "ok"}