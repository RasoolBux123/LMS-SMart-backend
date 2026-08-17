# api/routes/project.py
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime
import os
import shutil
import uuid
import json

from app.core.database import database
from app.api.deps import get_current_user, require_roles
from app.models.project import new_project_doc, project_to_public
from app.models.submission import new_submission_doc, submission_to_public
from app.schemas.project import (
    GradeProjectSubmissionRequest,
)

router = APIRouter(prefix="/projects", tags=["projects"])


UPLOAD_DIR = "uploads/projects"
SUBMISSION_UPLOAD_DIR = "uploads/submissions"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SUBMISSION_UPLOAD_DIR, exist_ok=True)



# ---- Instructor/Admin: upload/create project ----
@router.post("")
async def create_project(
    courseId: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    instructions: str = Form(""),
    dueAt: str = Form(...),
    maxScore: int = Form(...),
    maxFileSizeMb: int = Form(25),
    allowedFileTypes: str = Form("[]"),  # JSON-encoded array from the frontend
    status: str = Form("draft"),
    file: UploadFile = File(...),
    user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db

    # Validate course ObjectId
    try:
        course_id = ObjectId(courseId)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid course ID")

    course = await db.courses.find_one({"_id": course_id})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Parse allowedFileTypes (sent as a JSON string array)
    try:
        allowed_file_types = json.loads(allowedFileTypes)
        if not isinstance(allowed_file_types, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="allowedFileTypes must be a JSON array of strings",
        )

    if status not in ("draft", "published"):
        raise HTTPException(status_code=400, detail="status must be 'draft' or 'published'")

    # Save project file
    file_extension = os.path.splitext(file.filename)[1]
    file_name = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, file_name)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc = new_project_doc(
        course_id=courseId,
        title=title,
        description=description,
        instructions=instructions,
        due_at=dueAt,
        max_score=maxScore,
        max_file_size_mb=maxFileSizeMb,
        allowed_file_types=allowed_file_types,
        status=status,
        attachment_url=file_path,
    )

    result = await db.projects.insert_one(doc)
    doc["_id"] = result.inserted_id

    return {
        "success": True,
        "data": project_to_public(doc),
        "message": "project created"
    }
# ---- Instructor/Admin: update project ----
@router.put("/{project_id}")
async def update_project(
    project_id: str,
    courseId: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    dueAt: str = Form(...),
    maxScore: int = Form(...),
    file: UploadFile = File(None),
    user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db

    # Validate project ObjectId
    try:
        project_object_id = ObjectId(project_id)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid project ID"
        )

    # Check if project exists
    project = await db.projects.find_one({
        "_id": project_object_id
    })

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    # Validate course ObjectId
    try:
        course_object_id = ObjectId(courseId)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid course ID"
        )

    course = await db.courses.find_one({
        "_id": course_object_id
    })

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found"
        )

    update_data = {
        "courseId": courseId,
        "title": title,
        "description": description,
        "dueAt": dueAt,
        "maxScore": maxScore,
        "updatedAt": datetime.utcnow(),
    }

    # Replace file only when instructor uploads a new file
    if file:
        file_extension = os.path.splitext(file.filename)[1]
        file_name = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, file_name)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        update_data["attachmentUrl"] = file_path

    await db.projects.update_one(
        {"_id": project_object_id},
        {"$set": update_data}
    )

    updated_project = await db.projects.find_one({
        "_id": project_object_id
    })

    return {
        "success": True,
        "data": project_to_public(updated_project),
        "message": "project updated"
    }


# ---- Instructor/Admin: delete project ----
@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db

    # Validate project ObjectId
    try:
        project_object_id = ObjectId(project_id)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid project ID"
        )

    # Check if project exists
    project = await db.projects.find_one({
        "_id": project_object_id
    })

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    # Delete project
    result = await db.projects.delete_one({
        "_id": project_object_id
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    # Delete related submissions
    await db.submissions.delete_many({
        "type": "project",
        "referenceId": project_id
    })

    return {
        "success": True,
        "data": None,
        "message": "project deleted"
    }


# ---- List projects for a course (admin, instructor, student) ----
@router.get("/course/{course_id}")
async def list_projects(
    course_id: str,
    user: dict = Depends(get_current_user)
):
    db = database.db

    cursor = db.projects.find({
        "courseId": course_id
    }).sort("dueAt", 1)

    projects = [
        project_to_public(p)
        async for p in cursor
    ]

    return {
        "success": True,
        "data": projects,
        "message": "ok"
    }


# ---- Get single project details ----
@router.get("/{project_id}")
async def get_project(
    project_id: str,
    user: dict = Depends(get_current_user)
):
    db = database.db

    try:
        project_object_id = ObjectId(project_id)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid project ID"
        )

    project = await db.projects.find_one({
        "_id": project_object_id
    })

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    return {
        "success": True,
        "data": project_to_public(project),
        "message": "ok"
    }


# ---- Student: submit project with file ----
@router.post("/{project_id}/submit")
async def submit_project(
    project_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_roles("student"))
):
    db = database.db

    try:
        project_object_id = ObjectId(project_id)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid project ID"
        )

    project = await db.projects.find_one({
        "_id": project_object_id
    })

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    existing = await db.submissions.find_one({
        "type": "project",
        "referenceId": project_id,
        "studentId": str(user["_id"]),
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Already submitted"
        )

    # Save student submission file
    file_extension = os.path.splitext(file.filename)[1]
    file_name = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(SUBMISSION_UPLOAD_DIR, file_name)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc = new_submission_doc(
        "project",
        project_id,
        str(user["_id"]),
        content=file_path
    )

    result = await db.submissions.insert_one(doc)
    doc["_id"] = result.inserted_id

    return {
        "success": True,
        "data": submission_to_public(doc),
        "message": "submitted"
    }


# ---- Instructor/Admin: list submissions for a project ----
@router.get("/{project_id}/submissions")
async def list_project_submissions(
    project_id: str,
    user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db

    try:
        project_object_id = ObjectId(project_id)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid project ID"
        )

    project = await db.projects.find_one({
        "_id": project_object_id
    })

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    cursor = db.submissions.find({
        "type": "project",
        "referenceId": project_id
    })

    submissions = [
        submission_to_public(s)
        async for s in cursor
    ]

    return {
        "success": True,
        "data": submissions,
        "message": "ok"
    }


# ---- Instructor: grade a project submission ----
@router.patch("/submissions/{submission_id}/grade")
async def grade_project_submission(
    submission_id: str,
    payload: GradeProjectSubmissionRequest,
    user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db

    try:
        submission_object_id = ObjectId(submission_id)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid submission ID"
        )

    submission = await db.submissions.find_one({
        "_id": submission_object_id,
        "type": "project"
    })

    if not submission:
        raise HTTPException(
            status_code=404,
            detail="Submission not found"
        )

    await db.submissions.update_one(
        {"_id": submission_object_id},
        {
            "$set": {
                "score": payload.score,
                "feedback": payload.feedback,
                "gradedBy": str(user["_id"]),
                "gradedAt": datetime.utcnow(),
            }
        },
    )

    updated = await db.submissions.find_one({
        "_id": submission_object_id
    })

    return {
        "success": True,
        "data": submission_to_public(updated),
        "message": "graded"
    }


# ---- Student: view own project submission for a course ----
@router.get("/course/{course_id}/my-submissions")
async def my_project_submissions(
    course_id: str,
    user: dict = Depends(require_roles("student"))
):
    db = database.db

    project_ids = [
        str(p["_id"])
        async for p in db.projects.find({
            "courseId": course_id
        })
    ]

    cursor = db.submissions.find({
        "type": "project",
        "referenceId": {"$in": project_ids},
        "studentId": str(user["_id"]),
    })

    submissions = [
        submission_to_public(s)
        async for s in cursor
    ]

    return {
        "success": True,
        "data": submissions,
        "message": "ok"
    }
    
    
# ---- Instructor/Admin: list all projects across the instructor's own courses ----
@router.get("/instructor/my-projects")
async def list_my_projects(
    status: str | None = None,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db

    # Find the instructor's own courses (admins see all courses' projects)
    course_query = {} if user["role"] == "admin" else {"instructorId": str(user["_id"])}
    course_ids = [str(c["_id"]) async for c in db.courses.find(course_query)]

    if not course_ids:
        return {"success": True, "data": [], "message": "ok"}

    project_query = {"courseId": {"$in": course_ids}}
    if status:
        project_query["status"] = status

    cursor = db.projects.find(project_query).sort("createdAt", -1)
    projects = [project_to_public(p) async for p in cursor]

    # Attach course title/code for display
    course_docs = {
        str(c["_id"]): c async for c in db.courses.find({"_id": {"$in": [ObjectId(cid) for cid in course_ids]}})
    }
    for p in projects:
        course = course_docs.get(p["courseId"])
        p["courseTitle"] = course.get("title", "") if course else ""

    return {"success": True, "data": projects, "message": "ok"}


# ---- Student: list all projects across enrolled courses ----
@router.get("/student/my-projects")
async def list_student_projects(
    status: str | None = None,
    user: dict = Depends(require_roles("student")),
):
    db = database.db

    enrollments = await db.enrollments.find({"userId": str(user["_id"])}).to_list(length=None)
    course_ids = [e["courseId"] for e in enrollments]

    if not course_ids:
        return {"success": True, "data": [], "message": "ok"}

    project_query = {"courseId": {"$in": course_ids}, "status": "published"}
    if status and status != "published":
        # students should never see drafts; ignore any other status filter override
        pass

    cursor = db.projects.find(project_query).sort("dueAt", 1)
    projects = [project_to_public(p) async for p in cursor]

    course_docs = {
        str(c["_id"]): c async for c in db.courses.find({"_id": {"$in": [ObjectId(cid) for cid in course_ids]}})
    }
    for p in projects:
        course = course_docs.get(p["courseId"])
        p["courseTitle"] = course.get("title", "") if course else ""

    return {"success": True, "data": projects, "message": "ok"}