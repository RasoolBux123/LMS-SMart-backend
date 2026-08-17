from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime


class QuestionInput(BaseModel):
    id: Optional[str] = None
    question: str = Field(min_length=1)
    options: List[str] = Field(min_length=2)
    type: Literal["single", "multiple"] = "single"
    points: float = Field(default=1, ge=0.5)
    correctAnswer: Optional[int] = None  # For single choice
    correctAnswers: Optional[List[int]] = []  # For multiple choice


class CreateQuizRequest(BaseModel):
    courseId: str
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)
    timeLimit: int = Field(default=30, ge=1, le=180)  # minutes
    passingScore: float = Field(default=60, ge=0, le=100)
    attemptsAllowed: int = Field(default=1, ge=1, le=10)
    isPublished: bool = Field(default=True)
    questions: List[QuestionInput] = Field(default=[], min_length=1)


class UpdateQuizRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    timeLimit: Optional[int] = Field(None, ge=1, le=180)
    passingScore: Optional[float] = Field(None, ge=0, le=100)
    attemptsAllowed: Optional[int] = Field(None, ge=1, le=10)
    isPublished: Optional[bool] = None
    questions: Optional[List[QuestionInput]] = None


class SubmitQuizRequest(BaseModel):
    answers: List[
        dict
    ]  # [{"questionId": 0, "selected": 0}] or [{"questionId": 0, "selected": [0, 2]}]


class GradeQuizRequest(BaseModel):
    score: float = Field(ge=0)
    feedback: Optional[str] = None


class QuizListParams(BaseModel):
    courseId: Optional[str] = None
    isPublished: Optional[bool] = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=10, ge=1, le=100)
