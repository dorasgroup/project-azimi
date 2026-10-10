from django.core.management.base import BaseCommand, CommandError

from apps.monitoring.bale_client import BaleClient, BaleClientError


class Command(BaseCommand):
    help="فقط شناسه و مشخصات محدود chatهای Bale را نمایش می‌دهد؛ متن پیام‌ها ذخیره یا چاپ نمی‌شود."

    def handle(self, *args, **options):
        try:
            updates=BaleClient().get_updates().get("result") or []
        except BaleClientError as exc:
            raise CommandError(str(exc))
        seen=set()
        for update in updates:
            message=update.get("message") or update.get("channel_post") or {}
            chat=message.get("chat") or {}
            chat_id=chat.get("id")
            if chat_id is None or chat_id in seen:
                continue
            seen.add(chat_id)
            title=chat.get("title") or chat.get("first_name") or "بدون نام"
            self.stdout.write(f"chat_id={chat_id}\ttype={chat.get('type') or 'unknown'}\tname={title}")
        if not seen:
            self.stdout.write("chat_id قابل نمایش پیدا نشد.")
