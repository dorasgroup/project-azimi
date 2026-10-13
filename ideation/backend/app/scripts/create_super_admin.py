import asyncio
import getpass
import os

from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models.user import User
from app.repositories.user_repository import UserRepository


async def main() -> None:
    email = os.getenv("SUPER_ADMIN_EMAIL") or input("Email: ")
    full_name = os.getenv("SUPER_ADMIN_FULL_NAME") or input("Full name: ")
    password = os.getenv("SUPER_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    email = email.strip().lower()
    full_name = full_name.strip()
    if not email or not full_name or not password:
        raise ValueError("Email, full name, and password are required")
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        users = UserRepository(session)
        existing = await users.get_by_email(email)
        if existing:
            print("User already exists")
            return
        user = User(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            role="super_admin",
            is_active=True,
        )
        await users.create(user)
        await session.commit()
        print(f"Super admin created: {email}")


if __name__ == "__main__":
    asyncio.run(main())
