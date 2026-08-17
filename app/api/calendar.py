"""Calendar / timetable API.

Instructors create events for their courses.
Students only see events for courses they are enrolled in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_roles
from app.core.database import database

router = APIRouter(prefix="/calendar", tags=["calendar"])

EventType = Literal["class", "lab", "review", "office_hours", "other"]


class CalendarEventPayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    type: EventType = "class"
    courseId: str
    startAt: str
    endAt: str
    allDay: bool = False
    location: str = ""
    notes: str = ""


def _to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date/time")


def _event_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", ""),
        "type": doc.get("type", "class"),
        "courseId": doc.get("courseId", ""),
        "courseTitle": doc.get("courseTitle", ""),
        "instructorId": doc.get("instructorId", ""),
        "startAt": _to_iso(doc.get("startAt")),
        "endAt": _to_iso(doc.get("endAt")),
        "allDay": bool(doc.get("allDay", False)),
        "location": doc.get("location") or "",
        "notes": doc.get("notes") or "",
        "createdAt": _to_iso(doc.get("createdAt")),
    }


async def _instructor_course_ids(db, user_id: str) -> set[str]:
    ids: set[str] = set()
    cursor = db.courses.find({"instructorId": user_id})
    async for c in cursor:
        ids.add(str(c["_id"]))
    # Also courses where they created coursework
    for coll in ("assignments", "quizzes", "exams", "projects"):
        async for doc in db[coll].find({"instructorId": user_id}, {"courseId": 1}):
            if doc.get("courseId"):
                ids.add(str(doc["courseId"]))
    return ids


async def _student_course_ids(db, user_id: str) -> set[str]:
    ids: set[str] = set()
    cursor = db.enrollments.find({"userId": user_id})
    async for e in cursor:
        if e.get("courseId"):
            ids.add(str(e["courseId"]))
    return ids


@router.get("/events")
async def list_events(
    courseId: Optional[str] = Query(None),
    fromDate: Optional[str] = Query(None),
    toDate: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    db = database.db
    query: dict[str, Any] = {}

    if user["role"] == "instructor":
        allowed = await _instructor_course_ids(db, str(user["_id"]))
        if courseId:
            if courseId not in allowed and user["role"] != "admin":
                raise HTTPException(status_code=403, detail="Not your course")
            query["courseId"] = courseId
        else:
            query["courseId"] = {"$in": list(allowed) if allowed else ["__none__"]}
        # Instructors also see their own events by instructorId
        if not courseId:
            query = {
                "$or": [
                    {"courseId": {"$in": list(allowed) if allowed else ["__none__"]}},
                    {"instructorId": str(user["_id"])},
                ]
            }
    elif user["role"] == "student":
        allowed = await _student_course_ids(db, str(user["_id"]))
        if courseId:
            if courseId not in allowed:
                raise HTTPException(status_code=403, detail="Not enrolled")
            query["courseId"] = courseId
        else:
            query["courseId"] = {"$in": list(allowed) if allowed else ["__none__"]}
    elif user["role"] == "admin":
        if courseId:
            query["courseId"] = courseId
    else:
        raise HTTPException(status_code=403, detail="Not authorized")

    if fromDate or toDate:
        time_q: dict[str, Any] = {}
        if fromDate:
            time_q["$gte"] = _parse_dt(fromDate)
        if toDate:
            time_q["$lte"] = _parse_dt(toDate)
        query["startAt"] = time_q

    cursor = db.calendar_events.find(query).sort("startAt", 1)
    items = [_event_public(doc) async for doc in cursor]
    return {"success": True, "data": items, "message": "ok"}


@router.post("/events")
async def create_event(
    payload: CalendarEventPayload,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    if not ObjectId.is_valid(payload.courseId):
        raise HTTPException(status_code=400, detail="Invalid courseId")

    course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    if user["role"] == "instructor":
        allowed = await _instructor_course_ids(db, str(user["_id"]))
        if payload.courseId not in allowed:
            # Also allow if they are the course instructorId
            if str(course.get("instructorId") or "") != str(user["_id"]):
                raise HTTPException(status_code=403, detail="Not your course")

    start = _parse_dt(payload.startAt)
    end = _parse_dt(payload.endAt)
    if end < start:
        raise HTTPException(status_code=400, detail="End must be after start")

    now = datetime.utcnow()
    doc = {
        "title": payload.title.strip(),
        "type": payload.type,
        "courseId": payload.courseId,
        "courseTitle": course.get("title", ""),
        "instructorId": str(user["_id"]),
        "startAt": start,
        "endAt": end,
        "allDay": payload.allDay,
        "location": (payload.location or "").strip(),
        "notes": (payload.notes or "").strip(),
        "createdAt": now,
        "updatedAt": now,
    }
    result = await db.calendar_events.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "data": _event_public(doc), "message": "created"}


@router.patch("/events/{event_id}")
async def update_event(
    event_id: str,
    payload: CalendarEventPayload,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    try:
        oid = ObjectId(event_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")

    existing = await db.calendar_events.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Event not found")

    if user["role"] == "instructor" and existing.get("instructorId") != str(
        user["_id"]
    ):
        raise HTTPException(status_code=403, detail="Not your event")

    if not ObjectId.is_valid(payload.courseId):
        raise HTTPException(status_code=400, detail="Invalid courseId")
    course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    start = _parse_dt(payload.startAt)
    end = _parse_dt(payload.endAt)
    if end < start:
        raise HTTPException(status_code=400, detail="End must be after start")

    update = {
        "title": payload.title.strip(),
        "type": payload.type,
        "courseId": payload.courseId,
        "courseTitle": course.get("title", ""),
        "startAt": start,
        "endAt": end,
        "allDay": payload.allDay,
        "location": (payload.location or "").strip(),
        "notes": (payload.notes or "").strip(),
        "updatedAt": datetime.utcnow(),
    }
    await db.calendar_events.update_one({"_id": oid}, {"$set": update})
    doc = await db.calendar_events.find_one({"_id": oid})
    return {"success": True, "data": _event_public(doc), "message": "updated"}


@router.delete("/events/{event_id}")
async def delete_event(
    event_id: str,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    try:
        oid = ObjectId(event_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")

    existing = await db.calendar_events.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Event not found")

    if user["role"] == "instructor" and existing.get("instructorId") != str(
        user["_id"]
    ):
        raise HTTPException(status_code=403, detail="Not your event")

    await db.calendar_events.delete_one({"_id": oid})
    return {"success": True, "data": None, "message": "deleted"}