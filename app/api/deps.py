# from fastapi import Depends, HTTPException, status
# from fastapi.security import OAuth2PasswordBearer
# from bson import ObjectId
# from jose import JWTError
# from app.core.security import decode_access_token
# from app.core.database import database

# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
#     credentials_error = HTTPException(
#         status_code=status.HTTP_401_UNAUTHORIZED,
#         detail="Could not validate credentials",
#     )
#     try:
#         payload = decode_access_token(token)
#         user_id = payload.get("sub")
#         if not user_id:
#             raise credentials_error
#     except JWTError:
#         raise credentials_error

#     db = database.db
#     user = await db.users.find_one({"_id": ObjectId(user_id)})
#     if not user or user.get("status") != "active":
#         raise credentials_error
#     return user

# def require_roles(*allowed_roles: str):
#     async def role_checker(user: dict = Depends(get_current_user)) -> dict:
#         if user["role"] not in allowed_roles:
#             raise HTTPException(
#                 status_code=status.HTTP_403_FORBIDDEN,
#                 detail="Not authorized for this action",
#             )
#         return user
#     return role_checker


from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from bson import ObjectId
from jose import JWTError

from app.core.security import decode_access_token
from app.core.database import database

# Bearer Token Authentication
bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:

    token = credentials.credentials

    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
    )

    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")

        if not user_id:
            raise credentials_error

    except JWTError:
        raise credentials_error

    db = database.db

    user = await db.users.find_one(
        {
            "_id": ObjectId(user_id)
        }
    )

    if not user:
        raise credentials_error

    if user.get("status") != "active":
        raise HTTPException(
            status_code=403,
            detail="Account disabled",
        )

    return user


def require_roles(*allowed_roles: str):
    async def role_checker(
        user: dict = Depends(get_current_user),
    ):

        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized",
            )

        return user

    return role_checker