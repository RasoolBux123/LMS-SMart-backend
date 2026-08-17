from pydantic import BaseModel, Field
from typing import Optional, Literal


class CreateMaterialRequest(BaseModel):
    moduleId: str
    title: str = Field(min_length=2, max_length=200)
    type: Literal["video", "pdf", "link", "text", "image", "audio"]
    url: str = Field(default="", max_length=1000)
    content: str = Field(default="", max_length=10000)
    duration: int = Field(default=0, ge=0)
    fileSize: int = Field(default=0, ge=0)
    order: int = Field(default=0, ge=0)
    isRequired: bool = Field(default=True)


class UpdateMaterialRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=200)
    type: Optional[Literal["video", "pdf", "link", "text", "image", "audio"]] = None
    url: Optional[str] = Field(None, max_length=1000)
    content: Optional[str] = Field(None, max_length=10000)
    duration: Optional[int] = Field(None, ge=0)
    fileSize: Optional[int] = Field(None, ge=0)
    order: Optional[int] = Field(None, ge=0)
    isRequired: Optional[bool] = None
