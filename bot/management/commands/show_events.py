"""Look at what Meta actually sent.

    python manage.py show_events              # son 10 olay
    python manage.py show_events --failed     # yalnızca başarısızlar
    python manage.py show_events --payload    # ham webhook gövdesiyle

The raw payload is the only way to tell a comment mention from a caption
mention, or to see which fields Meta omitted.
"""

import json

from django.core.management.base import BaseCommand

from bot.models import InboxEvent, OutboxReply, Reminder


class Command(BaseCommand):
    help = "Print recent webhook events with their reminders, replies and raw payloads."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--failed", action="store_true", help="Only events that failed.")
        parser.add_argument("--payload", action="store_true", help="Include the raw webhook value.")
        parser.add_argument("--comment-id", default=None, help="One specific comment id.")

    def handle(self, *args, **options):
        events = InboxEvent.objects.order_by("-received_at")
        if options["failed"]:
            events = events.filter(status=InboxEvent.Status.FAILED)
        if options["comment_id"]:
            events = events.filter(comment_id=options["comment_id"])

        rows = list(events[: options["limit"]])
        if not rows:
            self.stdout.write("kayıt yok")
            return

        for event in rows:
            flag = self.style.ERROR if event.status == InboxEvent.Status.FAILED else self.style.SUCCESS
            self.stdout.write(
                f"\n{event.received_at:%Y-%m-%d %H:%M:%S} [{event.source}] {event.comment_id} "
                f"{flag(event.status)} attempts={event.attempts}"
            )
            if event.last_error:
                self.stdout.write(f"  hata: {event.last_error}")
            if options["payload"]:
                self.stdout.write("  payload: " + json.dumps(event.payload, ensure_ascii=False))

            reminder = Reminder.objects.filter(comment_id=event.comment_id).first()
            if reminder is None:
                self.stdout.write("  hatırlatma: yok")
                continue
            self.stdout.write(
                f"  hatırlatma: {reminder.status} due={reminder.due_at:%Y-%m-%d %H:%M} "
                f"user={reminder.requester_username or '-'} phrase={reminder.delay_phrase!r} "
                f"note={reminder.note!r} reason={reminder.ignored_reason or '-'}"
            )
            self.stdout.write(f"  metin: {reminder.comment_text!r}")
            for reply in OutboxReply.objects.filter(reminder=reminder).order_by("id"):
                detail = f" hata={reply.last_error}" if reply.last_error else ""
                self.stdout.write(f"  [{reply.kind}] {reply.status} attempts={reply.attempts}{detail}")
