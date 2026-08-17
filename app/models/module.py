from datetime import datetime


def new_module_doc(
    course_id: str,
    title: str,
    description: str = "",
    order: int = 0,
    is_published: bool = True,
) -> dict:
    return {
        "courseId": course_id,
        "title": title,
        "description": description,
        "order": order,
        "isPublished": is_published,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }


def module_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "courseId": doc["courseId"],
        "title": doc["title"],
        "description": doc.get("description", ""),
        "order": doc.get("order", 0),
        "isPublished": doc.get("isPublished", True),
        "createdAt": doc["createdAt"],
        "updatedAt": doc.get("updatedAt", doc["createdAt"]),
    }
