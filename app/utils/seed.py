from datetime import datetime
from app.core.database import database
from app.core.security import get_password_hash
import asyncio

async def seed_users():
    """Seed database with test users"""
    
    # Check if users already exist
    existing = await database.get_db().users.find_one({"email": "admin@test.com"})
    if existing:
        print("✅ Users already seeded")
        return
    
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
        result = await database.get_db().users.insert_one(user)
        print(f"✅ Created user: {user['email']} (ID: {result.inserted_id})")
    
    print("✅ Seeding complete!")

async def main():
    await database.connect()
    await seed_users()
    await database.disconnect()

if __name__ == "__main__":
    asyncio.run(main())