from pydantic import BaseModel, Field
from typing import Optional, List, Literal


class CreateCourseRequest(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=5000)
    category: str = Field(default="general")
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    durationWeeks: int = Field(default=4, ge=1, le=52)
    thumbnail: str = Field(default="")
    objectives: List[str] = Field(default=[])
    prerequisites: List[str] = Field(default=[])
    instructorId: str = Field(default="")
    status: Literal["draft", "published"] = "draft"


class UpdateCourseRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=5000)
    category: Optional[str] = None
    level: Optional[Literal["beginner", "intermediate", "advanced"]] = None
    durationWeeks: Optional[int] = Field(None, ge=1, le=52)
    thumbnail: Optional[str] = None
    objectives: Optional[List[str]] = None
    prerequisites: Optional[List[str]] = None
    instructorId: Optional[str] = None
    status: Optional[Literal["draft", "published"]] = None