from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    Form,
)
from fastapi.responses import FileResponse
from bson import ObjectId
from datetime import datetime
import os
import shutil

from app.core.database import database
from app.api.deps import get_current_user, require_roles

from app.models.exam import (
    new_exam_doc,
    exam_to_public,
)

from app.models.exam_submission import (
    new_exam_submission_doc,
    exam_submission_to_public,
)

from app.schemas.exam import (
    UpdateExamRequest,
    GradeExamRequest,
)

router = APIRouter(
    prefix="/exams",
    tags=["Exams"],
)

UPLOAD_DIR = "uploads/exams"
ANSWER_DIR = "uploads/submissions"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(ANSWER_DIR, exist_ok=True)


# ======================================================
# Create Exam
# ======================================================

@router.post("")
async def create_exam(
    courseId: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    totalMarks: int = Form(...),
    dueAt: str = Form(...),
    exam_file: UploadFile = File(...),
    user: dict = Depends(require_roles("instructor", "admin")),
):

    db = database.db

    course = await db.courses.find_one(
        {"_id": ObjectId(courseId)}
    )

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    ext = exam_file.filename.split(".")[-1].lower()

    if ext not in ["pdf", "doc", "docx"]:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, DOC and DOCX allowed",
        )

    filename = f"{datetime.utcnow().timestamp()}_{exam_file.filename}"

    filepath = os.path.join(
        UPLOAD_DIR,
        filename,
    )

    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(
            exam_file.file,
            buffer,
        )

    try:
        due_date = datetime.fromisoformat(dueAt)
    except ValueError:
        due_date = datetime.strptime(dueAt, "%d-%m-%Y")

    doc = new_exam_doc(
    course_id=courseId,
    title=title,
    description=description,
    exam_file=filepath,
    total_marks=totalMarks,
    due_at=due_date,
    created_by=str(user["_id"]),
)

    result = await db.exams.insert_one(doc)

    doc["_id"] = result.inserted_id

    return {
        "success": True,
        "data": exam_to_public(doc),
        "message": "Exam created successfully",
    }


# ======================================================
# List Exams
# ======================================================

@router.get("")
async def list_exams(
    user: dict = Depends(get_current_user),
):

    db = database.db

    if user["role"] == "student":

        enrollments = await db.enrollments.find(
            {
                "userId": str(user["_id"])
            }
        ).to_list(length=None)

        course_ids = [
            e["courseId"]
            for e in enrollments
        ]

        cursor = db.exams.find(
            {
                "courseId": {
                    "$in": course_ids
                }
            }
        )

    elif user["role"] == "instructor":

        cursor = db.exams.find(
            {
                "createdBy": str(user["_id"])
            }
        )

    else:

        cursor = db.exams.find()

    exams = [
        exam_to_public(doc)
        async for doc in cursor
    ]

    return {
        "success": True,
        "data": exams,
        "message": "ok",
    }
    
# ======================================================
# Get Single Exam
# ======================================================

@router.get("/{exam_id}")
async def get_exam(
    exam_id: str,
    user: dict = Depends(get_current_user),
):
    db = database.db

    exam = await db.exams.find_one(
        {"_id": ObjectId(exam_id)}
    )

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found",
        )

    return {
        "success": True,
        "data": exam_to_public(exam),
        "message": "ok",
    }


# ======================================================
# Update Exam
# ======================================================

@router.patch("/{exam_id}")
async def update_exam(
    exam_id: str,
    payload: UpdateExamRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db

    exam = await db.exams.find_one(
        {"_id": ObjectId(exam_id)}
    )

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found",
        )

    if (
        user["role"] == "instructor"
        and exam["createdBy"] != str(user["_id"])
    ):
        raise HTTPException(
            status_code=403,
            detail="Not authorized",
        )

    update_data = payload.dict(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="Nothing to update",
        )

    update_data["updatedAt"] = datetime.utcnow()

    await db.exams.update_one(
        {"_id": ObjectId(exam_id)},
        {"$set": update_data},
    )

    updated = await db.exams.find_one(
        {"_id": ObjectId(exam_id)}
    )

    return {
        "success": True,
        "data": exam_to_public(updated),
        "message": "Exam updated successfully",
    }


# ======================================================
# Delete Exam
# ======================================================

@router.delete("/{exam_id}")
async def delete_exam(
    exam_id: str,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db

    exam = await db.exams.find_one(
        {"_id": ObjectId(exam_id)}
    )

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found",
        )

    if (
        user["role"] == "instructor"
        and exam["createdBy"] != str(user["_id"])
    ):
        raise HTTPException(
            status_code=403,
            detail="Not authorized",
        )

    if os.path.exists(exam["examFile"]):
        os.remove(exam["examFile"])

    await db.exams.delete_one(
        {"_id": ObjectId(exam_id)}
    )

    return {
        "success": True,
        "message": "Exam deleted successfully",
    }
# ======================================================
# Download Exam Paper
# ======================================================

@router.get("/{exam_id}/download")
async def download_exam(
    exam_id: str,
    user: dict = Depends(get_current_user),
):
    db = database.db

    exam = await db.exams.find_one(
        {"_id": ObjectId(exam_id)}
    )

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found",
        )

    if not os.path.exists(exam["examFile"]):
        raise HTTPException(
            status_code=404,
            detail="Exam file not found",
        )

    return FileResponse(
        path=exam["examFile"],
        filename=os.path.basename(exam["examFile"]),
        media_type="application/octet-stream",
    )


# ======================================================
# Student Submit Exam
# ======================================================

@router.post("/{exam_id}/submit")
async def submit_exam(
    exam_id: str,
    answer_file: UploadFile = File(...),
    user: dict = Depends(require_roles("student")),
):
    db = database.db

    exam = await db.exams.find_one(
        {"_id": ObjectId(exam_id)}
    )

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found",
        )

    existing = await db.exam_submissions.find_one(
        {
            "examId": exam_id,
            "studentId": str(user["_id"]),
        }
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="You have already submitted this exam.",
        )

    ext = answer_file.filename.split(".")[-1].lower()

    if ext not in ["pdf", "doc", "docx"]:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, DOC and DOCX files are allowed.",
        )

    filename = (
        f"{datetime.utcnow().timestamp()}_{answer_file.filename}"
    )

    filepath = os.path.join(
        ANSWER_DIR,
        filename,
    )

    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(
            answer_file.file,
            buffer,
        )

    submission = new_exam_submission_doc(
        exam_id=exam_id,
        student_id=str(user["_id"]),
        answer_file=filepath,
    )

    result = await db.exam_submissions.insert_one(
        submission
    )

    submission["_id"] = result.inserted_id

    return {
        "success": True,
        "data": exam_submission_to_public(submission),
        "message": "Exam submitted successfully.",
    }
    
# ======================================================
# Instructor/Admin - View Exam Submissions
# ======================================================

@router.get("/{exam_id}/submissions")
async def list_exam_submissions(
    exam_id: str,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db

    exam = await db.exams.find_one(
        {"_id": ObjectId(exam_id)}
    )

    if not exam:
        raise HTTPException(
            status_code=404,
            detail="Exam not found",
        )

    if (
        user["role"] == "instructor"
        and exam["createdBy"] != str(user["_id"])
    ):
        raise HTTPException(
            status_code=403,
            detail="Not authorized",
        )

    cursor = db.exam_submissions.find(
        {
            "examId": exam_id,
        }
    )

    submissions = [
        exam_submission_to_public(doc)
        async for doc in cursor
    ]

    return {
        "success": True,
        "data": submissions,
        "message": "ok",
    }


# ======================================================
# Instructor/Admin - Grade Exam Submission
# ======================================================

@router.patch("/submissions/{submission_id}/grade")
async def grade_exam_submission(
    submission_id: str,
    payload: GradeExamRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db

    submission = await db.exam_submissions.find_one(
        {
            "_id": ObjectId(submission_id)
        }
    )

    if not submission:
        raise HTTPException(
            status_code=404,
            detail="Submission not found",
        )

    await db.exam_submissions.update_one(
        {
            "_id": ObjectId(submission_id)
        },
        {
            "$set": {
                "score": payload.score,
                "feedback": payload.feedback,
                "gradedBy": str(user["_id"]),
                "gradedAt": datetime.utcnow(),
            }
        },
    )

    updated = await db.exam_submissions.find_one(
        {
            "_id": ObjectId(submission_id)
        }
    )

    return {
        "success": True,
        "data": exam_submission_to_public(updated),
        "message": "Exam graded successfully",
    }
