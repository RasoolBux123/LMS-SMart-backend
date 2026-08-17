"""
Certificates.

Admin issues them, students hold them, anyone can verify one from the serial
printed on the PDF — the verify route is deliberately unauthenticated so the
QR code works for an employer who has no SmartLMS account.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import require_roles
from app.core.config import settings
from app.core.database import database
from app.models.certificate import (
    certificate_to_public,
    certificate_to_verification,
    generate_serial,
    grade_for,
    new_certificate_doc,
)
from app.schemas.certificate import (
    BulkIssueRequest,
    IssueCertificateRequest,
    RevokeCertificateRequest,
)
from app.services.certificate_pdf import UPLOAD_DIR, render_certificate

router = APIRouter(prefix="/certificates", tags=["certificates"])

# Same weighting the gradebook uses, so a certificate never contradicts the
# grades page a student is looking at.
BUCKETS = [
    ("assignments", "submissions", "assignmentId", 25),
    ("quizzes", "quiz_attempts", "quizId", 25),
    ("projects", "submissions", "assignmentId", 25),
    ("exams", "exam_submissions", "examId", 25),
]


def _oid(value: str, label: str = "id") -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")


def _verify_url(serial: str) -> str:
    base = (settings.frontend_url or "http://localhost:3000").rstrip("/")
    return f"{base}/verify/{serial}"


# --------------------------------------------------------------------------
# weighted result calculation
# --------------------------------------------------------------------------

async def _course_result(db, course_id: str, student_id: str) -> dict:
    """Weighted score for one student on one course, normalised to 0-100."""
    from app.api.routes.grading import _grade_rows_for  # shared with /grading

    total_weight = 0.0
    weighted = 0.0
    total_marks = 0.0
    obtained_marks = 0.0
    graded_items = 0
    total_items = 0

    for collection, sub_coll, id_field, weight in BUCKETS:
        rows = await _grade_rows_for(
            db,
            collection=collection,
            course_id=course_id,
            student_id=student_id,
            sub_coll=sub_coll,
            id_field=id_field,
        )
        if not rows:
            continue

        bucket_total = sum(r["totalMarks"] for r in rows) or 1
        bucket_obtained = sum(
            (r["obtainedMarks"] or 0) for r in rows if r["obtainedMarks"] is not None
        )
        total_weight += weight
        weighted += (bucket_obtained / bucket_total) * weight
        total_marks += bucket_total
        obtained_marks += bucket_obtained
        total_items += len(rows)
        graded_items += sum(1 for r in rows if r["obtainedMarks"] is not None)

    percentage = (weighted / total_weight * 100) if total_weight else 0.0
    return {
        "percentage": round(percentage, 2),
        "totalMarks": round(total_marks, 2),
        "obtainedMarks": round(obtained_marks, 2),
        "totalItems": total_items,
        "gradedItems": graded_items,
    }


async def _course_context(db, course_id: str) -> dict:
    """Course, its instructor's name and the program it sits under."""
    course = await db.courses.find_one({"_id": _oid(course_id, "courseId")})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    instructor_name = ""
    instructor_id = str(course.get("instructorId") or "")
    if instructor_id and ObjectId.is_valid(instructor_id):
        instructor = await db.users.find_one({"_id": ObjectId(instructor_id)})
        if instructor:
            instructor_name = instructor.get("name", "")

    program_id = ""
    program_title = ""
    program = await db.programs.find_one({"courseIds": course_id})
    if program:
        program_id = str(program["_id"])
        program_title = program.get("title", "")

    return {
        "course": course,
        "courseTitle": course.get("title", ""),
        "instructorName": instructor_name,
        "programId": program_id,
        "programTitle": program_title,
        "durationWeeks": course.get("durationWeeks", 0),
    }


async def _build_certificate(
    db,
    *,
    student: dict,
    ctx: dict,
    course_id: str,
    template: str,
    admin: dict,
    percentage_override: Optional[float] = None,
) -> dict:
    student_id = str(student["_id"])
    result = await _course_result(db, course_id, student_id)
    percentage = (
        float(percentage_override)
        if percentage_override is not None
        else result["percentage"]
    )
    letter, remark = grade_for(percentage)

    serial = generate_serial()
    while await db.certificates.find_one({"serial": serial}):
        serial = generate_serial()

    doc = new_certificate_doc(
        serial=serial,
        student_id=student_id,
        student_name=student.get("name", ""),
        student_email=student.get("email", ""),
        course_id=course_id,
        course_title=ctx["courseTitle"],
        program_id=ctx["programId"],
        program_title=ctx["programTitle"],
        instructor_name=ctx["instructorName"],
        template=template,
        percentage=percentage,
        grade=letter,
        remark=remark,
        total_marks=result["totalMarks"],
        obtained_marks=result["obtainedMarks"],
        duration_weeks=ctx["durationWeeks"],
        completed_at=datetime.utcnow(),
        issued_by=str(admin["_id"]),
        issued_by_name=admin.get("name", "Administration"),
    )

    result_id = await db.certificates.insert_one(doc)
    doc["_id"] = result_id.inserted_id

    try:
        path = render_certificate(doc, _verify_url(serial))
        doc["filePath"] = str(path.relative_to(UPLOAD_DIR))
        doc["fileUrl"] = f"/uploads/certificates/{path.name}"
        await db.certificates.update_one(
            {"_id": doc["_id"]},
            {"$set": {"filePath": doc["filePath"], "fileUrl": doc["fileUrl"]}},
        )
    except Exception as exc:
        # A PDF failure must not lose the record — the download route re-renders.
        print(f"[certificates] render failed for {serial}: {exc}")

    try:
        from app.api.notifications import notify_user

        await notify_user(
            student_id,
            title="Your certificate is ready",
            body=(
                f"A certificate for “{ctx['courseTitle']}” has been issued. "
                f"Certificate no. {serial}."
            ),
            kind="system",
            link="/student/certificates",
            course_id=course_id,
        )
    except Exception as exc:
        print(f"[certificates] notify failed: {exc}")

    return doc


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------

@router.get("")
async def list_certificates(
    studentId: Optional[str] = Query(None),
    courseId: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user: dict = Depends(require_roles("admin", "instructor")),
):
    db = database.db
    query: dict = {}
    if studentId:
        query["studentId"] = studentId
    if courseId:
        query["courseId"] = courseId
    if status in ("issued", "revoked"):
        query["status"] = status
    if search:
        query["$or"] = [
            {"studentName": {"$regex": search, "$options": "i"}},
            {"studentEmail": {"$regex": search, "$options": "i"}},
            {"serial": {"$regex": search, "$options": "i"}},
            {"courseTitle": {"$regex": search, "$options": "i"}},
        ]

    # An instructor only sees certificates on courses they teach.
    if user["role"] == "instructor":
        uid = str(user["_id"])
        own = [str(c["_id"]) async for c in db.courses.find({"instructorId": uid})]
        query["courseId"] = (
            courseId if courseId in own else {"$in": own or ["__none__"]}
        )

    cursor = db.certificates.find(query).sort("issuedAt", -1)
    rows = [certificate_to_public(doc) async for doc in cursor]
    return {"success": True, "data": rows, "message": "ok"}


@router.get("/eligible")
async def eligible_students(
    courseId: str = Query(...),
    user: dict = Depends(require_roles("admin", "instructor")),
):
    """Everyone enrolled on the course, with the score their grade comes from."""
    db = database.db
    ctx = await _course_context(db, courseId)

    issued: dict[str, dict] = {}
    async for cert in db.certificates.find({"courseId": courseId}):
        issued[cert["studentId"]] = cert

    rows = []
    async for enrolment in db.enrollments.find({"courseId": courseId}):
        sid = enrolment.get("userId", "")
        if not ObjectId.is_valid(sid):
            continue
        student = await db.users.find_one({"_id": ObjectId(sid), "role": "student"})
        if not student:
            continue

        result = await _course_result(db, courseId, sid)
        letter, remark = grade_for(result["percentage"])
        existing = issued.get(sid)

        rows.append(
            {
                "studentId": sid,
                "name": student.get("name", ""),
                "email": student.get("email", ""),
                "percentage": result["percentage"],
                "grade": letter,
                "remark": remark,
                "gradedItems": result["gradedItems"],
                "totalItems": result["totalItems"],
                "certificateId": str(existing["_id"]) if existing else None,
                "certificateSerial": existing.get("serial") if existing else None,
                "certificateStatus": existing.get("status") if existing else None,
            }
        )

    rows.sort(key=lambda r: (-r["percentage"], r["name"].lower()))
    return {
        "success": True,
        "data": {
            "courseId": courseId,
            "courseTitle": ctx["courseTitle"],
            "instructorName": ctx["instructorName"],
            "programTitle": ctx["programTitle"],
            "students": rows,
        },
        "message": "ok",
    }


@router.get("/me")
async def my_certificates(user: dict = Depends(require_roles("student"))):
    db = database.db
    cursor = db.certificates.find(
        {"studentId": str(user["_id"]), "status": "issued"}
    ).sort("issuedAt", -1)
    rows = [certificate_to_public(doc) async for doc in cursor]
    return {"success": True, "data": rows, "message": "ok"}


@router.get("/verify/{serial}")
async def verify_certificate(serial: str):
    """Public — no token. This is what the printed QR code points at."""
    db = database.db
    doc = await db.certificates.find_one({"serial": serial.strip().upper()})
    if not doc:
        raise HTTPException(status_code=404, detail="No certificate with that number")
    return {"success": True, "data": certificate_to_verification(doc), "message": "ok"}


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------

@router.post("")
async def issue_certificate(
    payload: IssueCertificateRequest,
    admin: dict = Depends(require_roles("admin")),
):
    db = database.db
    student = await db.users.find_one(
        {"_id": _oid(payload.studentId, "studentId"), "role": "student"}
    )
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    enrolled = await db.enrollments.find_one(
        {"courseId": payload.courseId, "userId": payload.studentId}
    )
    if not enrolled:
        raise HTTPException(
            status_code=400, detail="This student isn't enrolled in that course"
        )

    duplicate = await db.certificates.find_one(
        {"courseId": payload.courseId, "studentId": payload.studentId, "status": "issued"}
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Already issued as {duplicate.get('serial')}. Revoke it first to re-issue.",
        )

    ctx = await _course_context(db, payload.courseId)
    doc = await _build_certificate(
        db,
        student=student,
        ctx=ctx,
        course_id=payload.courseId,
        template=payload.template,
        admin=admin,
        percentage_override=payload.percentageOverride,
    )
    return {"success": True, "data": certificate_to_public(doc), "message": "issued"}


@router.post("/bulk")
async def bulk_issue(
    payload: BulkIssueRequest,
    admin: dict = Depends(require_roles("admin")),
):
    """Issue for a whole course in one go. Skips anyone already holding one."""
    db = database.db
    ctx = await _course_context(db, payload.courseId)

    targets = payload.studentIds
    if not targets:
        async for enrolment in db.enrollments.find({"courseId": payload.courseId}):
            targets.append(enrolment.get("userId", ""))

    created, skipped = [], []
    for sid in targets:
        if not ObjectId.is_valid(sid):
            continue
        student = await db.users.find_one({"_id": ObjectId(sid), "role": "student"})
        if not student:
            continue

        existing = await db.certificates.find_one(
            {"courseId": payload.courseId, "studentId": sid, "status": "issued"}
        )
        if existing:
            if not payload.overwrite:
                skipped.append({"name": student.get("name", ""), "reason": "already issued"})
                continue
            await db.certificates.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "status": "revoked",
                        "revokedAt": datetime.utcnow(),
                        "revokedReason": "Replaced by a re-issued certificate",
                        "updatedAt": datetime.utcnow(),
                    }
                },
            )

        doc = await _build_certificate(
            db,
            student=student,
            ctx=ctx,
            course_id=payload.courseId,
            template=payload.template,
            admin=admin,
        )
        created.append(certificate_to_public(doc))

    return {
        "success": True,
        "data": {"issued": created, "skipped": skipped},
        "message": f"{len(created)} issued, {len(skipped)} skipped",
    }


@router.post("/branding/logo")
async def upload_logo(
    file: UploadFile = File(...),
    admin: dict = Depends(require_roles("admin")),
):
    """Replace the logo printed on every certificate issued from now on."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        raise HTTPException(status_code=400, detail="Use a PNG or JPG file")

    target_dir = UPLOAD_DIR / "branding"
    target_dir.mkdir(parents=True, exist_ok=True)
    for old in target_dir.glob("logo.*"):
        old.unlink(missing_ok=True)

    target = target_dir / f"logo{'.png' if ext == '.png' else '.jpg'}"
    target.write_bytes(await file.read())
    return {
        "success": True,
        "data": {"url": f"/uploads/branding/{target.name}"},
        "message": "logo updated",
    }


@router.get("/{certificate_id}")
async def get_certificate(
    certificate_id: str,
    user: dict = Depends(require_roles("admin", "instructor", "student")),
):
    db = database.db
    doc = await db.certificates.find_one({"_id": _oid(certificate_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if user["role"] == "student" and doc["studentId"] != str(user["_id"]):
        raise HTTPException(status_code=403, detail="Not your certificate")
    return {"success": True, "data": certificate_to_public(doc), "message": "ok"}


@router.get("/{certificate_id}/download")
async def download_certificate(
    certificate_id: str,
    user: dict = Depends(require_roles("admin", "instructor", "student")),
):
    db = database.db
    doc = await db.certificates.find_one({"_id": _oid(certificate_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if user["role"] == "student" and doc["studentId"] != str(user["_id"]):
        raise HTTPException(status_code=403, detail="Not your certificate")
    if doc.get("status") == "revoked":
        raise HTTPException(status_code=410, detail="This certificate has been revoked")

    path = UPLOAD_DIR / (doc.get("filePath") or "")
    if not doc.get("filePath") or not path.exists():
        path = render_certificate(doc, _verify_url(doc["serial"]))
        await db.certificates.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "filePath": str(path.relative_to(UPLOAD_DIR)),
                    "fileUrl": f"/uploads/certificates/{path.name}",
                }
            },
        )

    safe_name = (doc.get("studentName") or "student").replace(" ", "_")
    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=f"{safe_name}_{doc['serial']}.pdf",
    )


@router.patch("/{certificate_id}/revoke")
async def revoke_certificate(
    certificate_id: str,
    payload: RevokeCertificateRequest,
    admin: dict = Depends(require_roles("admin")),
):
    db = database.db
    doc = await db.certificates.find_one({"_id": _oid(certificate_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")

    await db.certificates.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "revoked",
                "revokedAt": datetime.utcnow(),
                "revokedReason": payload.reason or "Revoked by administration",
                "updatedAt": datetime.utcnow(),
            }
        },
    )
    doc = await db.certificates.find_one({"_id": doc["_id"]})
    return {"success": True, "data": certificate_to_public(doc), "message": "revoked"}


@router.patch("/{certificate_id}/restore")
async def restore_certificate(
    certificate_id: str,
    admin: dict = Depends(require_roles("admin")),
):
    db = database.db
    doc = await db.certificates.find_one({"_id": _oid(certificate_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")

    clash = await db.certificates.find_one(
        {
            "courseId": doc["courseId"],
            "studentId": doc["studentId"],
            "status": "issued",
            "_id": {"$ne": doc["_id"]},
        }
    )
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"{clash.get('serial')} is already active for this student and course",
        )

    await db.certificates.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "issued",
                "revokedAt": None,
                "revokedReason": "",
                "updatedAt": datetime.utcnow(),
            }
        },
    )
    doc = await db.certificates.find_one({"_id": doc["_id"]})
    return {"success": True, "data": certificate_to_public(doc), "message": "restored"}


@router.delete("/{certificate_id}")
async def delete_certificate(
    certificate_id: str,
    admin: dict = Depends(require_roles("admin")),
):
    db = database.db
    doc = await db.certificates.find_one({"_id": _oid(certificate_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Certificate not found")

    if doc.get("filePath"):
        pdf = UPLOAD_DIR / doc["filePath"]
        if pdf.exists():
            pdf.unlink(missing_ok=True)

    await db.certificates.delete_one({"_id": doc["_id"]})
    return {"success": True, "data": None, "message": "deleted"}