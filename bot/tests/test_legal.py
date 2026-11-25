from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bot.models import InboxEvent, OutboxReply, Reminder, Source  # noqa: F401


class LegalPageTests(TestCase):
    """Meta will not publish the app without these two URLs resolving."""

    def test_privacy_page(self):
        response = self.client.get(reverse("privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gizlilik Politikası")
        self.assertContains(response, "Privacy Policy")

    def test_data_deletion_page(self):
        response = self.client.get(reverse("data-deletion"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Veri Silme")
        self.assertContains(response, "data-deletion")

    def test_pages_need_no_login(self):
        for name in ("privacy", "data-deletion"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class PurgeTests(TestCase):
    """The retention promise has to be backed by something that runs."""

    def make(self, status, age_days, comment_id):
        reminder = Reminder.objects.create(
            comment_id=comment_id, media_id="m", source=Source.COMMENTS,
            comment_text="@remindme.in 1 saat", due_at=timezone.now(), status=status,
        )
        OutboxReply.objects.create(
            reminder=reminder, kind=OutboxReply.Kind.REMIND, target_comment_id=comment_id,
            media_id="m", source=Source.COMMENTS, message="x",
        )
        Reminder.objects.filter(pk=reminder.pk).update(created_at=timezone.now() - timedelta(days=age_days))
        return reminder

    def purge(self, **kwargs):
        out = StringIO()
        call_command("purge_old", stdout=out, **kwargs)
        return out.getvalue()

    def test_dry_run_deletes_nothing(self):
        self.make(Reminder.Status.SENT, 200, "old")
        self.assertIn("kuru çalışma", self.purge())
        self.assertEqual(Reminder.objects.count(), 1)

    def test_old_closed_reminders_go_with_their_replies(self):
        self.make(Reminder.Status.SENT, 200, "old")
        self.purge(apply=True)
        self.assertEqual(Reminder.objects.count(), 0)
        self.assertEqual(OutboxReply.objects.count(), 0)

    def test_pending_reminders_are_never_purged(self):
        self.make(Reminder.Status.PENDING, 400, "waiting")
        self.purge(apply=True)
        self.assertEqual(Reminder.objects.count(), 1)

    def test_recent_reminders_survive(self):
        self.make(Reminder.Status.SENT, 10, "recent")
        self.purge(apply=True)
        self.assertEqual(Reminder.objects.count(), 1)

    def test_processed_events_are_cleaned_but_not_pending_ones(self):
        InboxEvent.objects.create(
            source=Source.COMMENTS, comment_id="done", payload={},
            status=InboxEvent.Status.DONE, processed_at=timezone.now() - timedelta(days=200),
        )
        self.make(Reminder.Status.PENDING, 400, "waiting")
        InboxEvent.objects.create(
            source=Source.COMMENTS, comment_id="waiting", payload={},
            status=InboxEvent.Status.DONE, processed_at=timezone.now() - timedelta(days=200),
        )
        self.purge(apply=True)
        self.assertEqual(list(InboxEvent.objects.values_list("comment_id", flat=True)), ["waiting"])


class ShowEventsTests(TestCase):
    """Forensics on what Meta sent: the raw payload must be reachable."""

    def setUp(self):
        self.event = InboxEvent.objects.create(
            source=Source.MENTIONS, comment_id="18387093088237627", media_id="m1",
            payload={"media_id": "m1", "comment_id": "18387093088237627"},
            status=InboxEvent.Status.FAILED, attempts=1,
            last_error="(#100) Param comment_id is not a valid instagram comment ID",
        )

    def run_cmd(self, **kwargs):
        out = StringIO()
        call_command("show_events", stdout=out, **kwargs)
        return out.getvalue()

    def test_failed_event_shows_its_error(self):
        output = self.run_cmd(failed=True)
        self.assertIn("18387093088237627", output)
        self.assertIn("not a valid instagram comment ID", output)
        self.assertIn("hatırlatma: yok", output)

    def test_payload_is_printed_on_demand(self):
        self.assertNotIn("media_id", self.run_cmd())
        self.assertIn('"media_id": "m1"', self.run_cmd(payload=True))

    def test_filtering_by_comment_id(self):
        InboxEvent.objects.create(source=Source.MENTIONS, comment_id="other", payload={})
        output = self.run_cmd(comment_id="other")
        self.assertIn("other", output)
        self.assertNotIn("18387093088237627", output)
