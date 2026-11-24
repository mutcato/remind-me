"""Run a comment through the whole pipeline locally, posting nothing.

    python manage.py simulate_mention --text "@remindme.in 3 gün tarifi dene" --fire

No Meta app, no token and no network needed: the fake event uses the `comments`
payload shape, which carries the comment text inline.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from bot.instagram import InstagramClient
from bot.models import InboxEvent, OutboxReply, Reminder, Source
from bot.pipeline import process_inbox, schedule_due, send_outbox


class Command(BaseCommand):
    help = "Feed a fake comment through the pipeline with DRY_RUN forced on."

    def add_arguments(self, parser):
        parser.add_argument("--text", required=True)
        parser.add_argument("--username", default="test_user")
        parser.add_argument("--comment-id", default=None)
        parser.add_argument("--media-id", default="sim-media-1")
        parser.add_argument("--send", action="store_true", help="Also run the sender (dry run).")
        parser.add_argument("--fire", action="store_true", help="Pull the reminder's due time to now and send it.")

    def handle(self, *args, **options):
        comment_id = options["comment_id"] or f"sim-{timezone.now():%Y%m%d%H%M%S%f}"
        InboxEvent.objects.create(
            source=Source.COMMENTS,
            comment_id=comment_id,
            media_id=options["media_id"],
            payload={
                "id": comment_id,
                "text": options["text"],
                "from": {"id": "sim-user", "username": options["username"]},
                "media": {"id": options["media_id"]},
                "timestamp": timezone.now().strftime("%Y-%m-%dT%H:%M:%S+0000"),
            },
        )
        client = InstagramClient(dry_run=True)
        self.stdout.write(f"inbox: {process_inbox(client)}")

        reminder = Reminder.objects.filter(comment_id=comment_id).first()
        if reminder:
            self.stdout.write(
                f"reminder: status={reminder.status} due={reminder.due_at} "
                f"locale={reminder.locale} phrase={reminder.delay_phrase!r} note={reminder.note!r} "
                f"reason={reminder.ignored_reason or '-'}"
            )
        if options["fire"] and reminder and reminder.due_at:
            Reminder.objects.filter(pk=reminder.pk).update(due_at=timezone.now())
            self.stdout.write(f"due queued: {schedule_due()}")
        if options["send"] or options["fire"]:
            self.stdout.write(f"sender: {send_outbox(client, sleep=lambda _s: None)}")

        for row in OutboxReply.objects.filter(target_comment_id=comment_id).order_by("id"):
            self.stdout.write(self.style.SUCCESS(f"\n[{row.kind}] ({row.status})\n{row.message}"))
