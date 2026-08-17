from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from datetime import datetime
from app.core.database import database
from app.api.deps import get_current_user, require_roles
from app.models.assignment import new_assignment_doc, assignment_to_public
from app.models.submission import new_submission_doc, submission_to_public
from app.schemas.assignment import (
    CreateAssignmentRequest,
    SubmitAssignmentRequest,
    SubmitQuizRequest,
    GradeSubmissionRequest,
)

router = APIRouter(prefix="/assignments", tags=["assignments"])


@router.post("")
async def create_assignment(
    payload: CreateAssignmentRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    questions = [q.dict() for q in payload.questions] if payload.type == "quiz" else []
    doc = new_assignment_doc(
        course_id=payload.courseId,
        title=payload.title,
        description=payload.description,
        due_at=payload.dueAt,
        max_score=payload.maxScore,
        assignment_type=payload.type,
        questions=questions,
    )
    result = await db.assignments.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {
        "success": True,
        "data": assignment_to_public(doc, include_answers=True),
        "message": "assignment created",
    }


@router.get("/course/{course_id}")
async def list_assignments(course_id: str, user: dict = Depends(get_current_user)):
    db = database.db
    cursor = db.assignments.find({"courseId": course_id}).sort("dueAt", 1)
    include_answers = user["role"] in ("instructor", "admin")
    assignments = [assignment_to_public(a, include_answers) async for a in cursor]
    return {"success": True, "data": assignments, "message": "ok"}


@router.post("/{assignment_id}/submit/assignment")
async def submit_assignment(
    assignment_id: str,
    payload: SubmitAssignmentRequest,
    user: dict = Depends(require_roles("student")),
):
    db = database.db
    assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    existing = await db.submissions.find_one(
        {"assignmentId": assignment_id, "studentId": str(user["_id"])}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Already submitted")
    doc = new_submission_doc(assignment_id, str(user["_id"]), content=payload.content)
    result = await db.submissions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "data": submission_to_public(doc), "message": "submitted"}


@router.post("/{assignment_id}/submit/quiz")
async def submit_quiz(
    assignment_id: str,
    payload: SubmitQuizRequest,
    user: dict = Depends(require_roles("student")),
):
    db = database.db
    assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
    if not assignment or assignment["type"] != "quiz":
        raise HTTPException(status_code=404, detail="Quiz not found")
    existing = await db.submissions.find_one(
        {"assignmentId": assignment_id, "studentId": str(user["_id"])}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Already submitted")
    questions = assignment.get("questions", [])
    correct = sum(
        1
        for i, q in enumerate(questions)
        if i < len(payload.answers) and payload.answers[i] == q["correctIndex"]
    )
    score = (correct / len(questions)) * assignment["maxScore"] if questions else 0
    doc = new_submission_doc(assignment_id, str(user["_id"]), answers=payload.answers)
    doc["score"] = round(score, 2)
    doc["gradedAt"] = datetime.utcnow()
    doc["gradedBy"] = "auto"
    result = await db.submissions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {
        "success": True,
        "data": submission_to_public(doc),
        "message": "quiz auto-graded",
    }


@router.get("/{assignment_id}/submissions")
async def list_submissions(
    assignment_id: str, user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db
    cursor = db.submissions.find({"assignmentId": assignment_id})
    submissions = [submission_to_public(s) async for s in cursor]
    return {"success": True, "data": submissions, "message": "ok"}


@router.patch("/submissions/{submission_id}/grade")
async def grade_submission(
    submission_id: str,
    payload: GradeSubmissionRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    submission = await db.submissions.find_one({"_id": ObjectId(submission_id)})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    await db.submissions.update_one(
        {"_id": ObjectId(submission_id)},
        {
            "$set": {
                "score": payload.score,
                "feedback": payload.feedback,
                "gradedBy": str(user["_id"]),
                "gradedAt": datetime.utcnow(),
            }
        },
    )
    updated = await db.submissions.find_one({"_id": ObjectId(submission_id)})
    return {"success": True, "data": submission_to_public(updated), "message": "graded"}


@router.get("/course/{course_id}/my-grades")
async def my_grades(course_id: str, user: dict = Depends(require_roles("student"))):
    db = database.db
    assignment_ids = [
        str(a["_id"]) async for a in db.assignments.find({"courseId": course_id})
    ]
    cursor = db.submissions.find(
        {"assignmentId": {"$in": assignment_ids}, "studentId": str(user["_id"])}
    )
    grades = [submission_to_public(s) async for s in cursor]
    return {"success": True, "data": grades, "message": "ok"}
