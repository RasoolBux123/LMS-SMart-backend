"""
Deadline reminders.

n8n calls this on a schedule (once a day) and asks: what is due in the next N
hours, and who hasn't been reminded yet? The reply carries everything the
workflow needs to write and send the emails — including the recipient list, for
the same reason as the publish webhook: enrolment lives in the `enrollments`
collection, not on the user document.

Each item is stamped with `reminderSentAt` as it goes out, so a student is
never reminded twice about the same assignment even if the schedule fires
several times a day.

Auth is the shared secret header, not a JWT — there is no signed-in user behind
a cron job.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import settings
from app.core.database import database
from app.services.n8n_webhook import (
    KIND_LABEL,
    KIND_STUDENT_PATH,
    _iso,
    _recipients,
)

router = APIRouter(prefix="/reminders", tags=["reminders"])

# collection → singular kind
COLLECTIONS = {
    "assignments": "assignment",
    "quizzes": "quiz",
    "exams": "exam",
    "projects": "project",
}


def _check_secret(provided: Optional[str]) -> None:
    expected = (settings.n8n_webhook_secret or "").strip()
    if not expected:
        return  # secret not configured — leave the endpoint open, as before
    if provided != expected:
        raise HTTPException(status_code=401, detail="Bad or missing secret header")


def _parse_dt(value: Any) -> Optional[datetime]:
    """Deadlines are datetimes on newer writes and ISO strings on older ones."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            return None
    return None


def _is_published(doc: dict) -> bool:
    status = doc.get("status")
    if status is None:
        return bool(doc.get("isPublished", False))
    return status == "published"


@router.post("/due-soon")
async def due_soon(
    withinHours: int = Query(24, ge=1, le=168),
    withinMinutes: Optional[int] = Query(None, ge=1, le=10080),
    label: str = Query("default"),
    dryRun: bool = Query(False),
    x_smartlms_secret: Optional[str] = Header(None, alias="X-SmartLMS-Secret"),
):
    """
    Everything published whose deadline lands inside the window and that hasn't
    been reminded about yet.

    `withinMinutes` wins over `withinHours` when both are given — that's how a
    "10 minutes before" reminder is expressed.

    `label` keeps separate windows from cancelling each other out. A day-ahead
    workflow (label omitted) and a last-call workflow (label=final) each stamp
    their own field, so an item reminded about yesterday can still trigger the
    ten-minute warning today.

    Pass `dryRun=true` while testing — it returns the same payload without
    stamping anything, so you can run it as often as you like.
    """
    _check_secret(x_smartlms_secret)

    db = database.db
    now = datetime.utcnow()
    window = (
        timedelta(minutes=withinMinutes)
        if withinMinutes is not None
        else timedelta(hours=withinHours)
    )
    horizon = now + window

    # Legacy field name for the default window, namespaced for the rest.
    stamp_field = "reminderSentAt" if label == "default" else f"remindersSent.{label}"
    frontend = (settings.frontend_url or "http://localhost:3000").rstrip("/")

    events: list[dict] = []
    scanned = 0
    skipped_no_students = 0

    for collection, kind in COLLECTIONS.items():
        cursor = db[collection].find({stamp_field: {"$exists": False}})

        async for item in cursor:
            scanned += 1
            if not _is_published(item):
                continue

            deadline = _parse_dt(item.get("deadline") or item.get("dueAt"))
            if deadline is None:
                continue
            # already gone, or still further out than the window
            if deadline <= now or deadline > horizon:
                continue

            course_id = str(item.get("courseId") or "")
            if not course_id:
                continue

            recipients = await _recipients(db, course_id)
            if not recipients:
                skipped_no_students += 1
                continue

            course = None
            if ObjectId.is_valid(course_id):
                course = await db.courses.find_one({"_id": ObjectId(course_id)})

            instructor_name = "Your instructor"
            instructor_id = str(item.get("instructorId") or item.get("createdBy") or "")
            if instructor_id and ObjectId.is_valid(instructor_id):
                instructor = await db.users.find_one({"_id": ObjectId(instructor_id)})
                if instructor:
                    instructor_name = instructor.get("name") or instructor_name

            seconds_left = max(0, (deadline - now).total_seconds())
            minutes_left = max(0, round(seconds_left / 60))
            hours_left = max(0, round(seconds_left / 3600))

            events.append(
                {
                    "event": "deadline.reminder",
                    "kind": kind,
                    "kindLabel": KIND_LABEL.get(kind, "Item"),
                    "hoursLeft": hours_left,
                    "minutesLeft": minutes_left,
                    "sentAt": _iso(now),
                    "course": {
                        "id": course_id,
                        "title": (course or {}).get("title", "your course"),
                        "code": (course or {}).get("code", ""),
                    },
                    "content": {
                        "id": str(item["_id"]),
                        "title": item.get("title", ""),
                        "description": item.get("description", "")
                        or item.get("instructions", ""),
                        "deadline": _iso(deadline),
                        "totalMarks": item.get("totalMarks")
                        or item.get("maxScore")
                        or 0,
                        "link": f"{frontend}{KIND_STUDENT_PATH.get(kind, '/student')}",
                    },
                    "instructor": {"name": instructor_name},
                    "recipients": recipients,
                    "recipientCount": len(recipients),
                    "_collection": collection,
                }
            )

    # Stamp them so the next run skips these.
    if events and not dryRun:
        for event in events:
            await db[event["_collection"]].update_one(
                {"_id": ObjectId(event["content"]["id"])},
                {"$set": {stamp_field: now}},
            )

    for event in events:
        event.pop("_collection", None)

    window_text = (
        f"{withinMinutes}m" if withinMinutes is not None else f"{withinHours}h"
    )
    print(
        f"[reminders] scanned={scanned} label={label} due<={window_text}"
        f" -> {len(events)} event(s)"
        + (" (dry run)" if dryRun else "")
        + (f", {skipped_no_students} skipped with no students" if skipped_no_students else "")
    )

    return {
        "success": True,
        "data": {"events": events, "count": len(events)},
        "message": f"{len(events)} item(s) due within {window_text}",
    }


@router.post("/reset/{item_id}")
async def reset_reminder(
    item_id: str,
    x_smartlms_secret: Optional[str] = Header(None, alias="X-SmartLMS-Secret"),
):
    """Clear `reminderSentAt` so an item can be reminded about again. Testing aid."""
    _check_secret(x_smartlms_secret)

    if not ObjectId.is_valid(item_id):
        raise HTTPException(status_code=400, detail="Invalid id")

    db = database.db
    for collection in COLLECTIONS:
        result = await db[collection].update_one(
            {"_id": ObjectId(item_id)},
            {"$unset": {"reminderSentAt": "", "remindersSent": ""}},
        )
        if result.matched_count:
            return {"success": True, "data": None, "message": f"reset in {collection}"}

    raise HTTPException(status_code=404, detail="Item not found")