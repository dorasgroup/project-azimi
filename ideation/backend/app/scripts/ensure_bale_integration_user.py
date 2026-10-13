import asyncio
import secrets

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models.user import User

INTEGRATION_EMAIL = "bale-integration@idea.haminyar.ir"


async def main() -> None:
    user_id = settings.bale_integration_user_id
    if user_id is None:
        raise RuntimeError("BALE_INTEGRATION_USER_ID is required")

    async with get_sessionmaker()() as session:
        by_id = await session.get(User, user_id)
        by_email = await session.scalar(select(User).where(User.email == INTEGRATION_EMAIL))
        if by_id is not None:
            if by_id.email != INTEGRATION_EMAIL:
                raise RuntimeError("Configured Bale integration user ID belongs to another user")
            print(f"Bale integration user already exists: {by_id.id}")
            return
        if by_email is not None:
            raise RuntimeError("Bale integration email already belongs to another user ID")

        user = User(
            id=user_id,
            email=INTEGRATION_EMAIL,
            full_name="Bale bot integration",
            hashed_password=hash_password(secrets.token_urlsafe(48)),
            role="integration",
            is_active=False,
        )
        session.add(user)
        await session.commit()
        print(f"Bale integration user created: {user.id}")


if __name__ == "__main__":
    asyncio.run(main())
