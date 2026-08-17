from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from datetime import datetime


class CreateAssignmentRequest(BaseModel):
    courseId: str
    title: str = Field(min_length=2)
    description: str = ""
    type: Literal["assignment", "quiz"]
    dueAt: datetime
    maxScore: float = 100
    questions: list = []
    allowFileUpload: bool = Field(default=True)  # New field
    maxFileSize: int = Field(default=50)  # MB, new field
    allowedFileTypes: List[str] = Field(
        default=["pdf", "doc", "docx", "zip"]
    )  # New field


class QuestionInput(BaseModel):
    question: str
    options: list[str]
    correctIndex: int


class CreateAssignmentRequest(BaseModel):
    courseId: str
    title: str = Field(min_length=2)
    description: str = ""
    type: Literal["assignment", "quiz"]
    dueAt: datetime
    maxScore: float = 100
    questions: list[QuestionInput] = []


class SubmitAssignmentRequest(BaseModel):
    content: str = ""


class SubmitQuizRequest(BaseModel):
    answers: list[int]


class GradeSubmissionRequest(BaseModel):
    score: float
    feedback: Optional[str] = None
