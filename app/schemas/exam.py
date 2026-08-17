from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CreateExamRequest(BaseModel):
    courseId: str
    title: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = ""
    totalMarks: int = Field(..., gt=0)
    dueAt: datetime


class UpdateExamRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = None
    totalMarks: Optional[int] = Field(None, gt=0)
    dueAt: Optional[datetime] = None


class GradeExamRequest(BaseModel):
    score: float = Field(..., ge=0)
    feedback: Optional[str] = ""