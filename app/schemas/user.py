from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal

class RegisterRequest(BaseModel):
    name: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=6)
    role: Literal["admin", "instructor", "student"] = "student"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserPublic(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: str
    status: str

class TokenResponse(BaseModel):
    success: bool = True
    data: dict
    message: str = "ok"

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


