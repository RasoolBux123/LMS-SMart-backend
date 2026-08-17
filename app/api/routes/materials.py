"""
Course materials API.

Instructor uploads a file (any type) with a description against one of their
courses; every student enrolled in that course sees it on their Courses page and
gets a notification. Students can read and download, nothing else.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import get_current_user, require_roles
from app.core.database import database
from app.models.material import material_to_public, new_material_doc
from app.utils.file_upload import UPLOAD_DIR, save_upload_file

router = APIRouter(prefix="/materials", tags=["materials"])


def _oid(value: str, label: str = "id") -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")


async def _course_for(db, course_id: str, user: dict, *, write: bool = False) -> dict:
    """Course the caller is allowed to touch, or 403/404."""
    course = await db.courses.find_one({"_id": _oid(course_id, "courseId")})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    role = user["role"]
    if role == "admin":
        return course

    if role == "instructor":
        if str(course.get("instructorId") or "") != str(user["_id"]):
            raise HTTPException(status_code=403, detail="Not your course")
        return course

    # student
    if write:
        raise HTTPException(status_code=403, detail="Students can't upload materials")
    enrolled = await db.enrollments.find_one(
        {"courseId": course_id, "userId": str(user["_id"])}
    )
    if not enrolled:
        raise HTTPException(status_code=403, detail="You are not enrolled in this course")
    return course


async def _notify_course_students(
    db, *, course_id: str, course_title: str, material_title: str, instructor_name: str
) -> int:
    """One notification per enrolled student. Never raises."""
    try:
        now = datetime.utcnow()
        seen: set[str] = set()
        docs = []
        async for enrolment in db.enrollments.find({"courseId": course_id}):
            uid = str(enrolment.get("userId") or "")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            docs.append(
                {
                    "userId": uid,
                    "title": f"New material: {material_title}",
                    "body": f"{instructor_name} uploaded new material for {course_title}.",
                    "kind": "system",
                    "read": False,
                    "link": "/student/courses",
                    "courseId": course_id,
                    "courseworkId": None,
                    "courseworkKind": None,
                    "createdAt": now,
                }
            )
        if not docs:
            return 0
        result = await db.notifications.insert_many(docs)
        return len(result.inserted_ids)
    except Exception as exc:
        print(f"[materials] notify failed: {exc}")
        return 0


async def _student_course_ids(db, user: dict) -> list[str]:
    return [e["courseId"] async for e in db.enrollments.find({"userId": str(user["_id"])})]


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------

@router.get("")
async def list_materials(
    courseId: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """Materials for one course, or across everything the caller can see."""
    db = database.db

    if courseId:
        await _course_for(db, courseId, user)
        query = {"courseId": courseId}
    elif user["role"] == "admin":
        query = {}
    elif user["role"] == "instructor":
        own = [str(c["_id"]) async for c in db.courses.find({"instructorId": str(user["_id"])})]
        query = {"courseId": {"$in": own or ["__none__"]}}
    else:
        ids = await _student_course_ids(db, user)
        query = {"courseId": {"$in": ids or ["__none__"]}}

    cursor = db.materials.find(query).sort("createdAt", -1)
    rows = [material_to_public(doc) async for doc in cursor]

    # Join the course title so a combined list is readable without a second call.
    course_ids = {r["courseId"] for r in rows if r["courseId"]}
    titles: dict[str, str] = {}
    for cid in course_ids:
        if ObjectId.is_valid(cid):
            course = await db.courses.find_one({"_id": ObjectId(cid)})
            if course:
                titles[cid] = course.get("title", "")
    for r in rows:
        r["courseTitle"] = titles.get(r["courseId"], "")

    return {"success": True, "data": rows, "message": "ok"}


@router.get("/{material_id}/download")
async def download_material(
    material_id: str,
    user: dict = Depends(get_current_user),
):
    db = database.db
    doc = await db.materials.find_one({"_id": _oid(material_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Material not found")
    if doc.get("courseId"):
        await _course_for(db, doc["courseId"], user)

    url = doc.get("url") or ""
    if not url.startswith("/uploads/"):
        raise HTTPException(status_code=400, detail="This material has no file to download")

    path = UPLOAD_DIR / url.replace("/uploads/", "", 1)
    if not path.exists():
        raise HTTPException(status_code=404, detail="The file is missing from the server")

    await db.materials.update_one({"_id": doc["_id"]}, {"$inc": {"downloadCount": 1}})
    return FileResponse(
        str(path),
        filename=doc.get("fileName") or path.name,
        media_type="application/octet-stream",
    )


@router.get("/{material_id}")
async def get_material(
    material_id: str,
    user: dict = Depends(get_current_user),
):
    db = database.db
    doc = await db.materials.find_one({"_id": _oid(material_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Material not found")
    if doc.get("courseId"):
        await _course_for(db, doc["courseId"], user)
    return {"success": True, "data": material_to_public(doc), "message": "ok"}


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------

@router.post("")
async def create_material(
    courseId: str = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    type: str = Form("file"),
    url: str = Form(""),
    moduleId: str = Form(""),
    file: UploadFile = File(None),
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """
    Multipart. Any file type is accepted (the 50 MB cap in
    app/utils/file_upload.py still applies) — no extension whitelist, because a
    course can legitimately need .fig, .ipynb, .sql, .apk and so on.
    """
    db = database.db
    course = await _course_for(db, courseId, user, write=True)

    file_info = None
    if file is not None and file.filename:
        file_info = await save_upload_file(
            file=file,
            subfolder="materials",
            prefix=f"course_{courseId[:8]}",
            allowed_types=None,  # deliberately unrestricted
        )
        kind = "file"
    elif url.strip():
        kind = "link"
    else:
        kind = "text"

    if type in ("file", "link", "text"):
        kind = kind if type == "file" else type

    clean_title = title.strip() or (file_info["original_name"] if file_info else "Untitled material")
    if not description.strip() and kind == "text":
        raise HTTPException(status_code=400, detail="Add a description for a text note")
    if kind == "link" and not url.strip():
        raise HTTPException(status_code=400, detail="Add the link URL")

    doc = new_material_doc(
        course_id=courseId,
        title=clean_title,
        description=description,
        type=kind,
        url=file_info["url"] if file_info else url.strip(),
        file_name=file_info["original_name"] if file_info else "",
        file_size=file_info["size"] if file_info else 0,
        extension=file_info["extension"] if file_info else "",
        module_id=moduleId.strip(),
        uploaded_by=str(user["_id"]),
        uploaded_by_name=user.get("name", ""),
    )

    result = await db.materials.insert_one(doc)
    doc["_id"] = result.inserted_id

    notified = await _notify_course_students(
        db,
        course_id=courseId,
        course_title=course.get("title", "your course"),
        material_title=clean_title,
        instructor_name=user.get("name") or "Your instructor",
    )

    data = material_to_public(doc)
    data["courseTitle"] = course.get("title", "")
    return {
        "success": True,
        "data": data,
        "message": f"Material shared with {notified} student(s)",
    }


@router.patch("/{material_id}")
async def update_material(
    material_id: str,
    title: str = Form(None),
    description: str = Form(None),
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    doc = await db.materials.find_one({"_id": _oid(material_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Material not found")
    if doc.get("courseId"):
        await _course_for(db, doc["courseId"], user, write=True)

    updates: dict = {"updatedAt": datetime.utcnow()}
    if title is not None and title.strip():
        updates["title"] = title.strip()
    if description is not None:
        updates["description"] = description.strip()

    await db.materials.update_one({"_id": doc["_id"]}, {"$set": updates})
    doc = await db.materials.find_one({"_id": doc["_id"]})
    return {"success": True, "data": material_to_public(doc), "message": "updated"}


@router.delete("/{material_id}")
async def delete_material(
    material_id: str,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    doc = await db.materials.find_one({"_id": _oid(material_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Material not found")
    if doc.get("courseId"):
        await _course_for(db, doc["courseId"], user, write=True)

    url = doc.get("url") or ""
    if url.startswith("/uploads/"):
        path = UPLOAD_DIR / url.replace("/uploads/", "", 1)
        if path.exists():
            try:
                os.remove(path)
            except OSError as exc:
                print(f"[materials] could not delete {path}: {exc}")

    await db.materials.delete_one({"_id": doc["_id"]})
    return {"success": True, "data": None, "message": "deleted"}