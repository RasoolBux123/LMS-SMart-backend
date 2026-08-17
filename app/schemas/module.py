from pydantic import BaseModel, Field
from typing import Optional


class CreateModuleRequest(BaseModel):
    courseId: str
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)
    order: int = Field(default=0, ge=0)
    isPublished: bool = Field(default=True)


class UpdateModuleRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    order: Optional[int] = Field(None, ge=0)
    isPublished: Optional[bool] = None
