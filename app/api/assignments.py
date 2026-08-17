from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from bson import ObjectId
from datetime import datetime
from typing import Optional
import os
import uuid
import shutil

from app.core.database import database
from app.api.deps import get_current_user, require_roles
from app.models.assignment import new_assignment_doc, assignment_to_public
from app.models.submission import new_submission_doc, submission_to_public
from app.schemas.assignment import (
    CreateAssignmentRequest, SubmitAssignmentRequest, SubmitQuizRequest, GradeSubmissionRequest
)

SUBMISSION_UPLOAD_DIR = "uploads/submissions"
os.makedirs(SUBMISSION_UPLOAD_DIR, exist_ok=True)

router = APIRouter(prefix="/assignments", tags=["assignments"])




# from fastapi import APIRouter, Depends, HTTPException
# from bson import ObjectId
# from datetime import datetime

# from app.core.database import database
# from app.api.deps import get_current_user, require_roles
# from app.models.assignment import new_assignment_doc, assignment_to_public
# from app.models.submission import new_submission_doc, submission_to_public
# from app.schemas.assignment import (
#     CreateAssignmentRequest, SubmitAssignmentRequest, SubmitQuizRequest, GradeSubmissionRequest
# )

# router = APIRouter(prefix="/assignments", tags=["assignments"])

# ---- Instructor: create assignment ----
@router.post("")
async def create_assignment(payload: CreateAssignmentRequest, user: dict = Depends(require_roles("instructor", "admin"))):
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
    return {"success": True, "data": assignment_to_public(doc, include_answers=True), "message": "assignment created"}

# ---- List assignments for a course ----
@router.get("/course/{course_id}")
async def list_assignments(course_id: str, user: dict = Depends(get_current_user)):
    db = database.db
    cursor = db.assignments.find({"courseId": course_id}).sort("dueAt", 1)
    include_answers = user["role"] in ("instructor", "admin")
    assignments = [assignment_to_public(a, include_answers) async for a in cursor]
    return {"success": True, "data": assignments, "message": "ok"}


@router.get("/{assignment_id}")
async def get_assignment(assignment_id: str, user: dict = Depends(get_current_user)):
    db = database.db
    assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    include_answers = user["role"] in ("instructor", "admin")
    return {
        "success": True,
        "data": assignment_to_public(assignment, include_answers),
        "message": "ok",
    }

# ---- Student: submit assignment (file/text) ----
@router.post("/{assignment_id}/submit/assignment")
async def submit_assignment(assignment_id: str, payload: SubmitAssignmentRequest, user: dict = Depends(require_roles("student"))):
    db = database.db
    assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    deadline = assignment.get("deadline") or assignment.get("dueAt")
    if deadline:
        try:
            if isinstance(deadline, str):
                try:
                    from dateutil import parser as _dp
                    deadline = _dp.isoparse(deadline)
                except Exception:
                    deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            if getattr(deadline, "tzinfo", None) is not None:
                deadline = deadline.replace(tzinfo=None)
            if datetime.utcnow() > deadline:
                raise HTTPException(
                    status_code=403,
                    detail="Deadline has passed. Submissions are no longer accepted.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    existing = await db.submissions.find_one({"assignmentId": assignment_id, "studentId": str(user["_id"])})
    if existing:
        raise HTTPException(status_code=400, detail="Already submitted")

    doc = new_submission_doc(assignment_id, str(user["_id"]), content=payload.content)
    result = await db.submissions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "data": submission_to_public(doc), "message": "submitted"}

# ---- Student: submit quiz (auto-graded) ----
@router.post("/{assignment_id}/submit/quiz")
async def submit_quiz(assignment_id: str, payload: SubmitQuizRequest, user: dict = Depends(require_roles("student"))):
    db = database.db
    assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
    if not assignment or assignment["type"] != "quiz":
        raise HTTPException(status_code=404, detail="Quiz not found")

    existing = await db.submissions.find_one({"assignmentId": assignment_id, "studentId": str(user["_id"])})
    if existing:
        raise HTTPException(status_code=400, detail="Already submitted")

    questions = assignment.get("questions", [])
    correct = sum(
        1 for i, q in enumerate(questions)
        if i < len(payload.answers) and payload.answers[i] == q["correctIndex"]
    )
    score = (correct / len(questions)) * assignment["maxScore"] if questions else 0

    doc = new_submission_doc(assignment_id, str(user["_id"]), answers=payload.answers)
    doc["score"] = round(score, 2)
    doc["gradedAt"] = datetime.utcnow()
    doc["gradedBy"] = "auto"

    result = await db.submissions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "data": submission_to_public(doc), "message": "quiz auto-graded"}
# ---- Student: submit assignment with file (matches frontend POST /assignments/{id}/submissions) ----
@router.post("/{assignment_id}/submissions")
async def submit_assignment_with_file(
    assignment_id: str,
    file: UploadFile = File(...),
    studentId: Optional[str] = Query(None),
    content: Optional[str] = Form(None),
    user: dict = Depends(require_roles("student")),
):
    """
    Frontend calls: POST /assignments/{id}/submissions?studentId=...
    with multipart form containing "file".
    """
    db = database.db

    if not ObjectId.is_valid(assignment_id):
        raise HTTPException(status_code=400, detail="Invalid assignment ID")

    assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if assignment.get("status") != "published":
        raise HTTPException(status_code=403, detail="Assignment is not published")

    student_id = str(user["_id"])

    # Deadline: block all submissions after due date
    deadline = assignment.get("deadline") or assignment.get("dueAt")
    if deadline:
        try:
            if isinstance(deadline, str):
                try:
                    from dateutil import parser as _dp
                    deadline = _dp.isoparse(deadline)
                except Exception:
                    deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            if getattr(deadline, "tzinfo", None) is not None:
                deadline = deadline.replace(tzinfo=None)
            if datetime.utcnow() > deadline:
                raise HTTPException(
                    status_code=403,
                    detail="Deadline has passed. Submissions are no longer accepted.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    # Check existing submission / max attempts
    existing = await db.submissions.find_one(
        {"assignmentId": assignment_id, "studentId": student_id}
    )
    max_attempts = assignment.get("maxAttempts", 1)
    resubmission_allowed = assignment.get("resubmissionAllowed", False)

    if existing:
        attempts = existing.get("attemptNumber", 1)
        if not resubmission_allowed or attempts >= max_attempts:
            raise HTTPException(status_code=400, detail="Already submitted / max attempts reached")

    # Save uploaded file
    original_name = file.filename or "submission"
    ext = os.path.splitext(original_name)[1]
    safe_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(SUBMISSION_UPLOAD_DIR, safe_name)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_meta = {
        "id": safe_name,
        "name": original_name,
        "url": f"/uploads/submissions/{safe_name}",
        "size": os.path.getsize(file_path),
        "kind": ext.lstrip(".").lower() or "other",
    }

    status = "submitted"

    if existing and resubmission_allowed:
        # Update existing
        new_attempt = existing.get("attemptNumber", 1) + 1
        await db.submissions.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "files": [file_meta],
                    "content": content or existing.get("content", ""),
                    "submittedAt": datetime.utcnow(),
                    "status": status,
                    "attemptNumber": new_attempt,
                    "score": None,
                    "feedback": None,
                    "gradedAt": None,
                    "gradedBy": None,
                }
            },
        )
        updated = await db.submissions.find_one({"_id": existing["_id"]})
        return {
            "success": True,
            "data": submission_to_public(updated),
            "message": "resubmitted",
        }

    # New submission
    doc = new_submission_doc(
        assignment_id=assignment_id,
        student_id=student_id,
        content=content or "",
        files=[file_meta],
    )
    doc["status"] = status
    doc["attemptNumber"] = 1

    result = await db.submissions.insert_one(doc)
    doc["_id"] = result.inserted_id

    return {
        "success": True,
        "data": submission_to_public(doc),
        "message": "submitted",
    }

# ---- Instructor: list submissions for an assignment ----
@router.get("/{assignment_id}/submissions")
async def list_submissions(assignment_id: str, user: dict = Depends(require_roles("instructor", "admin"))):
    db = database.db
    cursor = db.submissions.find({"assignmentId": assignment_id})
    submissions = [submission_to_public(s) async for s in cursor]
    return {"success": True, "data": submissions, "message": "ok"}

# ---- Instructor: grade a submission (manual) ----
@router.patch("/submissions/{submission_id}/grade")
async def grade_submission(submission_id: str, payload: GradeSubmissionRequest, user: dict = Depends(require_roles("instructor", "admin"))):
    db = database.db
    submission = await db.submissions.find_one({"_id": ObjectId(submission_id)})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    await db.submissions.update_one(
        {"_id": ObjectId(submission_id)},
        {"$set": {
            "score": payload.score,
            "feedback": payload.feedback,
            "gradedBy": str(user["_id"]),
            "gradedAt": datetime.utcnow(),
        }},
    )
    updated = await db.submissions.find_one({"_id": ObjectId(submission_id)})
    return {"success": True, "data": submission_to_public(updated), "message": "graded"}

# ---- Student: view own grades for a course ----
@router.get("/course/{course_id}/my-grades")
async def my_grades(course_id: str, user: dict = Depends(require_roles("student"))):
    db = database.db
    assignment_ids = [
        str(a["_id"]) async for a in db.assignments.find({"courseId": course_id})
    ]
    cursor = db.submissions.find({
        "assignmentId": {"$in": assignment_ids},
        "studentId": str(user["_id"]),
    })
    grades = [submission_to_public(s) async for s in cursor]
    return {"success": True, "data": grades, "message": "ok"}





# from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
# from bson import ObjectId
# from datetime import datetime
# from typing import Optional
# import os
# import uuid
# import shutil

# from app.core.database import database
# from app.api.deps import get_current_user, require_roles
# from app.models.assignment import new_assignment_doc, assignment_to_public
# from app.models.submission import new_submission_doc, submission_to_public
# from app.schemas.assignment import (
#     CreateAssignmentRequest, SubmitAssignmentRequest, SubmitQuizRequest, GradeSubmissionRequest
# )

# SUBMISSION_UPLOAD_DIR = "uploads/submissions"
# os.makedirs(SUBMISSION_UPLOAD_DIR, exist_ok=True)

# router = APIRouter(prefix="/assignments", tags=["assignments"])




# # from fastapi import APIRouter, Depends, HTTPException
# # from bson import ObjectId
# # from datetime import datetime

# # from app.core.database import database
# # from app.api.deps import get_current_user, require_roles
# # from app.models.assignment import new_assignment_doc, assignment_to_public
# # from app.models.submission import new_submission_doc, submission_to_public
# # from app.schemas.assignment import (
# #     CreateAssignmentRequest, SubmitAssignmentRequest, SubmitQuizRequest, GradeSubmissionRequest
# # )

# # router = APIRouter(prefix="/assignments", tags=["assignments"])

# # ---- Instructor: create assignment ----
# @router.post("")
# async def create_assignment(payload: CreateAssignmentRequest, user: dict = Depends(require_roles("instructor", "admin"))):
#     db = database.db
#     course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
#     if not course:
#         raise HTTPException(status_code=404, detail="Course not found")

#     questions = [q.dict() for q in payload.questions] if payload.type == "quiz" else []
#     doc = new_assignment_doc(
#         course_id=payload.courseId,
#         title=payload.title,
#         description=payload.description,
#         due_at=payload.dueAt,
#         max_score=payload.maxScore,
#         assignment_type=payload.type,
#         questions=questions,
#     )
#     result = await db.assignments.insert_one(doc)
#     doc["_id"] = result.inserted_id
#     return {"success": True, "data": assignment_to_public(doc, include_answers=True), "message": "assignment created"}

# # ---- List assignments for a course ----
# @router.get("/course/{course_id}")
# async def list_assignments(course_id: str, user: dict = Depends(get_current_user)):
#     db = database.db
#     cursor = db.assignments.find({"courseId": course_id}).sort("dueAt", 1)
#     include_answers = user["role"] in ("instructor", "admin")
#     assignments = [assignment_to_public(a, include_answers) async for a in cursor]
#     return {"success": True, "data": assignments, "message": "ok"}


# @router.get("/{assignment_id}")
# async def get_assignment(assignment_id: str, user: dict = Depends(get_current_user)):
#     db = database.db
#     assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
#     if not assignment:
#         raise HTTPException(status_code=404, detail="Assignment not found")
#     include_answers = user["role"] in ("instructor", "admin")
#     return {
#         "success": True,
#         "data": assignment_to_public(assignment, include_answers),
#         "message": "ok",
#     }

# # ---- Student: submit assignment (file/text) ----
# @router.post("/{assignment_id}/submit/assignment")
# async def submit_assignment(assignment_id: str, payload: SubmitAssignmentRequest, user: dict = Depends(require_roles("student"))):
#     db = database.db
#     assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
#     if not assignment:
#         raise HTTPException(status_code=404, detail="Assignment not found")

#     existing = await db.submissions.find_one({"assignmentId": assignment_id, "studentId": str(user["_id"])})
#     if existing:
#         raise HTTPException(status_code=400, detail="Already submitted")

#     doc = new_submission_doc(assignment_id, str(user["_id"]), content=payload.content)
#     result = await db.submissions.insert_one(doc)
#     doc["_id"] = result.inserted_id
#     return {"success": True, "data": submission_to_public(doc), "message": "submitted"}

# # ---- Student: submit quiz (auto-graded) ----
# @router.post("/{assignment_id}/submit/quiz")
# async def submit_quiz(assignment_id: str, payload: SubmitQuizRequest, user: dict = Depends(require_roles("student"))):
#     db = database.db
#     assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
#     if not assignment or assignment["type"] != "quiz":
#         raise HTTPException(status_code=404, detail="Quiz not found")

#     existing = await db.submissions.find_one({"assignmentId": assignment_id, "studentId": str(user["_id"])})
#     if existing:
#         raise HTTPException(status_code=400, detail="Already submitted")

#     questions = assignment.get("questions", [])
#     correct = sum(
#         1 for i, q in enumerate(questions)
#         if i < len(payload.answers) and payload.answers[i] == q["correctIndex"]
#     )
#     score = (correct / len(questions)) * assignment["maxScore"] if questions else 0

#     doc = new_submission_doc(assignment_id, str(user["_id"]), answers=payload.answers)
#     doc["score"] = round(score, 2)
#     doc["gradedAt"] = datetime.utcnow()
#     doc["gradedBy"] = "auto"

#     result = await db.submissions.insert_one(doc)
#     doc["_id"] = result.inserted_id
#     return {"success": True, "data": submission_to_public(doc), "message": "quiz auto-graded"}
# # ---- Student: submit assignment with file (matches frontend POST /assignments/{id}/submissions) ----
# @router.post("/{assignment_id}/submissions")
# async def submit_assignment_with_file(
#     assignment_id: str,
#     file: UploadFile = File(...),
#     studentId: Optional[str] = Query(None),
#     content: Optional[str] = Form(None),
#     user: dict = Depends(require_roles("student")),
# ):
#     """
#     Frontend calls: POST /assignments/{id}/submissions?studentId=...
#     with multipart form containing "file".
#     """
#     db = database.db

#     if not ObjectId.is_valid(assignment_id):
#         raise HTTPException(status_code=400, detail="Invalid assignment ID")

#     assignment = await db.assignments.find_one({"_id": ObjectId(assignment_id)})
#     if not assignment:
#         raise HTTPException(status_code=404, detail="Assignment not found")

#     if assignment.get("status") != "published":
#         raise HTTPException(status_code=403, detail="Assignment is not published")

#     student_id = str(user["_id"])

#     # Check existing submission / max attempts
#     existing = await db.submissions.find_one(
#         {"assignmentId": assignment_id, "studentId": student_id}
#     )
#     max_attempts = assignment.get("maxAttempts", 1)
#     resubmission_allowed = assignment.get("resubmissionAllowed", False)

#     if existing:
#         attempts = existing.get("attemptNumber", 1)
#         if not resubmission_allowed or attempts >= max_attempts:
#             raise HTTPException(status_code=400, detail="Already submitted / max attempts reached")

#     # Save uploaded file
#     original_name = file.filename or "submission"
#     ext = os.path.splitext(original_name)[1]
#     safe_name = f"{uuid.uuid4().hex}{ext}"
#     file_path = os.path.join(SUBMISSION_UPLOAD_DIR, safe_name)

#     with open(file_path, "wb") as buffer:
#         shutil.copyfileobj(file.file, buffer)

#     file_meta = {
#         "id": safe_name,
#         "name": original_name,
#         "url": f"/uploads/submissions/{safe_name}",
#         "size": os.path.getsize(file_path),
#         "kind": ext.lstrip(".").lower() or "other",
#     }

#     # Deadline / late status
#     deadline = assignment.get("deadline") or assignment.get("dueAt")
#     is_late = False
#     if deadline:
#         try:
#             if isinstance(deadline, str):
#                 try:
#                     from dateutil import parser as _dp
#                     deadline = _dp.isoparse(deadline)
#                 except Exception:
#                     deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
#             if getattr(deadline, "tzinfo", None) is not None:
#                 deadline = deadline.replace(tzinfo=None)
#             is_late = datetime.utcnow() > deadline
#         except Exception:
#             pass

#     status = "late" if is_late else "submitted"

#     if existing and resubmission_allowed:
#         # Update existing
#         new_attempt = existing.get("attemptNumber", 1) + 1
#         await db.submissions.update_one(
#             {"_id": existing["_id"]},
#             {
#                 "$set": {
#                     "files": [file_meta],
#                     "content": content or existing.get("content", ""),
#                     "submittedAt": datetime.utcnow(),
#                     "status": status,
#                     "attemptNumber": new_attempt,
#                     "score": None,
#                     "feedback": None,
#                     "gradedAt": None,
#                     "gradedBy": None,
#                 }
#             },
#         )
#         updated = await db.submissions.find_one({"_id": existing["_id"]})
#         return {
#             "success": True,
#             "data": submission_to_public(updated),
#             "message": "resubmitted",
#         }

#     # New submission
#     doc = new_submission_doc(
#         assignment_id=assignment_id,
#         student_id=student_id,
#         content=content or "",
#         files=[file_meta],
#     )
#     doc["status"] = status
#     doc["attemptNumber"] = 1

#     result = await db.submissions.insert_one(doc)
#     doc["_id"] = result.inserted_id

#     return {
#         "success": True,
#         "data": submission_to_public(doc),
#         "message": "submitted",
#     }

# # ---- Instructor: list submissions for an assignment ----
# @router.get("/{assignment_id}/submissions")
# async def list_submissions(assignment_id: str, user: dict = Depends(require_roles("instructor", "admin"))):
#     db = database.db
#     cursor = db.submissions.find({"assignmentId": assignment_id})
#     submissions = [submission_to_public(s) async for s in cursor]
#     return {"success": True, "data": submissions, "message": "ok"}

# # ---- Instructor: grade a submission (manual) ----
# @router.patch("/submissions/{submission_id}/grade")
# async def grade_submission(submission_id: str, payload: GradeSubmissionRequest, user: dict = Depends(require_roles("instructor", "admin"))):
#     db = database.db
#     submission = await db.submissions.find_one({"_id": ObjectId(submission_id)})
#     if not submission:
#         raise HTTPException(status_code=404, detail="Submission not found")

#     await db.submissions.update_one(
#         {"_id": ObjectId(submission_id)},
#         {"$set": {
#             "score": payload.score,
#             "feedback": payload.feedback,
#             "gradedBy": str(user["_id"]),
#             "gradedAt": datetime.utcnow(),
#         }},
#     )
#     updated = await db.submissions.find_one({"_id": ObjectId(submission_id)})
#     return {"success": True, "data": submission_to_public(updated), "message": "graded"}

# # ---- Student: view own grades for a course ----
# @router.get("/course/{course_id}/my-grades")
# async def my_grades(course_id: str, user: dict = Depends(require_roles("student"))):
#     db = database.db
#     assignment_ids = [
#         str(a["_id"]) async for a in db.assignments.find({"courseId": course_id})
#     ]
#     cursor = db.submissions.find({
#         "assignmentId": {"$in": assignment_ids},
#         "studentId": str(user["_id"]),
#     })
#     grades = [submission_to_public(s) async for s in cursor]
#     return {"success": True, "data": grades, "message": "ok"}