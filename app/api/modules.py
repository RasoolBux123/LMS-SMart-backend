from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from bson import ObjectId
from datetime import datetime
from typing import Literal, Optional

from app.core.database import database
from app.api.deps import get_current_user, require_roles

router = APIRouter(tags=["modules"])


class CreateModuleRequest(BaseModel):
    title: str = Field(min_length=2)
    orderIndex: int = 0


class CreateMaterialRequest(BaseModel):
    title: str = Field(min_length=2)
    type: Literal["file", "link", "text"] = "text"
    content: str = ""
    url: Optional[str] = None


def module_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "courseId": doc["courseId"],
        "title": doc["title"],
        "orderIndex": doc.get("orderIndex", 0),
    }


def material_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "moduleId": doc["moduleId"],
        "title": doc["title"],
        "type": doc.get("type", "text"),
        "content": doc.get("content", ""),
        "url": doc.get("url"),
    }


async def _assert_course_access(db, course_id: str, user: dict, write: bool = False):
    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if write:
        if user["role"] == "admin":
            return course
        if user["role"] == "instructor" and course.get("instructorId") == str(user["_id"]):
            return course
        raise HTTPException(status_code=403, detail="Forbidden")
    if user["role"] in ("admin", "instructor"):
        if user["role"] == "instructor" and course.get("instructorId") != str(user["_id"]):
            # instructors can only manage own; for read of others deny
            raise HTTPException(status_code=403, detail="Forbidden")
        return course
    enrolled = await db.enrollments.find_one(
        {"courseId": course_id, "userId": str(user["_id"])}
    )
    if not enrolled:
        raise HTTPException(status_code=403, detail="Not enrolled")
    return course


@router.get("/courses/{course_id}/modules")
async def list_modules(course_id: str, user: dict = Depends(get_current_user)):
    db = database.db
    await _assert_course_access(db, course_id, user, write=False)
    cursor = db.modules.find({"courseId": course_id}).sort("orderIndex", 1)
    modules = [module_to_public(m) async for m in cursor]
    return {"success": True, "data": modules, "message": "ok"}


@router.post("/courses/{course_id}/modules")
async def create_module(
    course_id: str,
    payload: CreateModuleRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    await _assert_course_access(db, course_id, user, write=True)
    doc = {
        "courseId": course_id,
        "title": payload.title,
        "orderIndex": payload.orderIndex,
        "createdAt": datetime.utcnow(),
    }
    result = await db.modules.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "data": module_to_public(doc), "message": "module created"}


@router.get("/modules/{module_id}/materials")
async def list_materials(module_id: str, user: dict = Depends(get_current_user)):
    db = database.db
    module = await db.modules.find_one({"_id": ObjectId(module_id)})
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    await _assert_course_access(db, module["courseId"], user, write=False)
    cursor = db.materials.find({"moduleId": module_id}).sort("createdAt", 1)
    materials = [material_to_public(m) async for m in cursor]
    return {"success": True, "data": materials, "message": "ok"}


@router.post("/modules/{module_id}/materials")
async def create_material(
    module_id: str,
    payload: CreateMaterialRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    module = await db.modules.find_one({"_id": ObjectId(module_id)})
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    await _assert_course_access(db, module["courseId"], user, write=True)
    doc = {
        "moduleId": module_id,
        "title": payload.title,
        "type": payload.type,
        "content": payload.content,
        "url": payload.url,
        "createdAt": datetime.utcnow(),
    }
    result = await db.materials.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "data": material_to_public(doc), "message": "material created"}




