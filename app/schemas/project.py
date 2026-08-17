from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class CreateProjectRequest(BaseModel):
    courseId: str
    title: str = Field(min_length=2)
    description: str = ""
    dueAt: datetime
    maxScore: float = 100
    attachmentUrl: Optional[str] = None

class SubmitProjectRequest(BaseModel):
    content: str = ""

class GradeProjectSubmissionRequest(BaseModel):
    score: float
    feedback: Optional[str] = None