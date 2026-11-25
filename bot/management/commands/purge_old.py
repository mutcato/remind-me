"""Delete closed reminders, so the privacy policy's retention promise is real.

    python manage.py purge_old            # dry run, prints what would go
    python manage.py purge_old --apply

Pending reminders are never touched — they are still waiting to fire.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from bot.models import InboxEvent, Reminder

CLOSED = [Reminder.Status.SENT, Reminder.Status.FAILED, Reminder.Status.IGNORED]


class Command(BaseCommand):
    help = "Delete reminders closed longer ago than the retention window."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=settings.REMINDME["RETENTION_DAYS"])
        parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry run).")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        reminders = Reminder.objects.filter(status__in=CLOSED, created_at__lt=cutoff)
        events = InboxEvent.objects.filter(processed_at__lt=cutoff).exclude(
            comment_id__in=Reminder.objects.filter(status=Reminder.Status.PENDING).values("comment_id")
        )
        counts = (reminders.count(), events.count())
        self.stdout.write(f"{options['days']} günden eski: {counts[0]} hatırlatma, {counts[1]} webhook olayı")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("kuru çalışma — silmek için --apply"))
            return
        reminders.delete()  # cascades to their replies
        events.delete()
        self.stdout.write(self.style.SUCCESS(f"silindi: {counts[0]} hatırlatma, {counts[1]} olay"))
