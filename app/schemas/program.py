from pydantic import BaseModel, Field
from typing import Optional, List, Literal


ProgramLevel = Literal["certificate", "diploma", "undergraduate", "graduate"]
ProgramStatus = Literal["active", "draft", "archived"]


class CreateProgramRequest(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=5000)
    level: ProgramLevel = "diploma"
    status: ProgramStatus = "draft"
    durationMonths: int = Field(default=12, ge=1, le=60)
    totalCredits: int = Field(default=0, ge=0, le=300)
    coordinator: str = Field(default="", max_length=120)
    company: str = Field(default="", max_length=120)
    courseIds: List[str] = Field(default=[])
    color: str = Field(default="", max_length=20)


class UpdateProgramRequest(BaseModel):
    code: Optional[str] = Field(None, min_length=2, max_length=20)
    title: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=5000)
    level: Optional[ProgramLevel] = None
    status: Optional[ProgramStatus] = None
    durationMonths: Optional[int] = Field(None, ge=1, le=60)
    totalCredits: Optional[int] = Field(None, ge=0, le=300)
    coordinator: Optional[str] = Field(None, max_length=120)
    company: Optional[str] = Field(None, max_length=120)
    courseIds: Optional[List[str]] = None
    color: Optional[str] = Field(None, max_length=20)

# class CreateProgramRequest(BaseModel):
#     code: str = Field(min_length=2, max_length=20)
#     title: str = Field(min_length=2, max_length=120)
#     description: str = Field(default="", max_length=5000)
#     level: ProgramLevel = "diploma"
#     status: ProgramStatus = "draft"
#     durationMonths: int = Field(default=12, ge=1, le=60)
#     totalCredits: int = Field(default=0, ge=0, le=300)
#     coordinator: str = Field(default="", max_length=120)
#     courseIds: List[str] = Field(default=[])
#     color: str = Field(default="", max_length=20)


# class UpdateProgramRequest(BaseModel):
#     code: Optional[str] = Field(None, min_length=2, max_length=20)
#     title: Optional[str] = Field(None, min_length=2, max_length=120)
#     description: Optional[str] = Field(None, max_length=5000)
#     level: Optional[ProgramLevel] = None
#     status: Optional[ProgramStatus] = None
#     durationMonths: Optional[int] = Field(None, ge=1, le=60)
#     totalCredits: Optional[int] = Field(None, ge=0, le=300)
#     coordinator: Optional[str] = Field(None, max_length=120)
#     courseIds: Optional[List[str]] = None
#     color: Optional[str] = Field(None, max_length=20)