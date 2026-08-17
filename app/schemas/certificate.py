from typing import List, Literal, Optional

from pydantic import BaseModel, Field

CertificateTemplate = Literal["classic", "modern", "elegant"]


class IssueCertificateRequest(BaseModel):
    studentId: str
    courseId: str
    template: CertificateTemplate = "classic"
    """Leave blank to use the student's real weighted score from the gradebook."""
    percentageOverride: Optional[float] = Field(None, ge=0, le=100)


class BulkIssueRequest(BaseModel):
    courseId: str
    """Omit to issue for everyone who clears the thresholds."""
    studentIds: List[str] = Field(default_factory=list)
    template: CertificateTemplate = "classic"
    """Re-issue for students who already hold a certificate on this course."""
    overwrite: bool = False


class RevokeCertificateRequest(BaseModel):
    reason: str = Field(default="", max_length=300)