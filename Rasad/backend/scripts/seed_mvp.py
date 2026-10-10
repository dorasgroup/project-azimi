import asyncio

from sqlalchemy import select, update

from app.core.security import hash_password
from app.db.session import SessionFactory, engine
from app.models import BotIdentity, Source, User, UserRole


USERNAME = "mvp_admin"
PASSWORD = "MvpDemoPass123!"


async def seed_mvp() -> None:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.username == USERNAME))
        if user is None:
            user = User(
                full_name="مدیر MVP",
                username=USERNAME,
                email="mvp_admin@example.com",
                password_hash=hash_password(PASSWORD),
                role=UserRole.SUPER_ADMIN,
                is_active=True,
            )
            session.add(user)
            await session.flush()
        else:
            user.password_hash = hash_password(PASSWORD)
            user.role = UserRole.SUPER_ADMIN
            user.is_active = True

        source = await session.scalar(
            select(Source).where(Source.base_url == "https://www.python.org/")
        )
        if source is not None:
            source.is_active = False

        identity = await session.scalar(
            select(BotIdentity).where(BotIdentity.title == "تحلیلگر MVP")
        )
        await session.execute(update(BotIdentity).values(is_default=False))
        if identity is None:
            identity = BotIdentity(
                title="تحلیلگر MVP",
                description="هویت پیش‌فرض برای اجرای محلی MVP",
                role_definition="اخبار منتخب را از نظر روایت، بازیگران، چارچوب‌بندی و نشانه‌های عملیات روانی تحلیل کن.",
                tone="رسمی",
                analysis_style="ساختاریافته",
                cognitive_framework="روایت، بازیگران، چارچوب‌بندی، نشانه‌های اقناع",
                output_structure={"format": "json"},
                source_policy={"require_citations": True},
                is_active=True,
                is_default=True,
                creator=user,
            )
            session.add(identity)
        else:
            identity.is_active = True
            identity.is_default = True

        await session.commit()
        print(f"MVP seed ready: username={USERNAME}, password={PASSWORD}")


async def main() -> None:
    try:
        await seed_mvp()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
