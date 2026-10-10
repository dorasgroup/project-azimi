from django.core.management.base import BaseCommand

from apps.monitoring.retention_services import cleanup_retention


class Command(BaseCommand):
    help="تعداد محتوای قابل حذف را با --dry-run نشان می‌دهد؛ حذف واقعی task دوره‌ای است."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run",action="store_true")

    def handle(self,*args,**options):
        count=cleanup_retention(dry_run=options["dry_run"])
        self.stdout.write(f"{count} مورد {'قابل حذف' if options['dry_run'] else 'حذف شد'}.")
