"""
n8n outbound webhook.

When an instructor publishes an assignment / quiz / exam / project, this posts a
payload to the SmartLMS n8n workflow, which writes the email copy with an LLM and
sends it to every enrolled student.

The recipient list is built HERE, not in n8n. The workflow's MongoDB node looks
for `course_ids` on the users collection, but SmartLMS keeps enrolment in a
separate `enrollments` collection — so that node would always come back empty.
Sending `recipients` in the payload keeps the source of truth in one place.

Fire-and-forget: a slow or offline n8n must never make an instructor's "Publish"
button hang, and must never fail the request.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

import httpx
from bson import ObjectId

from app.core.config import settings
from app.core.database import database

KIND_COLLECTION = {
    "assignment": "assignments",
    "quiz": "quizzes",
    "exam": "exams",
    "project": "projects",
}

KIND_LABEL = {
    "assignment": "Assignment",
    "quiz": "Quiz",
    "exam": "Exam",
    "project": "Project",
}

KIND_STUDENT_PATH = {
    "assignment": "/student/assignments",
    "quiz": "/student/quizzes",
    "exam": "/student/exams",
    "project": "/student/projects",
}

TIMEOUT_SECONDS = 15.0


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def _first_name(full_name: str) -> str:
    return (full_name or "").strip().split(" ")[0] or "there"


async def _recipients(db, course_id: str) -> list[dict]:
    """Every student enrolled in the course, de-duplicated, with an email."""
    seen: set[str] = set()
    rows: list[dict] = []

    async for enrolment in db.enrollments.find({"courseId": course_id}):
        uid = str(enrolment.get("userId") or "")
        if not uid or uid in seen or not ObjectId.is_valid(uid):
            continue
        seen.add(uid)

        student = await db.users.find_one({"_id": ObjectId(uid)})
        if not student:
            continue
        email = (student.get("email") or "").strip()
        if not email:
            continue
        if student.get("status") not in (None, "active"):
            continue  # suspended accounts don't get mail

        name = student.get("name", "")
        rows.append(
            {
                "studentId": uid,
                "name": name,
                "first_name": _first_name(name),
                "email": email,
            }
        )

    return rows


async def build_payload(
    *,
    kind: str,
    coursework_id: str,
    course_id: str,
    instructor_name: str = "Your instructor",
) -> Optional[dict]:
    db = database.db
    collection = KIND_COLLECTION.get(kind)
    if not collection or not ObjectId.is_valid(coursework_id):
        print(f"[n8n] skipped — unknown kind '{kind}' or bad id '{coursework_id}'")
        return None

    item = await db[collection].find_one({"_id": ObjectId(coursework_id)})
    if not item:
        print(f"[n8n] skipped — {kind} {coursework_id} not found in {collection}")
        return None

    course = None
    if course_id and ObjectId.is_valid(course_id):
        course = await db.courses.find_one({"_id": ObjectId(course_id)})

    recipients = await _recipients(db, course_id)
    if not recipients:
        print(
            f"[n8n] skipped — no enrolled students with an email on course {course_id}"
        )
        return None  # nobody to mail — don't wake n8n up for nothing

    label = KIND_LABEL.get(kind, "Item")
    frontend = (settings.frontend_url or "http://localhost:3000").rstrip("/")
    link = f"{frontend}{KIND_STUDENT_PATH.get(kind, '/student')}"

    return {
        "event": "content.created",
        "kind": kind,
        "kindLabel": label,
        "sentAt": _iso(datetime.utcnow()),
        "course": {
            "id": course_id,
            "title": (course or {}).get("title", "your course"),
            "code": (course or {}).get("code", ""),
        },
        "content": {
            "id": coursework_id,
            "title": item.get("title", ""),
            "description": item.get("description", "") or item.get("instructions", ""),
            "deadline": _iso(item.get("deadline") or item.get("dueAt")),
            "totalMarks": item.get("totalMarks") or item.get("maxScore") or 0,
            "link": link,
        },
        "instructor": {"name": instructor_name},
        "recipients": recipients,
        "recipientCount": len(recipients),
    }


async def _post(payload: dict) -> None:
    url = (settings.n8n_webhook_url or "").strip()
    if not url:
        return

    headers = {"Content-Type": "application/json"}
    if settings.n8n_webhook_secret:
        headers[settings.n8n_webhook_header] = settings.n8n_webhook_secret

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            res = await client.post(url, json=payload, headers=headers)
        if res.status_code >= 400:
            print(f"[n8n] webhook returned {res.status_code}: {res.text[:200]}")
        else:
            print(
                f"[n8n] {payload['kind']} '{payload['content']['title']}' "
                f"→ {payload['recipientCount']} student(s)"
            )
    except Exception as exc:
        # n8n being down is never a reason to break publishing.
        print(f"[n8n] webhook failed: {exc}")


async def trigger_content_created(
    *,
    kind: str,
    coursework_id: str,
    course_id: str,
    instructor_name: str = "Your instructor",
) -> None:
    """Build the payload and post it in the background. Never raises."""
    url = (settings.n8n_webhook_url or "").strip()
    if not url:
        print(
            "[n8n] skipped — N8N_WEBHOOK_URL is empty. "
            "Add it to backend/.env and restart the backend."
        )
        return

    print(f"[n8n] publish event: kind={kind} course={course_id} item={coursework_id}")
    try:
        payload = await build_payload(
            kind=kind,
            coursework_id=coursework_id,
            course_id=course_id,
            instructor_name=instructor_name,
        )
        if not payload:
            return
        print(f"[n8n] posting to {url} for {payload['recipientCount']} student(s)")
        # create_task so the instructor's request returns immediately
        asyncio.create_task(_post(payload))
    except Exception as exc:
        print(f"[n8n] trigger failed: {exc}")