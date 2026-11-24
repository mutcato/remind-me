from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from bot import pipeline
from bot.instagram import InstagramError
from bot.models import InboxEvent, OutboxReply, Reminder, Source

from .helpers import FakeClient, conf

SETTINGS = conf(IG_USER_ID="bot-ig-id")
NEVER_SLEEP = lambda _seconds: None  # noqa: E731


def stamp(when=None) -> str:
    return (when or timezone.now()).strftime("%Y-%m-%dT%H:%M:%S+0000")


def mention_event(comment_id="c1", media_id="m1") -> InboxEvent:
    return InboxEvent.objects.create(
        source=Source.MENTIONS,
        comment_id=comment_id,
        media_id=media_id,
        payload={"media_id": media_id, "comment_id": comment_id},
    )


def comment_event(text, *, comment_id="c1", media_id="m1", username="ahmet", from_id="user-1") -> InboxEvent:
    return InboxEvent.objects.create(
        source=Source.COMMENTS,
        comment_id=comment_id,
        media_id=media_id,
        payload={
            "id": comment_id,
            "text": text,
            "from": {"id": from_id, "username": username},
            "media": {"id": media_id},
            "timestamp": stamp(),
        },
    )


def mention_client(text="@remindme.in 3 gün", **kwargs) -> FakeClient:
    return FakeClient(
        comment={
            "id": "c1",
            "text": text,
            "timestamp": stamp(),
            "media": {"id": "m1", "permalink": "https://instagram.com/p/abc/"},
        },
        **kwargs,
    )


@override_settings(REMINDME=SETTINGS)
class InboxTests(TestCase):
    def test_mention_becomes_a_reminder_and_a_confirmation(self):
        mention_event()
        counters = pipeline.process_inbox(mention_client())
        self.assertEqual(counters["scheduled"], 1)

        reminder = Reminder.objects.get()
        self.assertEqual(reminder.status, Reminder.Status.PENDING)
        self.assertEqual(reminder.source, Source.MENTIONS)
        self.assertEqual(reminder.delay_phrase, "3 gün")
        self.assertEqual(reminder.permalink, "https://instagram.com/p/abc/")
        self.assertAlmostEqual(
            (reminder.due_at - timezone.now()).total_seconds(), timedelta(days=3).total_seconds(), delta=120
        )

        confirm = OutboxReply.objects.get(kind=OutboxReply.Kind.CONFIRM)
        self.assertEqual(confirm.status, OutboxReply.Status.PENDING)
        self.assertIn("hatırlat", confirm.message.lower())
        self.assertEqual(InboxEvent.objects.get().status, InboxEvent.Status.DONE)

    def test_our_own_comment_never_triggers_the_bot(self):
        comment_event("@remindme.in 3 gün", from_id="bot-ig-id")
        pipeline.process_inbox(FakeClient())
        self.assertFalse(Reminder.objects.exists())
        self.assertEqual(InboxEvent.objects.get().status, InboxEvent.Status.DONE)

    def test_comment_without_our_handle_is_dropped(self):
        comment_event("ne güzel post")
        pipeline.process_inbox(FakeClient())
        self.assertFalse(Reminder.objects.exists())

    def test_overlong_comment_is_dropped_before_parsing(self):
        comment_event("@remindme.in 3 gün " + "x" * 400)
        pipeline.process_inbox(FakeClient())
        self.assertFalse(Reminder.objects.exists())

    def test_username_is_kept_when_the_webhook_provides_it(self):
        comment_event("@remindme.in 2 saat", username="ahmet")
        pipeline.process_inbox(FakeClient())
        self.assertEqual(Reminder.objects.get().requester_username, "ahmet")

    def test_the_same_comment_is_only_ever_scheduled_once(self):
        comment = pipeline.Comment(
            comment_id="c9", media_id="m1", source=Source.COMMENTS, text="@remindme.in 3 gün"
        )
        self.assertEqual(pipeline.handle_reminder(comment), "scheduled")
        self.assertEqual(pipeline.handle_reminder(comment), "ignored:duplicate")
        self.assertEqual(Reminder.objects.count(), 1)

    def test_transient_fetch_failure_is_retried(self):
        mention_event()
        counters = pipeline.process_inbox(mention_client(error=InstagramError("rate limit", code=4, retryable=True)))
        self.assertEqual(counters["retried"], 1)
        event = InboxEvent.objects.get()
        self.assertEqual(event.status, InboxEvent.Status.PENDING)
        self.assertEqual(event.attempts, 1)
        self.assertGreater(event.next_attempt_at, timezone.now())

    def test_permanent_fetch_failure_stops(self):
        mention_event()
        counters = pipeline.process_inbox(mention_client(error=InstagramError("comment deleted", code=100)))
        self.assertEqual(counters["failed"], 1)
        self.assertEqual(InboxEvent.objects.get().status, InboxEvent.Status.FAILED)


@override_settings(REMINDME=SETTINGS)
class CommandTests(TestCase):
    def test_same_joins_the_last_reminder_on_that_post(self):
        comment_event("@remindme.in 3 gün", comment_id="c1", username="ahmet")
        pipeline.process_inbox(FakeClient())
        first = Reminder.objects.get(comment_id="c1")

        comment_event("@remindme.in aynı", comment_id="c2", username="zeynep")
        pipeline.process_inbox(FakeClient())
        second = Reminder.objects.get(comment_id="c2")
        self.assertEqual(second.due_at, first.due_at)
        self.assertEqual(second.status, Reminder.Status.PENDING)

    def test_same_on_a_post_with_no_reminder_is_ignored(self):
        comment_event("@remindme.in aynı")
        pipeline.process_inbox(FakeClient())
        self.assertEqual(Reminder.objects.get().ignored_reason, "same_without_sibling")

    @override_settings(REMINDME=conf(IG_USER_ID="bot-ig-id", MAX_ACTIVE_PER_USER=1))
    def test_per_user_cap(self):
        comment_event("@remindme.in 3 gün", comment_id="c1", username="ahmet")
        comment_event("@remindme.in 4 gün", comment_id="c2", username="ahmet")
        pipeline.process_inbox(FakeClient())
        self.assertEqual(Reminder.objects.filter(status=Reminder.Status.PENDING).count(), 1)
        self.assertEqual(Reminder.objects.get(comment_id="c2").ignored_reason, "user_limit")

    def test_unparsed_stays_silent_by_default(self):
        comment_event("@remindme.in çok güzelmiş")
        pipeline.process_inbox(FakeClient())
        self.assertEqual(Reminder.objects.get().ignored_reason, "unparsed")
        self.assertFalse(OutboxReply.objects.exists())

    @override_settings(REMINDME=conf(IG_USER_ID="bot-ig-id", REPLY_ON_UNPARSED=True))
    def test_unparsed_can_answer_with_usage_help(self):
        comment_event("@remindme.in çok güzelmiş")
        pipeline.process_inbox(FakeClient())
        help_reply = OutboxReply.objects.get(kind=OutboxReply.Kind.HELP)
        self.assertIn("@remindme.in", help_reply.message)

    def test_too_far_is_answered_with_the_limit(self):
        comment_event("@remindme.in 5 yıl")
        pipeline.process_inbox(FakeClient())
        self.assertEqual(Reminder.objects.get().ignored_reason, "too_far")
        self.assertIn("365", OutboxReply.objects.get(kind=OutboxReply.Kind.HELP).message)


@override_settings(REMINDME=SETTINGS)
class SchedulerAndSenderTests(TestCase):
    def schedule(self, text="@remindme.in 3 gün", source=Source.MENTIONS) -> Reminder:
        if source == Source.MENTIONS:
            mention_event()
            pipeline.process_inbox(mention_client(text))
        else:
            comment_event(text)
            pipeline.process_inbox(FakeClient())
        return Reminder.objects.get()

    def test_due_reminder_is_queued_once(self):
        reminder = self.schedule()
        self.assertEqual(pipeline.schedule_due(), 0)  # not due yet

        Reminder.objects.filter(pk=reminder.pk).update(due_at=timezone.now())
        self.assertEqual(pipeline.schedule_due(), 1)
        self.assertEqual(pipeline.schedule_due(), 0)  # idempotent
        remind = OutboxReply.objects.get(kind=OutboxReply.Kind.REMIND)
        self.assertIn("Hatırlatma", remind.message)

    def test_sender_posts_and_closes_the_reminder(self):
        reminder = self.schedule()
        Reminder.objects.filter(pk=reminder.pk).update(due_at=timezone.now())
        pipeline.schedule_due()

        client = FakeClient()
        counters = pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        self.assertEqual(counters["sent"], 2)  # confirmation + reminder
        self.assertEqual({edge for edge, _cid, _msg in client.sent}, {"mentions"})
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, Reminder.Status.SENT)
        self.assertIsNotNone(reminder.sent_at)
        self.assertFalse(OutboxReply.objects.filter(status=OutboxReply.Status.PENDING).exists())

    def test_comment_source_replies_on_the_comment_edge(self):
        self.schedule("@remindme.in 2 saat", source=Source.COMMENTS)
        client = FakeClient()
        pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        self.assertEqual(client.sent[0][0], "comments")

    @override_settings(REMINDME=conf(IG_USER_ID="bot-ig-id", MAX_ATTEMPTS=2))
    def test_transient_send_error_is_retried_then_given_up(self):
        reminder = self.schedule()
        Reminder.objects.filter(pk=reminder.pk).update(due_at=timezone.now())
        pipeline.schedule_due()
        client = FakeClient(error=InstagramError("rate limit", code=4, retryable=True))

        counters = pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        self.assertEqual(counters["retried"], 2)
        self.assertTrue(OutboxReply.objects.filter(status=OutboxReply.Status.PENDING).exists())

        later = timezone.now() + timedelta(hours=2)
        counters = pipeline.send_outbox(client, now=later, sleep=NEVER_SLEEP)
        self.assertEqual(counters["failed"], 2)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, Reminder.Status.FAILED)

    def test_deleted_comment_fails_the_reminder_immediately(self):
        reminder = self.schedule()
        Reminder.objects.filter(pk=reminder.pk).update(due_at=timezone.now())
        pipeline.schedule_due()
        client = FakeClient(error=InstagramError("comment does not exist", code=100))

        pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, Reminder.Status.FAILED)
        self.assertEqual(OutboxReply.objects.filter(status=OutboxReply.Status.FAILED).count(), 2)

    @override_settings(REMINDME=conf(IG_USER_ID="bot-ig-id", SEND_MAX_PER_HOUR=1))
    def test_hourly_budget_stops_the_sender(self):
        self.schedule()
        client = FakeClient()
        self.assertEqual(pipeline.send_outbox(client, sleep=NEVER_SLEEP)["sent"], 1)

        comment_event("@remindme.in 1 saat", comment_id="c2", media_id="m2")
        pipeline.process_inbox(FakeClient())
        counters = pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        self.assertEqual(counters, {"sent": 0, "retried": 0, "failed": 0, "throttled": 1})

    def test_sender_paces_itself_between_posts(self):
        self.schedule()
        Reminder.objects.filter(status=Reminder.Status.PENDING).update(due_at=timezone.now())
        pipeline.schedule_due()
        waits: list = []
        pipeline.send_outbox(FakeClient(), sleep=waits.append)
        self.assertEqual(waits, [SETTINGS["SEND_INTERVAL_SECONDS"]])

    def test_run_once_walks_the_whole_pipeline(self):
        mention_event()
        counters = pipeline.run_once(mention_client())
        self.assertEqual(counters["scheduled"], 1)
        self.assertEqual(counters["sent"], 1)  # the confirmation


@override_settings(REMINDME=SETTINGS)
class EdgeFallbackTests(TestCase):
    """Instagram Login delivers @mentions inside the `comments` webhook, and those
    cannot be answered on the comment edge — the sender has to switch."""

    class OnlyMentionsEdge(FakeClient):
        def reply_to_comment(self, *, comment_id, message):
            raise InstagramError("Unsupported post request", code=100)

    def test_sender_switches_edge_when_the_first_one_is_refused(self):
        comment_event("@remindme.in 2 saat")
        pipeline.process_inbox(FakeClient())

        client = self.OnlyMentionsEdge()
        counters = pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        self.assertEqual(counters["sent"], 1)
        self.assertEqual(client.sent[0][0], "mentions")
        self.assertEqual(OutboxReply.objects.get().status, OutboxReply.Status.SENT)

    def test_failure_on_both_edges_still_fails(self):
        comment_event("@remindme.in 2 saat")
        pipeline.process_inbox(FakeClient())
        counters = pipeline.send_outbox(FakeClient(error=InstagramError("gone", code=100)), sleep=NEVER_SLEEP)
        self.assertEqual(counters["failed"], 1)
