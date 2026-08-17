"""
Unified coursework API for assignments, quizzes, exams and projects.

Matches the frontend contract in frontend/src/lib/api/coursework.ts:
- List returns a raw array of list-items (course joined + counts)
- Detail returns a single list-item
- Create/update/status return the entity
- Students only see published items from courses they are enrolled in
- Instructors see items they own
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_roles
from app.core.database import database
from app.api.notifications import notify_enrolled_students

Status = Literal["draft", "published", "archived"]

# collection name → singular kind used by notifications
COLLECTION_KIND = {
    "assignments": "assignment",
    "quizzes": "quiz",
    "exams": "exam",
    "projects": "project",
}


class CourseworkPayload(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    objectives: list[str] = Field(default_factory=list)
    instructions: str = ""
    courseId: str
    deadline: Optional[str] = None
    totalMarks: float = 100
    allowedFileTypes: list[str] = Field(
        default_factory=lambda: ["pdf", "docx", "zip"]
    )
    maxFileSizeMb: int = 25
    resubmissionAllowed: bool = False
    maxAttempts: int = 1
    status: Status = "draft"
    instructorId: Optional[str] = None
    timeLimit: Optional[int] = None
    passingScore: Optional[float] = None
    questions: list[dict] = Field(default_factory=list)


class StatusPayload(BaseModel):
    status: Status


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")


def _guess_kind(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if ext == "pdf":
        return "pdf"
    if ext in ("doc", "docx"):
        return "docx"
    if ext in ("png", "jpg", "jpeg", "gif", "webp"):
        return "image"
    if ext in ("zip", "rar", "7z"):
        return "zip"
    return "other"


def _to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


async def _join_course(db, course_id: str) -> dict:
    course = None
    if course_id and ObjectId.is_valid(course_id):
        course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        return {"id": course_id or "", "code": "—", "title": "Unassigned"}
    return {
        "id": str(course["_id"]),
        "code": course.get("code") or (course.get("title", "—")[:8]),
        "title": course.get("title", "Untitled"),
        "instructorName": course.get("instructorName"),
    }


async def _counts(db, collection: str, item_id: str) -> tuple[int, int, int]:
    item = await db[collection].find_one({"_id": ObjectId(item_id)})
    enrolled = 0
    if item and item.get("courseId"):
        enrolled = await db.enrollments.count_documents(
            {"courseId": item["courseId"]}
        )
    sub_coll = {
        "assignments": "submissions",
        "quizzes": "quiz_attempts",
        "exams": "exam_submissions",
        "projects": "submissions",
    }.get(collection, "submissions")
    id_field = {
        "assignments": "assignmentId",
        "quizzes": "quizId",
        "exams": "examId",
        "projects": "assignmentId",
    }.get(collection, "assignmentId")
    submitted = await db[sub_coll].count_documents({id_field: item_id})
    graded = await db[sub_coll].count_documents(
        {id_field: item_id, "score": {"$ne": None}}
    )
    graded2 = await db[sub_coll].count_documents(
        {id_field: item_id, "status": "graded"}
    )
    return enrolled, submitted, max(graded, graded2)


def _doc_to_entity(doc: dict, collection: str) -> dict:
    deadline = (
        doc.get("deadline")
        or doc.get("dueAt")
        or doc.get("due_at")
        or doc.get("createdAt")
    )
    status = doc.get("status")
    if status is None:
        status = "published" if doc.get("isPublished", True) else "draft"

    attachments = doc.get("attachments") or []
    if not attachments and doc.get("attachmentUrl"):
        attachments = [
            {
                "id": "legacy",
                "name": os.path.basename(str(doc["attachmentUrl"])),
                "kind": _guess_kind(str(doc["attachmentUrl"])),
                "size": 0,
                "url": str(doc["attachmentUrl"]),
            }
        ]
    if not attachments and doc.get("examFile"):
        attachments = [
            {
                "id": "examfile",
                "name": os.path.basename(str(doc["examFile"])),
                "kind": _guess_kind(str(doc["examFile"])),
                "size": 0,
                "url": str(doc["examFile"]),
            }
        ]

    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", ""),
        "description": doc.get("description", ""),
        "objectives": doc.get("objectives") or [],
        "instructions": doc.get("instructions") or doc.get("description", ""),
        "courseId": doc.get("courseId", ""),
        "instructorId": doc.get("instructorId") or doc.get("createdBy", ""),
        "createdAt": _to_iso(doc.get("createdAt")) or datetime.utcnow().isoformat(),
        "deadline": _to_iso(deadline) or datetime.utcnow().isoformat(),
        "totalMarks": float(
            doc.get("totalMarks")
            or doc.get("maxScore")
            or doc.get("max_score")
            or 100
        ),
        "allowedFileTypes": doc.get("allowedFileTypes")
        or doc.get("allowed_file_types")
        or ["pdf", "docx", "zip"],
        "maxFileSizeMb": int(
            doc.get("maxFileSizeMb") or doc.get("max_file_size_mb") or 25
        ),
        "resubmissionAllowed": bool(doc.get("resubmissionAllowed", False)),
        "maxAttempts": int(
            doc.get("maxAttempts") or doc.get("attemptsAllowed") or 1
        ),
        "attachments": attachments,
        "status": status,
        "timeLimit": doc.get("timeLimit"),
        "passingScore": doc.get("passingScore"),
        "questions": doc.get("questions") or [],
    }


async def _to_list_item(db, doc: dict, collection: str) -> dict:
    entity = _doc_to_entity(doc, collection)
    course = await _join_course(db, entity["courseId"])
    enrolled, submitted, graded = await _counts(db, collection, entity["id"])
    return {
        **entity,
        "course": course,
        "enrolled": enrolled,
        "submittedCount": submitted,
        "gradedCount": graded,
    }


async def _student_enrolled_course_ids(db, user: dict) -> list[str]:
    cursor = db.enrollments.find({"userId": str(user["_id"])})
    return [e["courseId"] async for e in cursor]


# --------------------------------------------------------------------------
# Submissions
# --------------------------------------------------------------------------
# Assignments and projects intentionally share the `submissions` collection
# (and the `assignmentId` field), which is how the existing counting and
# grading code already reads them.

_SUB_COLLECTION = {
    "assignments": "submissions",
    "quizzes": "quiz_attempts",
    "exams": "exam_submissions",
    "projects": "submissions",
}

_SUB_ID_FIELD = {
    "assignments": "assignmentId",
    "quizzes": "quizId",
    "exams": "examId",
    "projects": "assignmentId",
}


def _parse_dt(value: Any) -> Optional[datetime]:
    """Deadlines are stored as datetimes by newer writes and as ISO strings
    by older ones, so both have to be understood."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _normalise_ext(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    # A rule of "docx" is meant to cover Word files generally.
    if ext == "doc":
        return "docx"
    if ext in ("jpeg", "jpg"):
        return "jpg"
    return ext


def _submission_row(doc: dict, id_field: str, *, for_student: bool = False) -> dict:
    """The exact shape `Submission` in frontend/src/types/assignment.ts expects."""
    score = doc.get("score")
    if score is None:
        score = doc.get("marksAwarded")

    marks_hidden = bool(doc.get("marksHidden", False))
    # Student must not see marks/feedback while instructor has hidden them
    if for_student and marks_hidden:
        score = None
        feedback = None
        pass_fail = None
        status = "submitted"
    else:
        feedback = doc.get("feedback")
        pass_fail = doc.get("passFail")
        status = doc.get("status", "submitted")

    return {
        "id": str(doc["_id"]),
        "assignmentId": doc.get(id_field, ""),
        "studentId": doc.get("studentId", ""),
        "studentName": doc.get("studentName"),
        "studentEmail": doc.get("studentEmail"),
        "status": status,
        "submittedAt": _to_iso(doc.get("submittedAt") or doc.get("createdAt")),
        "files": doc.get("files") or [],
        "attemptNumber": doc.get("attemptNumber", 1),
        "marksAwarded": score,
        "feedback": feedback,
        "passFail": pass_fail,
        "marksHidden": marks_hidden,
    }


def _build_query(
    user: dict,
    *,
    search: Optional[str],
    status: Optional[str],
    course_id: Optional[str],
    instructor_id: Optional[str],
    student_id: Optional[str],
    enrolled_ids: Optional[list[str]] = None,
) -> dict:
    query: dict[str, Any] = {}

    if course_id:
        query["courseId"] = course_id

    if status and status != "all":
        if status == "published":
            query["$or"] = [
                {"status": "published"},
                {"isPublished": True, "status": {"$exists": False}},
            ]
        else:
            query["status"] = status

    if user["role"] == "student" or student_id:
        ids = enrolled_ids or []
        if course_id:
            if course_id not in ids:
                query["courseId"] = "__none__"
        else:
            query["courseId"] = {"$in": ids}
        query.pop("status", None)
        query["$or"] = [
            {"status": "published"},
            {"isPublished": True, "status": {"$exists": False}},
            {"status": {"$exists": False}, "isPublished": {"$exists": False}},
        ]

    if user["role"] == "instructor" and not student_id:
        iid = instructor_id or str(user["_id"])
        query["instructorId"] = iid

    if user["role"] == "admin" and instructor_id:
        query["instructorId"] = instructor_id

    if search:
        query["$and"] = query.get("$and", []) + [
            {
                "$or": [
                    {"title": {"$regex": search, "$options": "i"}},
                    {"description": {"$regex": search, "$options": "i"}},
                ]
            }
        ]

    return query


def _new_doc(payload: CourseworkPayload, user: dict) -> dict:
    now = datetime.utcnow()
    instructor_id = payload.instructorId or str(user["_id"])
    deadline = None
    if payload.deadline:
        try:
            deadline = datetime.fromisoformat(
                payload.deadline.replace("Z", "+00:00")
            )
        except ValueError:
            deadline = payload.deadline

    return {
        "title": payload.title,
        "description": payload.description,
        "objectives": payload.objectives,
        "instructions": payload.instructions,
        "courseId": payload.courseId,
        "instructorId": instructor_id,
        "deadline": deadline or now,
        "dueAt": deadline or now,
        "totalMarks": payload.totalMarks,
        "maxScore": payload.totalMarks,
        "allowedFileTypes": payload.allowedFileTypes,
        "maxFileSizeMb": payload.maxFileSizeMb,
        "resubmissionAllowed": payload.resubmissionAllowed,
        "maxAttempts": payload.maxAttempts,
        "attemptsAllowed": payload.maxAttempts,
        "status": payload.status,
        "isPublished": payload.status == "published",
        "attachments": [],
        "timeLimit": payload.timeLimit,
        "passingScore": payload.passingScore,
        "questions": payload.questions,
        "createdAt": now,
        "updatedAt": now,
        "createdBy": instructor_id,
    }


def create_coursework_router(
    prefix: str,
    collection: str,
    tags: list[str],
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=tags)
    upload_dir = f"uploads/{collection}"
    os.makedirs(upload_dir, exist_ok=True)

    @router.get("")
    async def list_items(
        search: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        courseId: Optional[str] = Query(None),
        instructorId: Optional[str] = Query(None),
        studentId: Optional[str] = Query(None),
        user: dict = Depends(get_current_user),
    ):
        db = database.db
        enrolled = None
        if user["role"] == "student" or studentId:
            enrolled = await _student_enrolled_course_ids(db, user)

        query = _build_query(
            user,
            search=search,
            status=status,
            course_id=courseId,
            instructor_id=instructorId,
            student_id=studentId,
            enrolled_ids=enrolled,
        )

        cursor = db[collection].find(query).sort("createdAt", -1)
        items = []
        async for doc in cursor:
            items.append(await _to_list_item(db, doc, collection))
        return items

    @router.get("/{item_id}")
    async def get_item(
        item_id: str,
        user: dict = Depends(get_current_user),
    ):
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")
        doc = await db[collection].find_one({"_id": ObjectId(item_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Not found")

        if user["role"] == "student":
            enrolled = await _student_enrolled_course_ids(db, user)
            if doc.get("courseId") not in enrolled:
                raise HTTPException(status_code=403, detail="Not enrolled")
            status_val = doc.get("status")
            published = (
                status_val == "published"
                or (status_val is None and doc.get("isPublished", True))
            )
            if not published:
                raise HTTPException(status_code=403, detail="Not published")

        return await _to_list_item(db, doc, collection)

    @router.post("")
    async def create_item(
        payload: CourseworkPayload,
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        db = database.db
        if not ObjectId.is_valid(payload.courseId):
            raise HTTPException(status_code=400, detail="Invalid courseId")
        course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        doc = _new_doc(payload, user)
        result = await db[collection].insert_one(doc)
        doc["_id"] = result.inserted_id

        # Notify enrolled students when created as published
        if payload.status == "published":
            try:
                await notify_enrolled_students(
                    course_id=payload.courseId,
                    coursework_id=str(result.inserted_id),
                    coursework_kind=COLLECTION_KIND.get(collection, collection.rstrip("s")),
                    title=payload.title or doc.get("title", "New item"),
                    instructor_name=user.get("name") or "Instructor",
                )
            except Exception:
                # Never fail the create request because of notification errors
                pass

        return _doc_to_entity(doc, collection)

    @router.patch("/{item_id}")
    async def update_item(
        item_id: str,
        payload: CourseworkPayload,
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")
        existing = await db[collection].find_one({"_id": ObjectId(item_id)})
        if not existing:
            raise HTTPException(status_code=404, detail="Not found")

        if user["role"] == "instructor":
            owner = existing.get("instructorId") or existing.get("createdBy")
            if owner and owner != str(user["_id"]):
                raise HTTPException(status_code=403, detail="Not your item")

        was_published = (
            existing.get("status") == "published"
            or (
                existing.get("status") is None
                and existing.get("isPublished", False)
            )
        )

        update = _new_doc(payload, user)
        update["createdAt"] = existing.get("createdAt", update["createdAt"])
        update["attachments"] = existing.get("attachments", [])
        update.pop("createdBy", None)

        await db[collection].update_one(
            {"_id": ObjectId(item_id)}, {"$set": update}
        )
        doc = await db[collection].find_one({"_id": ObjectId(item_id)})

        if payload.status == "published" and not was_published:
            try:
                await notify_enrolled_students(
                    course_id=str(payload.courseId or existing.get("courseId") or ""),
                    coursework_id=item_id,
                    coursework_kind=COLLECTION_KIND.get(
                        collection, collection.rstrip("s")
                    ),
                    title=payload.title or existing.get("title") or "New item",
                    instructor_name=user.get("name") or "Instructor",
                )
            except Exception:
                pass

        return _doc_to_entity(doc, collection)

    @router.patch("/{item_id}/status")
    async def update_status(
        item_id: str,
        payload: StatusPayload,
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")
        existing = await db[collection].find_one({"_id": ObjectId(item_id)})
        if not existing:
            raise HTTPException(status_code=404, detail="Not found")

        was_published = (
            existing.get("status") == "published"
            or (
                existing.get("status") is None
                and existing.get("isPublished", False)
            )
        )

        await db[collection].update_one(
            {"_id": ObjectId(item_id)},
            {
                "$set": {
                    "status": payload.status,
                    "isPublished": payload.status == "published",
                    "updatedAt": datetime.utcnow(),
                }
            },
        )
        doc = await db[collection].find_one({"_id": ObjectId(item_id)})

        # Notify when first published (draft/archived → published)
        if payload.status == "published" and not was_published:
            try:
                course_id = str(
                    existing.get("courseId") or doc.get("courseId") or ""
                )
                await notify_enrolled_students(
                    course_id=course_id,
                    coursework_id=item_id,
                    coursework_kind=COLLECTION_KIND.get(
                        collection, collection.rstrip("s")
                    ),
                    title=existing.get("title") or doc.get("title") or "New item",
                    instructor_name=user.get("name") or "Instructor",
                )
            except Exception:
                pass

        return _doc_to_entity(doc, collection)

    @router.post("/{item_id}/duplicate")
    async def duplicate_item(
        item_id: str,
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")
        existing = await db[collection].find_one({"_id": ObjectId(item_id)})
        if not existing:
            raise HTTPException(status_code=404, detail="Not found")

        new_doc = {k: v for k, v in existing.items() if k != "_id"}
        new_doc["title"] = f"{existing.get('title', 'Item')} (Copy)"
        new_doc["status"] = "draft"
        new_doc["isPublished"] = False
        new_doc["createdAt"] = datetime.utcnow()
        new_doc["updatedAt"] = datetime.utcnow()
        new_doc["instructorId"] = str(user["_id"])
        result = await db[collection].insert_one(new_doc)
        new_doc["_id"] = result.inserted_id
        return await _to_list_item(db, new_doc, collection)

    @router.delete("/{item_id}")
    async def delete_item(
        item_id: str,
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")
        existing = await db[collection].find_one({"_id": ObjectId(item_id)})
        if not existing:
            raise HTTPException(status_code=404, detail="Not found")
        if user["role"] == "instructor":
            owner = existing.get("instructorId") or existing.get("createdBy")
            if owner and owner != str(user["_id"]):
                raise HTTPException(status_code=403, detail="Not your item")
        await db[collection].delete_one({"_id": ObjectId(item_id)})
        return {"data": None, "message": "deleted"}

    @router.post("/{item_id}/attachments")
    async def upload_attachment(
        item_id: str,
        file: UploadFile = File(...),
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")
        existing = await db[collection].find_one({"_id": ObjectId(item_id)})
        if not existing:
            raise HTTPException(status_code=404, detail="Not found")

        ext = os.path.splitext(file.filename or "file")[1]
        fname = f"{uuid.uuid4()}{ext}"
        path = os.path.join(upload_dir, fname)
        with open(path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)

        att = {
            "id": str(uuid.uuid4()),
            "name": file.filename or fname,
            "kind": _guess_kind(file.filename or fname),
            "size": os.path.getsize(path),
            "url": f"/uploads/{collection}/{fname}",
        }
        await db[collection].update_one(
            {"_id": ObjectId(item_id)},
            {
                "$push": {"attachments": att},
                "$set": {"updatedAt": datetime.utcnow()},
            },
        )
        return att

    @router.delete("/{item_id}/attachments/{attachment_id}")
    async def delete_attachment(
        item_id: str,
        attachment_id: str,
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")
        await db[collection].update_one(
            {"_id": ObjectId(item_id)},
            {"$pull": {"attachments": {"id": attachment_id}}},
        )
        return {"data": None, "message": "deleted"}
    #changing

    


    @router.post("/{item_id}/submissions")
    async def submit_work(
        item_id: str,
        file: UploadFile = File(...),
        studentId: Optional[str] = Query(None),
        user: dict = Depends(require_roles("student", "admin")),
    ):
        """Student hands in one file.

        `studentId` arrives as a query param from the submission panel, but it
        is only ever a hint — the owner of the submission is taken from the
        token so a student cannot submit on someone else's behalf.
        """
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")

        item = await db[collection].find_one({"_id": ObjectId(item_id)})
        if not item:
            raise HTTPException(status_code=404, detail="Not found")

        status_val = item.get("status")
        published = status_val == "published" or (
            status_val is None and item.get("isPublished", True)
        )
        if not published:
            raise HTTPException(
                status_code=403, detail="This item is not open for submissions"
            )

        if user["role"] == "student":
            enrolled = await _student_enrolled_course_ids(db, user)
            if item.get("courseId") not in enrolled:
                raise HTTPException(status_code=403, detail="Not enrolled")

        sub_coll = _SUB_COLLECTION.get(collection, "submissions")
        id_field = _SUB_ID_FIELD.get(collection, "assignmentId")
        student_id = str(user["_id"])

        # ---- deadline: block all submissions after due date ---------------
        now = datetime.utcnow()
        deadline = _parse_dt(item.get("deadline") or item.get("dueAt"))
        if deadline is not None and deadline.tzinfo is not None:
            deadline = deadline.replace(tzinfo=None)
        if deadline is not None and now > deadline:
            raise HTTPException(
                status_code=403,
                detail="Deadline has passed. Submissions are no longer accepted.",
            )

        # ---- attempt limits ------------------------------------------------
        previous = await db[sub_coll].count_documents(
            {id_field: item_id, "studentId": student_id}
        )
        resubmission = bool(item.get("resubmissionAllowed", False))
        max_attempts = int(
            item.get("maxAttempts") or item.get("attemptsAllowed") or 1
        )
        if previous:
            if not resubmission:
                raise HTTPException(
                    status_code=409,
                    detail="You have already submitted and resubmission is not allowed",
                )
            if previous >= max_attempts:
                raise HTTPException(
                    status_code=409,
                    detail=f"No attempts left (limit {max_attempts})",
                )

        # ---- file rules ----------------------------------------------------
        allowed = [
            _normalise_ext(t)
            for t in (
                item.get("allowedFileTypes")
                or item.get("allowed_file_types")
                or ["pdf", "docx", "zip"]
            )
        ]
        original = file.filename or "submission"
        ext = _normalise_ext(os.path.splitext(original)[1])

        if allowed and ext not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Only {', '.join(allowed)} files are accepted",
            )

        max_mb = int(item.get("maxFileSizeMb") or item.get("max_file_size_mb") or 25)
        content = await file.read()
        if len(content) > max_mb * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"File is larger than the {max_mb} MB limit",
            )
        if not content:
            raise HTTPException(status_code=400, detail="The file is empty")

        # ---- store ---------------------------------------------------------
        sub_dir = os.path.join("uploads", "submissions", collection)
        os.makedirs(sub_dir, exist_ok=True)

        fname = f"{uuid.uuid4()}{os.path.splitext(original)[1]}"
        with open(os.path.join(sub_dir, fname), "wb") as buf:
            buf.write(content)

        doc = {
            id_field: item_id,
            "kind": collection,
            "courseId": item.get("courseId", ""),
            "studentId": student_id,
            "studentEmail": user.get("email", ""),
            "studentName": user.get("name") or user.get("fullName") or "",
            "status": "submitted",
            "submittedAt": now,
            "createdAt": now,
            "attemptNumber": previous + 1,
            "files": [
                {
                    "id": str(uuid.uuid4()),
                    "name": original,
                    "kind": _guess_kind(original),
                    "size": len(content),
                    "url": f"/uploads/submissions/{collection}/{fname}",
                }
            ],
            "score": None,
            "feedback": None,
            "passFail": None,
        }

        result = await db[sub_coll].insert_one(doc)
        doc["_id"] = result.inserted_id

        return {"data": _submission_row(doc, id_field), "message": "submitted"}

    @router.get("/{item_id}/submissions/me")
    async def my_submission(
        item_id: str,
        studentId: Optional[str] = Query(None),
        user: dict = Depends(get_current_user),
    ):
        """The signed-in student's own latest attempt, or null."""
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")

        sub_coll = _SUB_COLLECTION.get(collection, "submissions")
        id_field = _SUB_ID_FIELD.get(collection, "assignmentId")

        doc = await db[sub_coll].find_one(
            {id_field: item_id, "studentId": str(user["_id"])},
            sort=[("attemptNumber", -1)],
        )
        if not doc:
            return {"data": None, "message": "no submission"}

        return {
            "data": _submission_row(
                doc, id_field, for_student=(user.get("role") == "student")
            ),
            "message": "ok",
        }

    @router.get("/{item_id}/submissions")
    async def list_submissions(
        item_id: str,
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        db = database.db
        sub_coll = _SUB_COLLECTION.get(collection, "submissions")
        id_field = _SUB_ID_FIELD.get(collection, "assignmentId")

        cursor = db[sub_coll].find({id_field: item_id})
        rows = [_submission_row(s, id_field) async for s in cursor]
        return {"data": rows, "message": "ok"}

    @router.post("/{item_id}/submissions/release-marks")
    async def release_marks(
        item_id: str,
        user: dict = Depends(require_roles("instructor", "admin")),
    ):
        """Unhide marks for every student on this item (bulk release)."""
        db = database.db
        if not ObjectId.is_valid(item_id):
            raise HTTPException(status_code=400, detail="Invalid id")

        item = await db[collection].find_one({"_id": ObjectId(item_id)})
        if not item:
            raise HTTPException(status_code=404, detail="Not found")

        if user["role"] == "instructor":
            owner = item.get("instructorId") or item.get("createdBy")
            if owner and owner != str(user["_id"]):
                raise HTTPException(status_code=403, detail="Not your item")

        sub_coll = _SUB_COLLECTION.get(collection, "submissions")
        id_field = _SUB_ID_FIELD.get(collection, "assignmentId")

        result = await db[sub_coll].update_many(
            {
                id_field: item_id,
                "marksHidden": True,
            },
            {"$set": {"marksHidden": False}},
        )

        return {
            "data": {"updated": result.modified_count},
            "message": f"Released marks for {result.modified_count} student(s)",
        }

    return router


assignments_router = create_coursework_router(
    "/assignments", "assignments", ["assignments"]
)
quizzes_router = create_coursework_router("/quizzes", "quizzes", ["quizzes"])
exams_router = create_coursework_router("/exams", "exams", ["exams"])
projects_router = create_coursework_router(
    "/projects", "projects", ["projects"]
)

submissions_router = APIRouter(prefix="/submissions", tags=["submissions"])


class GradePayload(BaseModel):
    marksAwarded: Optional[float] = None
    score: Optional[float] = None
    feedback: str = ""
    passFail: Optional[Literal["pass", "fail"]] = None
    # When True, student sees "Not graded yet" until instructor unhides
    hideMarks: bool = False


@submissions_router.patch("/{submission_id}/grade")
async def grade_submission(
    submission_id: str,
    payload: GradePayload,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Awards marks. The submission may live in any of the three collections,
    so each is tried in turn rather than asking the caller which one it is."""
    db = database.db
    if not ObjectId.is_valid(submission_id):
        raise HTTPException(status_code=400, detail="Invalid id")

    oid = ObjectId(submission_id)
    score = payload.marksAwarded if payload.marksAwarded is not None else payload.score
    if score is None:
        raise HTTPException(status_code=400, detail="marksAwarded is required")

    for sub_coll, id_field in (
        ("submissions", "assignmentId"),
        ("quiz_attempts", "quizId"),
        ("exam_submissions", "examId"),
    ):
        doc = await db[sub_coll].find_one({"_id": oid})
        if not doc:
            continue

        await db[sub_coll].update_one(
            {"_id": oid},
            {
                "$set": {
                    "score": float(score),
                    "marksAwarded": float(score),
                    "feedback": payload.feedback,
                    "passFail": payload.passFail,
                    "status": "graded",
                    "marksHidden": bool(payload.hideMarks),
                    "gradedAt": datetime.utcnow(),
                    "gradedBy": str(user["_id"]),
                }
            },
        )
        updated = await db[sub_coll].find_one({"_id": oid})
        return {"data": _submission_row(updated, id_field), "message": "graded"}

    raise HTTPException(status_code=404, detail="Submission not found")


@submissions_router.get("/me")
async def my_submissions(
    studentId: Optional[str] = Query(None),
    user: dict = Depends(require_roles("student", "admin")),
):
    db = database.db
    sid = str(user["_id"])
    for_student = user.get("role") == "student"
    results = []
    for coll, id_field in [
        ("submissions", "assignmentId"),
        ("quiz_attempts", "quizId"),
        ("exam_submissions", "examId"),
    ]:
        cursor = db[coll].find({"studentId": sid})
        async for s in cursor:
            results.append(
                _submission_row(s, id_field, for_student=for_student)
            )
    return {"data": results, "message": "ok"}




# """
# Unified coursework API for assignments, quizzes, exams and projects.

# Matches the frontend contract in frontend/src/lib/api/coursework.ts:
# - List returns a raw array of list-items (course joined + counts)
# - Detail returns a single list-item
# - Create/update/status return the entity
# - Students only see published items from courses they are enrolled in
# - Instructors see items they own
# """

# from __future__ import annotations

# import os
# import shutil
# import uuid
# from datetime import datetime
# from typing import Any, Literal, Optional

# from bson import ObjectId
# from bson.errors import InvalidId
# from fastapi import (
#     APIRouter,
#     Depends,
#     File,
#     HTTPException,
#     Query,
#     UploadFile,
# )
# from pydantic import BaseModel, Field

# from app.api.deps import get_current_user, require_roles
# from app.core.database import database

# Status = Literal["draft", "published", "archived"]


# class CourseworkPayload(BaseModel):
#     title: str = Field(min_length=1)
#     description: str = ""
#     objectives: list[str] = Field(default_factory=list)
#     instructions: str = ""
#     courseId: str
#     deadline: Optional[str] = None
#     totalMarks: float = 100
#     allowedFileTypes: list[str] = Field(
#         default_factory=lambda: ["pdf", "docx", "zip"]
#     )
#     maxFileSizeMb: int = 25
#     resubmissionAllowed: bool = False
#     maxAttempts: int = 1
#     status: Status = "draft"
#     instructorId: Optional[str] = None
#     timeLimit: Optional[int] = None
#     passingScore: Optional[float] = None
#     questions: list[dict] = Field(default_factory=list)


# class StatusPayload(BaseModel):
#     status: Status


# def _oid(value: str) -> ObjectId:
#     try:
#         return ObjectId(value)
#     except (InvalidId, TypeError):
#         raise HTTPException(status_code=400, detail="Invalid id")


# def _guess_kind(filename: str) -> str:
#     ext = os.path.splitext(filename)[1].lower().lstrip(".")
#     if ext == "pdf":
#         return "pdf"
#     if ext in ("doc", "docx"):
#         return "docx"
#     if ext in ("png", "jpg", "jpeg", "gif", "webp"):
#         return "image"
#     if ext in ("zip", "rar", "7z"):
#         return "zip"
#     return "other"


# def _to_iso(value: Any) -> Optional[str]:
#     if value is None:
#         return None
#     if isinstance(value, datetime):
#         return value.isoformat() + ("Z" if value.tzinfo is None else "")
#     return str(value)


# async def _join_course(db, course_id: str) -> dict:
#     course = None
#     if course_id and ObjectId.is_valid(course_id):
#         course = await db.courses.find_one({"_id": ObjectId(course_id)})
#     if not course:
#         return {"id": course_id or "", "code": "—", "title": "Unassigned"}
#     return {
#         "id": str(course["_id"]),
#         "code": course.get("code") or (course.get("title", "—")[:8]),
#         "title": course.get("title", "Untitled"),
#         "instructorName": course.get("instructorName"),
#     }


# async def _counts(db, collection: str, item_id: str) -> tuple[int, int, int]:
#     item = await db[collection].find_one({"_id": ObjectId(item_id)})
#     enrolled = 0
#     if item and item.get("courseId"):
#         enrolled = await db.enrollments.count_documents(
#             {"courseId": item["courseId"]}
#         )
#     sub_coll = {
#         "assignments": "submissions",
#         "quizzes": "quiz_attempts",
#         "exams": "exam_submissions",
#         "projects": "submissions",
#     }.get(collection, "submissions")
#     id_field = {
#         "assignments": "assignmentId",
#         "quizzes": "quizId",
#         "exams": "examId",
#         "projects": "assignmentId",
#     }.get(collection, "assignmentId")
#     submitted = await db[sub_coll].count_documents({id_field: item_id})
#     graded = await db[sub_coll].count_documents(
#         {id_field: item_id, "score": {"$ne": None}}
#     )
#     graded2 = await db[sub_coll].count_documents(
#         {id_field: item_id, "status": "graded"}
#     )
#     return enrolled, submitted, max(graded, graded2)


# def _doc_to_entity(doc: dict, collection: str) -> dict:
#     deadline = (
#         doc.get("deadline")
#         or doc.get("dueAt")
#         or doc.get("due_at")
#         or doc.get("createdAt")
#     )
#     status = doc.get("status")
#     if status is None:
#         status = "published" if doc.get("isPublished", True) else "draft"

#     attachments = doc.get("attachments") or []
#     if not attachments and doc.get("attachmentUrl"):
#         attachments = [
#             {
#                 "id": "legacy",
#                 "name": os.path.basename(str(doc["attachmentUrl"])),
#                 "kind": _guess_kind(str(doc["attachmentUrl"])),
#                 "size": 0,
#                 "url": str(doc["attachmentUrl"]),
#             }
#         ]
#     if not attachments and doc.get("examFile"):
#         attachments = [
#             {
#                 "id": "examfile",
#                 "name": os.path.basename(str(doc["examFile"])),
#                 "kind": _guess_kind(str(doc["examFile"])),
#                 "size": 0,
#                 "url": str(doc["examFile"]),
#             }
#         ]

#     return {
#         "id": str(doc["_id"]),
#         "title": doc.get("title", ""),
#         "description": doc.get("description", ""),
#         "objectives": doc.get("objectives") or [],
#         "instructions": doc.get("instructions") or doc.get("description", ""),
#         "courseId": doc.get("courseId", ""),
#         "instructorId": doc.get("instructorId") or doc.get("createdBy", ""),
#         "createdAt": _to_iso(doc.get("createdAt")) or datetime.utcnow().isoformat(),
#         "deadline": _to_iso(deadline) or datetime.utcnow().isoformat(),
#         "totalMarks": float(
#             doc.get("totalMarks")
#             or doc.get("maxScore")
#             or doc.get("max_score")
#             or 100
#         ),
#         "allowedFileTypes": doc.get("allowedFileTypes")
#         or doc.get("allowed_file_types")
#         or ["pdf", "docx", "zip"],
#         "maxFileSizeMb": int(
#             doc.get("maxFileSizeMb") or doc.get("max_file_size_mb") or 25
#         ),
#         "resubmissionAllowed": bool(doc.get("resubmissionAllowed", False)),
#         "maxAttempts": int(
#             doc.get("maxAttempts") or doc.get("attemptsAllowed") or 1
#         ),
#         "attachments": attachments,
#         "status": status,
#         "timeLimit": doc.get("timeLimit"),
#         "passingScore": doc.get("passingScore"),
#         "questions": doc.get("questions") or [],
#     }


# async def _to_list_item(db, doc: dict, collection: str) -> dict:
#     entity = _doc_to_entity(doc, collection)
#     course = await _join_course(db, entity["courseId"])
#     enrolled, submitted, graded = await _counts(db, collection, entity["id"])
#     return {
#         **entity,
#         "course": course,
#         "enrolled": enrolled,
#         "submittedCount": submitted,
#         "gradedCount": graded,
#     }


# async def _student_enrolled_course_ids(db, user: dict) -> list[str]:
#     cursor = db.enrollments.find({"userId": str(user["_id"])})
#     return [e["courseId"] async for e in cursor]


# # --------------------------------------------------------------------------
# # Submissions
# # --------------------------------------------------------------------------
# # Assignments and projects intentionally share the `submissions` collection
# # (and the `assignmentId` field), which is how the existing counting and
# # grading code already reads them.

# _SUB_COLLECTION = {
#     "assignments": "submissions",
#     "quizzes": "quiz_attempts",
#     "exams": "exam_submissions",
#     "projects": "submissions",
# }

# _SUB_ID_FIELD = {
#     "assignments": "assignmentId",
#     "quizzes": "quizId",
#     "exams": "examId",
#     "projects": "assignmentId",
# }


# def _parse_dt(value: Any) -> Optional[datetime]:
#     """Deadlines are stored as datetimes by newer writes and as ISO strings
#     by older ones, so both have to be understood."""
#     if isinstance(value, datetime):
#         return value
#     if isinstance(value, str) and value:
#         try:
#             return datetime.fromisoformat(value.replace("Z", "+00:00"))
#         except ValueError:
#             return None
#     return None


# def _normalise_ext(ext: str) -> str:
#     ext = ext.lower().lstrip(".")
#     # A rule of "docx" is meant to cover Word files generally.
#     if ext == "doc":
#         return "docx"
#     if ext in ("jpeg", "jpg"):
#         return "jpg"
#     return ext


# def _submission_row(doc: dict, id_field: str) -> dict:
#     """The exact shape `Submission` in frontend/src/types/assignment.ts expects."""
#     score = doc.get("score")
#     if score is None:
#         score = doc.get("marksAwarded")

#     return {
#         "id": str(doc["_id"]),
#         "assignmentId": doc.get(id_field, ""),
#         "studentId": doc.get("studentId", ""),
#         "studentName": doc.get("studentName"),
#         "studentEmail": doc.get("studentEmail"),
#         "status": doc.get("status", "submitted"),
#         "submittedAt": _to_iso(doc.get("submittedAt") or doc.get("createdAt")),
#         "files": doc.get("files") or [],
#         "attemptNumber": doc.get("attemptNumber", 1),
#         "marksAwarded": score,
#         "feedback": doc.get("feedback"),
#         "passFail": doc.get("passFail"),
#     }


# def _build_query(
#     user: dict,
#     *,
#     search: Optional[str],
#     status: Optional[str],
#     course_id: Optional[str],
#     instructor_id: Optional[str],
#     student_id: Optional[str],
#     enrolled_ids: Optional[list[str]] = None,
# ) -> dict:
#     query: dict[str, Any] = {}

#     if course_id:
#         query["courseId"] = course_id

#     if status and status != "all":
#         if status == "published":
#             query["$or"] = [
#                 {"status": "published"},
#                 {"isPublished": True, "status": {"$exists": False}},
#             ]
#         else:
#             query["status"] = status

#     if user["role"] == "student" or student_id:
#         ids = enrolled_ids or []
#         if course_id:
#             if course_id not in ids:
#                 query["courseId"] = "__none__"
#         else:
#             query["courseId"] = {"$in": ids}
#         query.pop("status", None)
#         query["$or"] = [
#             {"status": "published"},
#             {"isPublished": True, "status": {"$exists": False}},
#             {"status": {"$exists": False}, "isPublished": {"$exists": False}},
#         ]

#     if user["role"] == "instructor" and not student_id:
#         iid = instructor_id or str(user["_id"])
#         query["instructorId"] = iid

#     if user["role"] == "admin" and instructor_id:
#         query["instructorId"] = instructor_id

#     if search:
#         query["$and"] = query.get("$and", []) + [
#             {
#                 "$or": [
#                     {"title": {"$regex": search, "$options": "i"}},
#                     {"description": {"$regex": search, "$options": "i"}},
#                 ]
#             }
#         ]

#     return query


# def _new_doc(payload: CourseworkPayload, user: dict) -> dict:
#     now = datetime.utcnow()
#     instructor_id = payload.instructorId or str(user["_id"])
#     deadline = None
#     if payload.deadline:
#         try:
#             deadline = datetime.fromisoformat(
#                 payload.deadline.replace("Z", "+00:00")
#             )
#         except ValueError:
#             deadline = payload.deadline

#     return {
#         "title": payload.title,
#         "description": payload.description,
#         "objectives": payload.objectives,
#         "instructions": payload.instructions,
#         "courseId": payload.courseId,
#         "instructorId": instructor_id,
#         "deadline": deadline or now,
#         "dueAt": deadline or now,
#         "totalMarks": payload.totalMarks,
#         "maxScore": payload.totalMarks,
#         "allowedFileTypes": payload.allowedFileTypes,
#         "maxFileSizeMb": payload.maxFileSizeMb,
#         "resubmissionAllowed": payload.resubmissionAllowed,
#         "maxAttempts": payload.maxAttempts,
#         "attemptsAllowed": payload.maxAttempts,
#         "status": payload.status,
#         "isPublished": payload.status == "published",
#         "attachments": [],
#         "timeLimit": payload.timeLimit,
#         "passingScore": payload.passingScore,
#         "questions": payload.questions,
#         "createdAt": now,
#         "updatedAt": now,
#         "createdBy": instructor_id,
#     }


# def create_coursework_router(
#     prefix: str,
#     collection: str,
#     tags: list[str],
# ) -> APIRouter:
#     router = APIRouter(prefix=prefix, tags=tags)
#     upload_dir = f"uploads/{collection}"
#     os.makedirs(upload_dir, exist_ok=True)

#     @router.get("")
#     async def list_items(
#         search: Optional[str] = Query(None),
#         status: Optional[str] = Query(None),
#         courseId: Optional[str] = Query(None),
#         instructorId: Optional[str] = Query(None),
#         studentId: Optional[str] = Query(None),
#         user: dict = Depends(get_current_user),
#     ):
#         db = database.db
#         enrolled = None
#         if user["role"] == "student" or studentId:
#             enrolled = await _student_enrolled_course_ids(db, user)

#         query = _build_query(
#             user,
#             search=search,
#             status=status,
#             course_id=courseId,
#             instructor_id=instructorId,
#             student_id=studentId,
#             enrolled_ids=enrolled,
#         )

#         cursor = db[collection].find(query).sort("createdAt", -1)
#         items = []
#         async for doc in cursor:
#             items.append(await _to_list_item(db, doc, collection))
#         return items

#     @router.get("/{item_id}")
#     async def get_item(
#         item_id: str,
#         user: dict = Depends(get_current_user),
#     ):
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")
#         doc = await db[collection].find_one({"_id": ObjectId(item_id)})
#         if not doc:
#             raise HTTPException(status_code=404, detail="Not found")

#         if user["role"] == "student":
#             enrolled = await _student_enrolled_course_ids(db, user)
#             if doc.get("courseId") not in enrolled:
#                 raise HTTPException(status_code=403, detail="Not enrolled")
#             status_val = doc.get("status")
#             published = (
#                 status_val == "published"
#                 or (status_val is None and doc.get("isPublished", True))
#             )
#             if not published:
#                 raise HTTPException(status_code=403, detail="Not published")

#         return await _to_list_item(db, doc, collection)

#     @router.post("")
#     async def create_item(
#         payload: CourseworkPayload,
#         user: dict = Depends(require_roles("instructor", "admin")),
#     ):
#         db = database.db
#         if not ObjectId.is_valid(payload.courseId):
#             raise HTTPException(status_code=400, detail="Invalid courseId")
#         course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
#         if not course:
#             raise HTTPException(status_code=404, detail="Course not found")

#         doc = _new_doc(payload, user)
#         result = await db[collection].insert_one(doc)
#         doc["_id"] = result.inserted_id
#         return _doc_to_entity(doc, collection)

#     @router.patch("/{item_id}")
#     async def update_item(
#         item_id: str,
#         payload: CourseworkPayload,
#         user: dict = Depends(require_roles("instructor", "admin")),
#     ):
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")
#         existing = await db[collection].find_one({"_id": ObjectId(item_id)})
#         if not existing:
#             raise HTTPException(status_code=404, detail="Not found")

#         if user["role"] == "instructor":
#             owner = existing.get("instructorId") or existing.get("createdBy")
#             if owner and owner != str(user["_id"]):
#                 raise HTTPException(status_code=403, detail="Not your item")

#         update = _new_doc(payload, user)
#         update["createdAt"] = existing.get("createdAt", update["createdAt"])
#         update["attachments"] = existing.get("attachments", [])
#         update.pop("createdBy", None)

#         await db[collection].update_one(
#             {"_id": ObjectId(item_id)}, {"$set": update}
#         )
#         doc = await db[collection].find_one({"_id": ObjectId(item_id)})
#         return _doc_to_entity(doc, collection)

#     @router.patch("/{item_id}/status")
#     async def update_status(
#         item_id: str,
#         payload: StatusPayload,
#         user: dict = Depends(require_roles("instructor", "admin")),
#     ):
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")
#         existing = await db[collection].find_one({"_id": ObjectId(item_id)})
#         if not existing:
#             raise HTTPException(status_code=404, detail="Not found")

#         await db[collection].update_one(
#             {"_id": ObjectId(item_id)},
#             {
#                 "$set": {
#                     "status": payload.status,
#                     "isPublished": payload.status == "published",
#                     "updatedAt": datetime.utcnow(),
#                 }
#             },
#         )
#         doc = await db[collection].find_one({"_id": ObjectId(item_id)})
#         return _doc_to_entity(doc, collection)

#     @router.post("/{item_id}/duplicate")
#     async def duplicate_item(
#         item_id: str,
#         user: dict = Depends(require_roles("instructor", "admin")),
#     ):
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")
#         existing = await db[collection].find_one({"_id": ObjectId(item_id)})
#         if not existing:
#             raise HTTPException(status_code=404, detail="Not found")

#         new_doc = {k: v for k, v in existing.items() if k != "_id"}
#         new_doc["title"] = f"{existing.get('title', 'Item')} (Copy)"
#         new_doc["status"] = "draft"
#         new_doc["isPublished"] = False
#         new_doc["createdAt"] = datetime.utcnow()
#         new_doc["updatedAt"] = datetime.utcnow()
#         new_doc["instructorId"] = str(user["_id"])
#         result = await db[collection].insert_one(new_doc)
#         new_doc["_id"] = result.inserted_id
#         return await _to_list_item(db, new_doc, collection)

#     @router.delete("/{item_id}")
#     async def delete_item(
#         item_id: str,
#         user: dict = Depends(require_roles("instructor", "admin")),
#     ):
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")
#         existing = await db[collection].find_one({"_id": ObjectId(item_id)})
#         if not existing:
#             raise HTTPException(status_code=404, detail="Not found")
#         if user["role"] == "instructor":
#             owner = existing.get("instructorId") or existing.get("createdBy")
#             if owner and owner != str(user["_id"]):
#                 raise HTTPException(status_code=403, detail="Not your item")
#         await db[collection].delete_one({"_id": ObjectId(item_id)})
#         return {"data": None, "message": "deleted"}

#     @router.post("/{item_id}/attachments")
#     async def upload_attachment(
#         item_id: str,
#         file: UploadFile = File(...),
#         user: dict = Depends(require_roles("instructor", "admin")),
#     ):
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")
#         existing = await db[collection].find_one({"_id": ObjectId(item_id)})
#         if not existing:
#             raise HTTPException(status_code=404, detail="Not found")

#         ext = os.path.splitext(file.filename or "file")[1]
#         fname = f"{uuid.uuid4()}{ext}"
#         path = os.path.join(upload_dir, fname)
#         with open(path, "wb") as buf:
#             shutil.copyfileobj(file.file, buf)

#         att = {
#             "id": str(uuid.uuid4()),
#             "name": file.filename or fname,
#             "kind": _guess_kind(file.filename or fname),
#             "size": os.path.getsize(path),
#             "url": f"/uploads/{collection}/{fname}",
#         }
#         await db[collection].update_one(
#             {"_id": ObjectId(item_id)},
#             {
#                 "$push": {"attachments": att},
#                 "$set": {"updatedAt": datetime.utcnow()},
#             },
#         )
#         return att

#     @router.delete("/{item_id}/attachments/{attachment_id}")
#     async def delete_attachment(
#         item_id: str,
#         attachment_id: str,
#         user: dict = Depends(require_roles("instructor", "admin")),
#     ):
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")
#         await db[collection].update_one(
#             {"_id": ObjectId(item_id)},
#             {"$pull": {"attachments": {"id": attachment_id}}},
#         )
#         return {"data": None, "message": "deleted"}
#     #changing

    


#     @router.post("/{item_id}/submissions")
#     async def submit_work(
#         item_id: str,
#         file: UploadFile = File(...),
#         studentId: Optional[str] = Query(None),
#         user: dict = Depends(require_roles("student", "admin")),
#     ):
#         """Student hands in one file.

#         `studentId` arrives as a query param from the submission panel, but it
#         is only ever a hint — the owner of the submission is taken from the
#         token so a student cannot submit on someone else's behalf.
#         """
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")

#         item = await db[collection].find_one({"_id": ObjectId(item_id)})
#         if not item:
#             raise HTTPException(status_code=404, detail="Not found")

#         status_val = item.get("status")
#         published = status_val == "published" or (
#             status_val is None and item.get("isPublished", True)
#         )
#         if not published:
#             raise HTTPException(
#                 status_code=403, detail="This item is not open for submissions"
#             )

#         if user["role"] == "student":
#             enrolled = await _student_enrolled_course_ids(db, user)
#             if item.get("courseId") not in enrolled:
#                 raise HTTPException(status_code=403, detail="Not enrolled")

#         sub_coll = _SUB_COLLECTION.get(collection, "submissions")
#         id_field = _SUB_ID_FIELD.get(collection, "assignmentId")
#         student_id = str(user["_id"])

#         # ---- attempt limits ------------------------------------------------
#         previous = await db[sub_coll].count_documents(
#             {id_field: item_id, "studentId": student_id}
#         )
#         resubmission = bool(item.get("resubmissionAllowed", False))
#         max_attempts = int(
#             item.get("maxAttempts") or item.get("attemptsAllowed") or 1
#         )
#         if previous:
#             if not resubmission:
#                 raise HTTPException(
#                     status_code=409,
#                     detail="You have already submitted and resubmission is not allowed",
#                 )
#             if previous >= max_attempts:
#                 raise HTTPException(
#                     status_code=409,
#                     detail=f"No attempts left (limit {max_attempts})",
#                 )

#         # ---- file rules ----------------------------------------------------
#         allowed = [
#             _normalise_ext(t)
#             for t in (
#                 item.get("allowedFileTypes")
#                 or item.get("allowed_file_types")
#                 or ["pdf", "docx", "zip"]
#             )
#         ]
#         original = file.filename or "submission"
#         ext = _normalise_ext(os.path.splitext(original)[1])

#         if allowed and ext not in allowed:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"Only {', '.join(allowed)} files are accepted",
#             )

#         max_mb = int(item.get("maxFileSizeMb") or item.get("max_file_size_mb") or 25)
#         content = await file.read()
#         if len(content) > max_mb * 1024 * 1024:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"File is larger than the {max_mb} MB limit",
#             )
#         if not content:
#             raise HTTPException(status_code=400, detail="The file is empty")

#         # ---- store ---------------------------------------------------------
#         sub_dir = os.path.join("uploads", "submissions", collection)
#         os.makedirs(sub_dir, exist_ok=True)

#         fname = f"{uuid.uuid4()}{os.path.splitext(original)[1]}"
#         with open(os.path.join(sub_dir, fname), "wb") as buf:
#             buf.write(content)

#         now = datetime.utcnow()
#         deadline = _parse_dt(item.get("deadline") or item.get("dueAt"))
#         if deadline is not None and deadline.tzinfo is not None:
#             deadline = deadline.replace(tzinfo=None)

#         doc = {
#             id_field: item_id,
#             "kind": collection,
#             "courseId": item.get("courseId", ""),
#             "studentId": student_id,
#             "studentEmail": user.get("email", ""),
#             "studentName": user.get("name") or user.get("fullName") or "",
#             "status": "late" if deadline and now > deadline else "submitted",
#             "submittedAt": now,
#             "createdAt": now,
#             "attemptNumber": previous + 1,
#             "files": [
#                 {
#                     "id": str(uuid.uuid4()),
#                     "name": original,
#                     "kind": _guess_kind(original),
#                     "size": len(content),
#                     "url": f"/uploads/submissions/{collection}/{fname}",
#                 }
#             ],
#             "score": None,
#             "feedback": None,
#             "passFail": None,
#         }

#         result = await db[sub_coll].insert_one(doc)
#         doc["_id"] = result.inserted_id

#         return {"data": _submission_row(doc, id_field), "message": "submitted"}

#     @router.get("/{item_id}/submissions/me")
#     async def my_submission(
#         item_id: str,
#         studentId: Optional[str] = Query(None),
#         user: dict = Depends(get_current_user),
#     ):
#         """The signed-in student's own latest attempt, or null."""
#         db = database.db
#         if not ObjectId.is_valid(item_id):
#             raise HTTPException(status_code=400, detail="Invalid id")

#         sub_coll = _SUB_COLLECTION.get(collection, "submissions")
#         id_field = _SUB_ID_FIELD.get(collection, "assignmentId")

#         doc = await db[sub_coll].find_one(
#             {id_field: item_id, "studentId": str(user["_id"])},
#             sort=[("attemptNumber", -1)],
#         )
#         if not doc:
#             return {"data": None, "message": "no submission"}

#         return {"data": _submission_row(doc, id_field), "message": "ok"}

#     @router.get("/{item_id}/submissions")
#     async def list_submissions(
#         item_id: str,
#         user: dict = Depends(require_roles("instructor", "admin")),
#     ):
#         db = database.db
#         sub_coll = _SUB_COLLECTION.get(collection, "submissions")
#         id_field = _SUB_ID_FIELD.get(collection, "assignmentId")

#         cursor = db[sub_coll].find({id_field: item_id})
#         rows = [_submission_row(s, id_field) async for s in cursor]
#         return {"data": rows, "message": "ok"}

#     return router


# assignments_router = create_coursework_router(
#     "/assignments", "assignments", ["assignments"]
# )
# quizzes_router = create_coursework_router("/quizzes", "quizzes", ["quizzes"])
# exams_router = create_coursework_router("/exams", "exams", ["exams"])
# projects_router = create_coursework_router(
#     "/projects", "projects", ["projects"]
# )

# submissions_router = APIRouter(prefix="/submissions", tags=["submissions"])


# class GradePayload(BaseModel):
#     marksAwarded: Optional[float] = None
#     score: Optional[float] = None
#     feedback: str = ""
#     passFail: Optional[Literal["pass", "fail"]] = None


# @submissions_router.patch("/{submission_id}/grade")
# async def grade_submission(
#     submission_id: str,
#     payload: GradePayload,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     """Awards marks. The submission may live in any of the three collections,
#     so each is tried in turn rather than asking the caller which one it is."""
#     db = database.db
#     if not ObjectId.is_valid(submission_id):
#         raise HTTPException(status_code=400, detail="Invalid id")

#     oid = ObjectId(submission_id)
#     score = payload.marksAwarded if payload.marksAwarded is not None else payload.score
#     if score is None:
#         raise HTTPException(status_code=400, detail="marksAwarded is required")

#     for sub_coll, id_field in (
#         ("submissions", "assignmentId"),
#         ("quiz_attempts", "quizId"),
#         ("exam_submissions", "examId"),
#     ):
#         doc = await db[sub_coll].find_one({"_id": oid})
#         if not doc:
#             continue

#         await db[sub_coll].update_one(
#             {"_id": oid},
#             {
#                 "$set": {
#                     "score": float(score),
#                     "marksAwarded": float(score),
#                     "feedback": payload.feedback,
#                     "passFail": payload.passFail,
#                     "status": "graded",
#                     "gradedAt": datetime.utcnow(),
#                     "gradedBy": str(user["_id"]),
#                 }
#             },
#         )
#         updated = await db[sub_coll].find_one({"_id": oid})
#         return {"data": _submission_row(updated, id_field), "message": "graded"}

#     raise HTTPException(status_code=404, detail="Submission not found")


# @submissions_router.get("/me")
# async def my_submissions(
#     studentId: Optional[str] = Query(None),
#     user: dict = Depends(require_roles("student", "admin")),
# ):
#     db = database.db
#     sid = str(user["_id"])
#     results = []
#     for coll, id_field in [
#         ("submissions", "assignmentId"),
#         ("quiz_attempts", "quizId"),
#         ("exam_submissions", "examId"),
#     ]:
#         cursor = db[coll].find({"studentId": sid})
#         async for s in cursor:
#             results.append(
#                 {
#                     "id": str(s["_id"]),
#                     "assignmentId": s.get(id_field)
#                     or s.get("assignmentId", ""),
#                     "studentId": sid,
#                     "status": s.get("status", "submitted"),
#                     "submittedAt": _to_iso(
#                         s.get("submittedAt") or s.get("createdAt")
#                     ),
#                     "files": s.get("files") or [],
#                     "attemptNumber": s.get("attemptNumber", 1),
#                     "marksAwarded": s.get("score")
#                     if s.get("score") is not None
#                     else s.get("marksAwarded"),
#                     "feedback": s.get("feedback"),
#                     "passFail": s.get("passFail"),
#                 }
#             )
#     return {"data": results, "message": "ok"}








