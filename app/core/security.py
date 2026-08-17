from datetime import datetime, timedelta

import bcrypt
from jose import jwt

from app.core.config import settings


def get_password_hash(password: str) -> str:
    # bcrypt only uses the first 72 bytes
    raw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        raw = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict, expires_minutes: int | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    to_encode.update({"exp": expire})
    return jwt.encode(
        to_encode, settings.secret_key, algorithm=settings.effective_algorithm
    )


def decode_access_token(data: str) -> dict:
    return jwt.decode(
        data, settings.secret_key, algorithms=[settings.effective_algorithm]
    )




# from datetime import datetime, timedelta
# from jose import jwt
# from passlib.context import CryptContext
# from app.core.config import settings

# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# def get_password_hash(password: str) -> str:
#     return pwd_context.hash(password)

# def verify_password(plain: str, hashed: str) -> bool:
#     return pwd_context.verify(plain, hashed)

# def create_access_token(data: dict, expires_minutes: int | None = None) -> str:
#     to_encode = data.copy()
#     expire = datetime.utcnow() + timedelta(
#         minutes=expires_minutes or settings.access_token_expire_minutes
#     )
#     to_encode.update({"exp": expire})
#     return jwt.encode(to_encode, settings.secret_key, algorithm=settings.effective_algorithm)

# def decode_access_token(data: str) -> dict:
#     return jwt.decode(data, settings.secret_key, algorithms=[settings.effective_algorithm])