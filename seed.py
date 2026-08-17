# backend/seed.py
import asyncio
from datetime import datetime
from app.core.database import database
from app.core.security import get_password_hash

async def seed_users():
    """Seed database with test users"""
    
    await database.connect()
    
    users = [
        {
            "name": "Admin User",
            "email": "admin@test.com",
            "password_hash": get_password_hash("Test123!"),
            "role": "admin",
            "status": "active",
            "created_at": datetime.utcnow()
        },
        {
            "name": "Instructor User",
            "email": "instructor@test.com",
            "password_hash": get_password_hash("Test123!"),
            "role": "instructor",
            "status": "active",
            "created_at": datetime.utcnow()
        },
        {
            "name": "Student User",
            "email": "student@test.com",
            "password_hash": get_password_hash("Test123!"),
            "role": "student",
            "status": "active",
            "created_at": datetime.utcnow()
        }
    ]
    
    for user in users:
        # Check if user already exists
        existing = await database.get_db().users.find_one({"email": user["email"]})
        if existing:
            print(f"⚠️ User already exists: {user['email']}")
        else:
            result = await database.get_db().users.insert_one(user)
            print(f"✅ Created user: {user['email']} (ID: {result.inserted_id})")
    
    await database.disconnect()
    print("✅ Seeding complete!")

if __name__ == "__main__":
    asyncio.run(seed_users())