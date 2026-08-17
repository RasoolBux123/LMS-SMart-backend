from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from datetime import datetime
from app.core.database import database
from app.api.deps import require_roles, get_current_user
from app.models.module import new_module_doc, module_to_public
from app.models.material import material_to_public
from app.schemas.module import CreateModuleRequest, UpdateModuleRequest

router = APIRouter(prefix="/modules", tags=["modules"])


@router.post("")
async def create_module(
    payload: CreateModuleRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if user["role"] == "instructor" and course["instructorId"] != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You don't own this course")
    doc = new_module_doc(
        course_id=payload.courseId,
        title=payload.title,
        description=payload.description,
        order=payload.order,
        is_published=payload.isPublished,
    )
    result = await db.modules.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "data": module_to_public(doc), "message": "Module created"}


@router.get("/course/{course_id}")
async def get_course_modules(
    course_id: str,
    include_materials: bool = False,
    user: dict = Depends(get_current_user),
):
    db = database.db
    cursor = db.modules.find({"courseId": course_id}).sort("order", 1)
    modules = []
    async for module in cursor:
        module_data = module_to_public(module)
        if include_materials:
            materials_cursor = db.materials.find({"moduleId": str(module["_id"])}).sort(
                "order", 1
            )
            materials = [material_to_public(m) async for m in materials_cursor]
            module_data["materials"] = materials
            module_data["totalMaterials"] = len(materials)
        modules.append(module_data)
    return {"success": True, "data": modules, "message": "ok"}


@router.get("/{module_id}")
async def get_module(
    module_id: str,
    include_materials: bool = True,
    user: dict = Depends(get_current_user),
):
    db = database.db
    module = await db.modules.find_one({"_id": ObjectId(module_id)})
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    module_data = module_to_public(module)
    if include_materials:
        materials_cursor = db.materials.find({"moduleId": module_id}).sort("order", 1)
        materials = [material_to_public(m) async for m in materials_cursor]
        module_data["materials"] = materials
        module_data["totalMaterials"] = len(materials)
    return {"success": True, "data": module_data, "message": "ok"}


@router.patch("/{module_id}")
async def update_module(
    module_id: str,
    payload: UpdateModuleRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    module = await db.modules.find_one({"_id": ObjectId(module_id)})
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    course = await db.courses.find_one({"_id": ObjectId(module["courseId"])})
    if user["role"] == "instructor" and course["instructorId"] != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You don't own this course")
    update_data = {
        k: v for k, v in payload.dict(exclude_unset=True).items() if v is not None
    }
    update_data["updatedAt"] = datetime.utcnow()
    if update_data:
        await db.modules.update_one({"_id": ObjectId(module_id)}, {"$set": update_data})
    updated = await db.modules.find_one({"_id": ObjectId(module_id)})
    return {
        "success": True,
        "data": module_to_public(updated),
        "message": "Module updated",
    }


@router.delete("/{module_id}")
async def delete_module(
    module_id: str, user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db
    module = await db.modules.find_one({"_id": ObjectId(module_id)})
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    await db.modules.delete_one({"_id": ObjectId(module_id)})
    await db.materials.delete_many({"moduleId": module_id})
    return {"success": True, "data": None, "message": "Module deleted"}


@router.patch("/reorder")
async def reorder_modules(
    module_ids: list[str], user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db
    for i, module_id in enumerate(module_ids):
        if ObjectId.is_valid(module_id):
            await db.modules.update_one(
                {"_id": ObjectId(module_id)},
                {"$set": {"order": i, "updatedAt": datetime.utcnow()}},
            )
    return {"success": True, "data": None, "message": "Modules reordered"}
