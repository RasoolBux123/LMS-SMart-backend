from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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
from app.api import certificates  # NEW

from app.api import notifications
from app.api import calendar
# Unified coursework (assignments / quizzes / exams / projects)
from app.api.coursework import (
    assignments_router,
    quizzes_router,
    exams_router,
    projects_router,
    submissions_router,
)

app = FastAPI(
    title="SmartLMS API",
    version="1.1",
)

# ==========================
# Create Upload Directories
# ==========================
for d in (
    "uploads/exams",
    "uploads/submissions",
    "uploads/assignments",
    "uploads/quizzes",
    "uploads/projects",
    "uploads/materials",
    "uploads/certificates",  # NEW — generated certificate PDFs
    "uploads/branding",      # NEW — logo printed on the certificates
):
    os.makedirs(d, exist_ok=True)

# ==========================
# Static Files
# ==========================
app.mount(
    "/uploads",
    StaticFiles(directory="uploads"),
    name="uploads",
)

# ==========================
# CORS
# ==========================
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

# ==========================
# Database
# ==========================
@app.on_event("startup")
async def on_startup():
    await database.connect()


@app.on_event("shutdown")
async def on_shutdown():
    await database.disconnect()


# ==========================
# Health Check
# ==========================
@app.get("/health")
async def health_check():
    db_status = "connected" if database.db is not None else "disconnected"
    return {
        "success": True,
        "data": {"status": "ok", "db": db_status},
        "message": "healthy",
    }


# ==========================
# Routers
# ==========================
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(courses.router)
app.include_router(enrollments.router)
app.include_router(modules.router)
app.include_router(materials.router)
app.include_router(attendance.router)
app.include_router(programs.router)
app.include_router(notifications.router)
app.include_router(calendar.router)
app.include_router(credentials.router)
app.include_router(certificates.router)  # NEW

# Coursework
app.include_router(assignments_router)
app.include_router(quizzes_router)
app.include_router(exams_router)
app.include_router(projects_router)
app.include_router(submissions_router)

# Grading report (instructor grading page)
app.include_router(grading.router)