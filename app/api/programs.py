
from fastapi import APIRouter, Depends, HTTPException, Query
from bson import ObjectId
from datetime import datetime
from typing import Optional

from app.core.database import database
from app.api.deps import require_roles, get_current_user
from app.models.program import new_program_doc, program_to_public
from app.schemas.program import CreateProgramRequest, UpdateProgramRequest

router = APIRouter(prefix="/programs", tags=["programs"])


async def _find_program(db, program_id: str):
    """Resolve by ObjectId or by string _id / code (supports seed-style ids)."""
    if ObjectId.is_valid(program_id):
        doc = await db.programs.find_one({"_id": ObjectId(program_id)})
        if doc:
            return doc
    # string _id (e.g. "prg-ds") or code match
    doc = await db.programs.find_one({"_id": program_id})
    if doc:
        return doc
    doc = await db.programs.find_one({"code": program_id.upper()})
    return doc


async def _student_count_for_courses(db, course_ids: list) -> int:
    if not course_ids:
        return 0
    # unique students enrolled in any of these courses
    pipeline = [
        {"$match": {"courseId": {"$in": [str(c) for c in course_ids]}}},
        {"$group": {"_id": "$userId"}},
        {"$count": "total"},
    ]
    result = await db.enrollments.aggregate(pipeline).to_list(length=1)
    return result[0]["total"] if result else 0


@router.get("")
async def list_programs(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    db = database.db
    query: dict = {}

    if status and status.lower() != "all":
        query["status"] = status.lower()
    if level and level.lower() != "all":
        query["level"] = level.lower()
    if search and search.strip():
        term = search.strip()
        query["$or"] = [
            {"title": {"$regex": term, "$options": "i"}},
            {"code": {"$regex": term, "$options": "i"}},
            {"description": {"$regex": term, "$options": "i"}},
        ]

    cursor = db.programs.find(query).sort("createdAt", -1)
    programs = []
    async for doc in cursor:
        course_ids = doc.get("courseIds") or []
        student_count = await _student_count_for_courses(db, course_ids)
        programs.append(
            program_to_public(doc, course_count=len(course_ids), student_count=student_count)
        )

    return {"success": True, "data": programs, "message": "ok"}


@router.get("/{program_id}")
async def get_program(
    program_id: str,
    user: dict = Depends(get_current_user),
):
    db = database.db
    doc = await _find_program(db, program_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Program not found")

    course_ids = doc.get("courseIds") or []
    student_count = await _student_count_for_courses(db, course_ids)
    return {
        "success": True,
        "data": program_to_public(doc, course_count=len(course_ids), student_count=student_count),
        "message": "ok",
    }


@router.get("/{program_id}/courses")
async def list_program_courses(
    program_id: str,
    user: dict = Depends(get_current_user),
):
    db = database.db
    doc = await _find_program(db, program_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Program not found")

    course_ids = doc.get("courseIds") or []
    if not course_ids:
        return {"success": True, "data": [], "message": "ok"}

    object_ids = [ObjectId(cid) for cid in course_ids if ObjectId.is_valid(cid)]
    courses = []
    if object_ids:
        async for c in db.courses.find({"_id": {"$in": object_ids}}):
            courses.append(
                {
                    "id": str(c["_id"]),
                    "title": c.get("title", ""),
                    "status": c.get("status", "draft"),
                }
            )

    return {"success": True, "data": courses, "message": "ok"}


@router.post("")
async def create_program(
    payload: CreateProgramRequest,
    user: dict = Depends(require_roles("admin")),
):
    db = database.db

    existing = await db.programs.find_one({"code": payload.code.strip().upper()})
    if existing:
        raise HTTPException(status_code=400, detail="Program code already exists")

    doc = new_program_doc(
        code=payload.code,
        title=payload.title,
        description=payload.description,
        level=payload.level,
        status=payload.status,
        duration_months=payload.durationMonths,
        total_credits=payload.totalCredits,
        coordinator=payload.coordinator or "",
        company=payload.company or "",
        course_ids=payload.courseIds or [],
        color=payload.color or "",
    )
    result = await db.programs.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Notify instructors of courses linked to this program
    course_ids = doc.get("courseIds") or []
    if course_ids and user.get("role") == "admin":
        admin_name = user.get("name") or "Admin"
        program_title = doc.get("title") or "a program"
        try:
            from app.api.notifications import notify_user

            seen = set()
            for cid in course_ids:
                if not ObjectId.is_valid(cid):
                    continue
                course = await db.courses.find_one({"_id": ObjectId(cid)})
                if not course:
                    continue
                iid = str(course.get("instructorId") or "")
                if not iid or iid in seen or iid == str(user["_id"]):
                    continue
                seen.add(iid)
                await notify_user(
                    iid,
                    title=f"Program update: {program_title}",
                    body=f"{admin_name} linked your course “{course.get('title', '')}” to program “{program_title}”.",
                    kind="system",
                    link="/instructor/programs",
                    course_id=cid,
                )
        except Exception as exc:
            print(f"[notifications] program create notify failed: {exc}")

    return {
        "success": True,
        "data": program_to_public(doc),
        "message": "program created",
    }


@router.patch("/{program_id}")
async def update_program(
    program_id: str,
    payload: UpdateProgramRequest,
    user: dict = Depends(require_roles("admin")),
):
    db = database.db
    doc = await _find_program(db, program_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Program not found")

    updates = payload.dict(exclude_unset=True)
    if "code" in updates and updates["code"]:
        updates["code"] = updates["code"].strip().upper()
        clash = await db.programs.find_one(
            {"code": updates["code"], "_id": {"$ne": doc["_id"]}}
        )
        if clash:
            raise HTTPException(status_code=400, detail="Program code already exists")

    if updates:
        updates["updatedAt"] = datetime.utcnow()
        await db.programs.update_one({"_id": doc["_id"]}, {"$set": updates})

    updated = await db.programs.find_one({"_id": doc["_id"]})
    course_ids = updated.get("courseIds") or []
    student_count = await _student_count_for_courses(db, course_ids)

    # Notify instructors of newly linked courses
    if "courseIds" in updates and course_ids:
        old_ids = set(str(x) for x in (doc.get("courseIds") or []))
        new_ids = [cid for cid in course_ids if str(cid) not in old_ids]
        if new_ids:
            admin_name = user.get("name") or "Admin"
            program_title = updated.get("title") or "a program"
            try:
                from app.api.notifications import notify_user

                seen = set()
                for cid in new_ids:
                    if not ObjectId.is_valid(cid):
                        continue
                    course = await db.courses.find_one({"_id": ObjectId(cid)})
                    if not course:
                        continue
                    iid = str(course.get("instructorId") or "")
                    if not iid or iid in seen or iid == str(user["_id"]):
                        continue
                    seen.add(iid)
                    await notify_user(
                        iid,
                        title=f"Program update: {program_title}",
                        body=f"{admin_name} linked your course “{course.get('title', '')}” to program “{program_title}”.",
                        kind="system",
                        link="/instructor/programs",
                        course_id=str(cid),
                    )
            except Exception as exc:
                print(f"[notifications] program update notify failed: {exc}")

    return {
        "success": True,
        "data": program_to_public(
            updated, course_count=len(course_ids), student_count=student_count
        ),
        "message": "program updated",
    }


@router.delete("/{program_id}")
async def delete_program(
    program_id: str,
    user: dict = Depends(require_roles("admin")),
):
    db = database.db
    doc = await _find_program(db, program_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Program not found")

    await db.programs.delete_one({"_id": doc["_id"]})
    return {"success": True, "data": None, "message": "program deleted"}





# from fastapi import APIRouter, Depends, HTTPException, Query
# from bson import ObjectId
# from datetime import datetime
# from typing import Optional

# from app.core.database import database
# from app.api.deps import require_roles, get_current_user
# from app.models.program import new_program_doc, program_to_public
# from app.schemas.program import CreateProgramRequest, UpdateProgramRequest

# router = APIRouter(prefix="/programs", tags=["programs"])


# async def _find_program(db, program_id: str):
#     """Resolve by ObjectId or by string _id / code (supports seed-style ids)."""
#     if ObjectId.is_valid(program_id):
#         doc = await db.programs.find_one({"_id": ObjectId(program_id)})
#         if doc:
#             return doc
#     # string _id (e.g. "prg-ds") or code match
#     doc = await db.programs.find_one({"_id": program_id})
#     if doc:
#         return doc
#     doc = await db.programs.find_one({"code": program_id.upper()})
#     return doc


# async def _student_count_for_courses(db, course_ids: list) -> int:
#     if not course_ids:
#         return 0
#     # unique students enrolled in any of these courses
#     pipeline = [
#         {"$match": {"courseId": {"$in": [str(c) for c in course_ids]}}},
#         {"$group": {"_id": "$userId"}},
#         {"$count": "total"},
#     ]
#     result = await db.enrollments.aggregate(pipeline).to_list(length=1)
#     return result[0]["total"] if result else 0


# @router.get("")
# async def list_programs(
#     search: Optional[str] = Query(None),
#     status: Optional[str] = Query(None),
#     level: Optional[str] = Query(None),
#     user: dict = Depends(get_current_user),
# ):
#     db = database.db
#     query: dict = {}

#     if status and status.lower() != "all":
#         query["status"] = status.lower()
#     if level and level.lower() != "all":
#         query["level"] = level.lower()
#     if search and search.strip():
#         term = search.strip()
#         query["$or"] = [
#             {"title": {"$regex": term, "$options": "i"}},
#             {"code": {"$regex": term, "$options": "i"}},
#             {"description": {"$regex": term, "$options": "i"}},
#         ]

#     cursor = db.programs.find(query).sort("createdAt", -1)
#     programs = []
#     async for doc in cursor:
#         course_ids = doc.get("courseIds") or []
#         student_count = await _student_count_for_courses(db, course_ids)
#         programs.append(
#             program_to_public(doc, course_count=len(course_ids), student_count=student_count)
#         )

#     return {"success": True, "data": programs, "message": "ok"}


# @router.get("/{program_id}")
# async def get_program(
#     program_id: str,
#     user: dict = Depends(get_current_user),
# ):
#     db = database.db
#     doc = await _find_program(db, program_id)
#     if not doc:
#         raise HTTPException(status_code=404, detail="Program not found")

#     course_ids = doc.get("courseIds") or []
#     student_count = await _student_count_for_courses(db, course_ids)
#     return {
#         "success": True,
#         "data": program_to_public(doc, course_count=len(course_ids), student_count=student_count),
#         "message": "ok",
#     }


# @router.get("/{program_id}/courses")
# async def list_program_courses(
#     program_id: str,
#     user: dict = Depends(get_current_user),
# ):
#     db = database.db
#     doc = await _find_program(db, program_id)
#     if not doc:
#         raise HTTPException(status_code=404, detail="Program not found")

#     course_ids = doc.get("courseIds") or []
#     if not course_ids:
#         return {"success": True, "data": [], "message": "ok"}

#     object_ids = [ObjectId(cid) for cid in course_ids if ObjectId.is_valid(cid)]
#     courses = []
#     if object_ids:
#         async for c in db.courses.find({"_id": {"$in": object_ids}}):
#             courses.append(
#                 {
#                     "id": str(c["_id"]),
#                     "title": c.get("title", ""),
#                     "status": c.get("status", "draft"),
#                 }
#             )

#     return {"success": True, "data": courses, "message": "ok"}


# @router.post("")
# async def create_program(
#     payload: CreateProgramRequest,
#     user: dict = Depends(require_roles("admin")),
# ):
#     db = database.db

#     existing = await db.programs.find_one({"code": payload.code.strip().upper()})
#     if existing:
#         raise HTTPException(status_code=400, detail="Program code already exists")

#     doc = new_program_doc(
#         code=payload.code,
#         title=payload.title,
#         description=payload.description,
#         level=payload.level,
#         status=payload.status,
#         duration_months=payload.durationMonths,
#         total_credits=payload.totalCredits,
#         coordinator=payload.coordinator or "",
#         company=payload.company or "",
#         course_ids=payload.courseIds or [],
#         color=payload.color or "",
#     )
#     result = await db.programs.insert_one(doc)
#     doc["_id"] = result.inserted_id

#     return {
#         "success": True,
#         "data": program_to_public(doc),
#         "message": "program created",
#     }


# @router.patch("/{program_id}")
# async def update_program(
#     program_id: str,
#     payload: UpdateProgramRequest,
#     user: dict = Depends(require_roles("admin")),
# ):
#     db = database.db
#     doc = await _find_program(db, program_id)
#     if not doc:
#         raise HTTPException(status_code=404, detail="Program not found")

#     updates = payload.dict(exclude_unset=True)
#     if "code" in updates and updates["code"]:
#         updates["code"] = updates["code"].strip().upper()
#         clash = await db.programs.find_one(
#             {"code": updates["code"], "_id": {"$ne": doc["_id"]}}
#         )
#         if clash:
#             raise HTTPException(status_code=400, detail="Program code already exists")

#     if updates:
#         updates["updatedAt"] = datetime.utcnow()
#         await db.programs.update_one({"_id": doc["_id"]}, {"$set": updates})

#     updated = await db.programs.find_one({"_id": doc["_id"]})
#     course_ids = updated.get("courseIds") or []
#     student_count = await _student_count_for_courses(db, course_ids)

#     return {
#         "success": True,
#         "data": program_to_public(
#             updated, course_count=len(course_ids), student_count=student_count
#         ),
#         "message": "program updated",
#     }


# @router.delete("/{program_id}")
# async def delete_program(
#     program_id: str,
#     user: dict = Depends(require_roles("admin")),
# ):
#     db = database.db
#     doc = await _find_program(db, program_id)
#     if not doc:
#         raise HTTPException(status_code=404, detail="Program not found")

#     await db.programs.delete_one({"_id": doc["_id"]})
#     return {"success": True, "data": None, "message": "program deleted"}



