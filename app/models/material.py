"""
Course materials.

Materials hang off a COURSE, not a module — an instructor uploads a file on the
course and every student enrolled in that course sees it. `moduleId` is kept as
an optional field so the older module-scoped materials created by
app/api/modules.py still read back without blowing up.
"""

from datetime import datetime
from typing import Any, Optional

MATERIAL_TYPES = ("file", "link", "text")


def new_material_doc(
    *,
    course_id: str,
    title: str,
    description: str = "",
    type: str = "file",
    url: str = "",
    file_name: str = "",
    file_size: int = 0,
    extension: str = "",
    module_id: str = "",
    uploaded_by: str = "",
    uploaded_by_name: str = "",
) -> dict:
    now = datetime.utcnow()
    return {
        "courseId": course_id,
        "moduleId": module_id,
        "title": title.strip(),
        "description": description.strip(),
        "type": type if type in MATERIAL_TYPES else "file",
        "url": url,
        "fileName": file_name,
        "fileSize": file_size,
        "extension": extension,
        "uploadedBy": uploaded_by,
        "uploadedByName": uploaded_by_name,
        "downloadCount": 0,
        "createdAt": now,
        "updatedAt": now,
    }


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def material_to_public(doc: dict) -> dict:
    # .get() throughout — older module-scoped docs are missing half these keys.
    return {
        "id": str(doc["_id"]),
        "courseId": doc.get("courseId", ""),
        "moduleId": doc.get("moduleId", ""),
        "title": doc.get("title", ""),
        "description": doc.get("description") or doc.get("content", ""),
        "type": doc.get("type", "file"),
        "url": doc.get("url", ""),
        "fileName": doc.get("fileName", ""),
        "fileSize": doc.get("fileSize", 0),
        "extension": doc.get("extension", ""),
        "uploadedBy": doc.get("uploadedBy", ""),
        "uploadedByName": doc.get("uploadedByName", ""),
        "downloadCount": doc.get("downloadCount", 0),
        "createdAt": _iso(doc.get("createdAt")),
        "updatedAt": _iso(doc.get("updatedAt") or doc.get("createdAt")),
    }