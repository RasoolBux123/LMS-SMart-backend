from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import os

from app.core.config import settings
from app.core.database import database

from app.api import auth
from app.api import users
from app.api import courses
from app.api import enrollments
from app.api import modules
from app.api import attendance
from app.api.routes import materials
from app.api.routes import grading
from app.api import programs
from app.api import credentials
from app.api import certificates
from app.api import reminders
from app.api import notifications
from app.api import calendar

# Unified coursework
# assignments / quizzes / exams / projects / submissions
from app.api.coursework import (
    assignments_router,
    quizzes_router,
    exams_router,
    projects_router,
    submissions_router,
)

from app.api import ai_insights


app = FastAPI(
    title="SmartLMS API",
    version="1.1",
)


# ============================================================
# 422 VALIDATION ERROR HANDLER
# ============================================================
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    print("\n")
    print("=" * 70)
    print("422 VALIDATION ERROR")
    print("=" * 70)

    print("URL:", request.url)
    print("METHOD:", request.method)

    print("\nVALIDATION ERRORS:")
    print(exc.errors())

    try:
        body = await request.body()
        print("\nREQUEST BODY:")
        print(body.decode())
    except Exception as e:
        print("\nCould not read request body:")
        print(e)

    print("=" * 70)
    print("\n")

    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors()
        },
    )


# ============================================================
# CREATE UPLOAD DIRECTORIES
# ============================================================
for d in (
    "uploads/exams",
    "uploads/submissions",
    "uploads/assignments",
    "uploads/quizzes",
    "uploads/projects",
    "uploads/materials",
    "uploads/certificates",
    "uploads/branding",
):
    os.makedirs(d, exist_ok=True)


# ============================================================
# STATIC FILES
# ============================================================
app.mount(
    "/uploads",
    StaticFiles(directory="uploads"),
    name="uploads",
)


# ============================================================
# CORS
# ============================================================
origins = [
    settings.frontend_url,
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DATABASE
# ============================================================
@app.on_event("startup")
async def on_startup():
    await database.connect()


@app.on_event("shutdown")
async def on_shutdown():
    await database.disconnect()


# ============================================================
# HEALTH CHECK
# ============================================================
@app.get("/health")
async def health_check():
    db_status = (
        "connected"
        if database.db is not None
        else "disconnected"
    )

    return {
        "success": True,
        "data": {
            "status": "ok",
            "db": db_status,
        },
        "message": "healthy",
    }


# ============================================================
# ROUTERS
# ============================================================

# Authentication
app.include_router(auth.router)

# Users
app.include_router(users.router)

# Courses
app.include_router(courses.router)

# Enrollments
app.include_router(enrollments.router)

# Modules
app.include_router(modules.router)

# Materials
app.include_router(materials.router)

# Attendance
app.include_router(attendance.router)

# Programs
app.include_router(programs.router)

# Notifications
app.include_router(notifications.router)

# Calendar
app.include_router(calendar.router)

# Credentials
app.include_router(credentials.router)

# Certificates
app.include_router(certificates.router)

# AI Insights
app.include_router(ai_insights.router)


# ============================================================
# COURSEWORK
# ============================================================

# Assignments
app.include_router(assignments_router)

# Quizzes
app.include_router(quizzes_router)

# Exams
app.include_router(exams_router)

# Projects
app.include_router(projects_router)

# Submissions
app.include_router(submissions_router)


# ============================================================
# GRADING
# ============================================================
app.include_router(grading.router)


# ============================================================
# REMINDERS
# ============================================================
app.include_router(reminders.router)