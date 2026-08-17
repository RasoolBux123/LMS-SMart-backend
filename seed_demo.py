"""
Seed SmartLMS with demo data for all roles.
Usage (from backend/):  python seed_demo.py
"""
import asyncio
from datetime import datetime, timedelta

from app.core.database import database
from app.core.security import get_password_hash
from app.models.user import new_user_doc


DEMO_PASSWORD = "Test123!"


async def upsert_user(db, name: str, email: str, role: str) -> dict:
    email = email.lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        # Ensure password/hash field is correct for login
        await db.users.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "passwordHash": get_password_hash(DEMO_PASSWORD),
                    "status": "active",
                    "role": role,
                    "name": name,
                },
                "$unset": {"password_hash": ""},
            },
        )
        existing = await db.users.find_one({"_id": existing["_id"]})
        print(f"↻ Updated {role}: {email}")
        return existing

    doc = new_user_doc(
        name=name,
        email=email,
        password_hash=get_password_hash(DEMO_PASSWORD),
        role=role,
    )
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    print(f"✅ Created {role}: {email}")
    return doc


async def seed():
    await database.connect()
    db = database.db

    admin = await upsert_user(db, "Super Admin", "admin@smartlms.com", "admin")
    instructor = await upsert_user(db, "Sara Instructor", "instructor@smartlms.com", "instructor")
    instructor2 = await upsert_user(db, "Ali Teacher", "instructor2@smartlms.com", "instructor")
    student1 = await upsert_user(db, "Ahmed Student", "student@smartlms.com", "student")
    student2 = await upsert_user(db, "Fatima Student", "student2@smartlms.com", "student")
    student3 = await upsert_user(db, "Omar Student", "student3@smartlms.com", "student")

    # Clear previous demo courses owned by this instructor (idempotent-ish by title)
    demo_titles = ["Full Stack Web Development", "Data Structures & Algorithms"]
    for title in demo_titles:
        old = await db.courses.find_one({"title": title, "instructorId": str(instructor["_id"])})
        if old:
            cid = str(old["_id"])
            module_ids = [str(m["_id"]) async for m in db.modules.find({"courseId": cid})]
            if module_ids:
                await db.materials.delete_many({"moduleId": {"$in": module_ids}})
            await db.modules.delete_many({"courseId": cid})
            assignment_ids = [str(a["_id"]) async for a in db.assignments.find({"courseId": cid})]
            if assignment_ids:
                await db.submissions.delete_many({"assignmentId": {"$in": assignment_ids}})
            await db.assignments.delete_many({"courseId": cid})
            await db.enrollments.delete_many({"courseId": cid})
            await db.courses.delete_one({"_id": old["_id"]})
            print(f"🧹 Cleared old course: {title}")

    now = datetime.utcnow()

    course1 = {
        "title": "Full Stack Web Development",
        "description": "Learn Next.js, FastAPI, and MongoDB by building SmartLMS features.",
        "instructorId": str(instructor["_id"]),
        "status": "active",
        "createdAt": now,
    }
    c1 = await db.courses.insert_one(course1)
    course1_id = str(c1.inserted_id)

    course2 = {
        "title": "Data Structures & Algorithms",
        "description": "Core CS foundations with quizzes and weekly assignments.",
        "instructorId": str(instructor["_id"]),
        "status": "active",
        "createdAt": now,
    }
    c2 = await db.courses.insert_one(course2)
    course2_id = str(c2.inserted_id)
    print("✅ Created 2 courses")

    # Enroll students
    for sid, cid in [
        (str(student1["_id"]), course1_id),
        (str(student2["_id"]), course1_id),
        (str(student3["_id"]), course1_id),
        (str(student1["_id"]), course2_id),
        (str(student2["_id"]), course2_id),
    ]:
        await db.enrollments.insert_one(
            {
                "courseId": cid,
                "userId": sid,
                "status": "active",
                "enrolledAt": now,
            }
        )
    print("✅ Enrolled students")

    # Modules + materials for course 1
    modules = [
        ("Introduction & Setup", 1, "Welcome to the course. Install Node, Python, and MongoDB Atlas access."),
        ("Backend with FastAPI", 2, "Build auth, courses, and assignment APIs with JWT."),
        ("Frontend with Next.js", 3, "Role dashboards, sidebars, and API integration."),
    ]
    for title, order, content in modules:
        m = await db.modules.insert_one(
            {
                "courseId": course1_id,
                "title": title,
                "orderIndex": order,
                "createdAt": now,
            }
        )
        await db.materials.insert_one(
            {
                "moduleId": str(m.inserted_id),
                "title": f"{title} notes",
                "type": "text",
                "content": content,
                "url": None,
                "createdAt": now,
            }
        )
    print("✅ Modules & materials")

    # Assignment
    a1 = await db.assignments.insert_one(
        {
            "courseId": course1_id,
            "title": "Build Login Page",
            "description": "Implement the SmartLMS login UI and wire it to /auth/login.",
            "type": "assignment",
            "dueAt": now + timedelta(days=7),
            "maxScore": 100,
            "questions": [],
            "createdAt": now,
        }
    )
    # Quiz
    q1 = await db.assignments.insert_one(
        {
            "courseId": course1_id,
            "title": "HTTP & REST Basics",
            "description": "Quick check on API fundamentals.",
            "type": "quiz",
            "dueAt": now + timedelta(days=5),
            "maxScore": 20,
            "questions": [
                {
                    "question": "Which HTTP method creates a resource?",
                    "options": ["GET", "POST", "DELETE", "HEAD"],
                    "correctIndex": 1,
                },
                {
                    "question": "What does JWT commonly carry?",
                    "options": ["Password plaintext", "User claims", "MongoDB URI", "CSS tokens"],
                    "correctIndex": 1,
                },
                {
                    "question": "Which status code means Unauthorized?",
                    "options": ["200", "301", "401", "500"],
                    "correctIndex": 2,
                },
            ],
            "createdAt": now,
        }
    )

    # DSA assignment
    await db.assignments.insert_one(
        {
            "courseId": course2_id,
            "title": "Implement Binary Search",
            "description": "Write binary search and explain time complexity.",
            "type": "assignment",
            "dueAt": now + timedelta(days=10),
            "maxScore": 50,
            "questions": [],
            "createdAt": now,
        }
    )
    print("✅ Assignments & quiz")

    # One graded submission for demo
    await db.submissions.insert_one(
        {
            "assignmentId": str(a1.inserted_id),
            "studentId": str(student1["_id"]),
            "content": "Login page completed with role tabs and JWT storage.",
            "answers": [],
            "submittedAt": now - timedelta(days=1),
            "score": 92,
            "feedback": "Great work — clean UI and correct API wiring.",
            "gradedBy": str(instructor["_id"]),
            "gradedAt": now,
        }
    )
    print("✅ Sample graded submission")

    await database.disconnect()

    print("\n========== DEMO LOGINS (password for all: Test123!) ==========")
    print("Admin:       admin@smartlms.com")
    print("Instructor:  instructor@smartlms.com")
    print("Instructor2: instructor2@smartlms.com")
    print("Student:     student@smartlms.com")
    print("Student2:    student2@smartlms.com")
    print("Student3:    student3@smartlms.com")
    print("=============================================================\n")


if __name__ == "__main__":
    asyncio.run(seed())
