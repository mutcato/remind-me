from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from bot import pipeline
from bot.instagram import InstagramClient, InstagramError
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


class AccountLookupTests(TestCase):
    """`account_type` is not available in every Meta setup; asking for it must
    never be what breaks the setup check."""

    class Client(InstagramClient):
        def __init__(self, reject_field: bool):
            super().__init__(ig_user_id="1", access_token="t")
            self.reject_field = reject_field
            self.calls: list = []

        def _call(self, method, path, *, params=None, data=None):
            self.calls.append(params["fields"])
            if self.reject_field and "account_type" in params["fields"]:
                raise InstagramError("unknown field", code=100)
            return {"id": "1", "username": "remindme.in"}

    def test_account_type_requested_first(self):
        client = self.Client(reject_field=False)
        client.fetch_account()
        self.assertEqual(client.calls, ["id,username,account_type"])

    def test_falls_back_when_the_field_is_rejected(self):
        client = self.Client(reject_field=True)
        self.assertEqual(client.fetch_account()["username"], "remindme.in")
        self.assertEqual(client.calls, ["id,username,account_type", "id,username"])

    def test_other_errors_still_propagate(self):
        class Dead(self.Client):
            def _call(self, method, path, *, params=None, data=None):
                raise InstagramError("token expired", code=190)

        with self.assertRaises(InstagramError):
            Dead(reject_field=False).fetch_account()


class SubscribeCommandTests(TestCase):
    """`mentions` does not exist in every setup; the command must still subscribe
    to what does."""

    def run_command(self, reject_mentions: bool):
        from io import StringIO

        from django.core.management import call_command

        asked: list = []

        class Client(InstagramClient):
            def __init__(self):
                super().__init__(ig_user_id="1", access_token="t")

            def list_subscriptions(self):
                return {"data": []}

            def subscribe(self, fields="comments,mentions"):
                asked.append(fields)
                if reject_mentions and "mentions" in fields:
                    raise InstagramError("(#100) Invalid field", code=100)
                return {"success": True}

        out = StringIO()
        with patch("bot.management.commands.subscribe_webhook.InstagramClient", Client):
            call_command("subscribe_webhook", stdout=out)
        return asked, out.getvalue()

    def test_subscribes_to_both_when_supported(self):
        asked, output = self.run_command(reject_mentions=False)
        self.assertEqual(asked, ["comments,mentions"])
        self.assertIn("success", output)

    def test_falls_back_to_comments_when_mentions_is_unknown(self):
        asked, output = self.run_command(reject_mentions=True)
        self.assertEqual(asked, ["comments,mentions", "comments"])
        self.assertIn("comments' bildiriminin içinde", output)


class CheckSetupTests(TestCase):
    """An unedited .env.example value must read as missing, not as configured."""

    def run_check(self, **overrides):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with override_settings(REMINDME=conf(**overrides), SECRET_KEY="a-real-key", DEBUG=False):
            try:
                call_command("check_setup", stdout=out)
            except SystemExit:
                pass
        return out.getvalue()

    def test_placeholder_ig_user_id_is_not_accepted(self):
        output = self.run_check(IG_USER_ID="17841400000000000", APP_SECRET="s", VERIFY_TOKEN="v", ACCESS_TOKEN="t")
        self.assertIn("eksik", output.split("IG_USER_ID")[0].splitlines()[-1])

    def test_real_values_pass_the_first_section(self):
        output = self.run_check(IG_USER_ID="17841499999999999", APP_SECRET="s", VERIFY_TOKEN="v", ACCESS_TOKEN="t")
        self.assertNotIn(".env tamamlanmadan", output)


class SecretRedactionTests(TestCase):
    """An access token must never survive into an exception message or a log."""

    def test_network_error_does_not_leak_the_token(self):
        import requests

        from bot.instagram import redact

        token = "EAAgPI8M6ZBZC0BTBSy5ZCz5DLbuOoEsFvTZCLzclM0uPyS36QEvoAZCJtPKHX"
        client = InstagramClient(ig_user_id="1", access_token=token)

        class Boom:
            def request(self, *a, **kw):
                raise requests.ConnectionError(
                    f"Max retries exceeded with url: /v27.0/1?access_token={token} (Network is unreachable)"
                )

        client.session = Boom()
        with self.assertRaises(InstagramError) as caught:
            client.fetch_account()
        message = str(caught.exception)
        self.assertNotIn(token, message)
        self.assertIn("REDACTED", message)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(redact("plain text", token), "plain text")

    def test_api_error_message_is_redacted_too(self):
        token = "EAAgPI8M6ZBZC0BTBSy5ZCz5DLbuOoEsFvTZCLzclM0uPyS36QEvoAZCJtPKHX"
        client = InstagramClient(ig_user_id="1", access_token=token)

        class Response:
            status_code = 400

            def json(self):
                return {"error": {"message": f"Invalid token {token}", "code": 190}}

        class Session:
            def request(self, *a, **kw):
                return Response()

        client.session = Session()
        with self.assertRaises(InstagramError) as caught:
            client.fetch_account()
        self.assertNotIn(token, str(caught.exception))


@override_settings(REMINDME=SETTINGS)
class SelftestSuppressionTests(TestCase):
    """A self-test reply must never reach Instagram: the comment id is synthetic."""

    def test_selftest_reply_is_marked_sent_without_posting(self):
        comment_event("@remindme.in 2 saat", comment_id=f"{pipeline.SELFTEST_PREFIX}1")
        pipeline.process_inbox(FakeClient())
        client = FakeClient()
        counters = pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        self.assertEqual(counters["sent"], 1)
        self.assertEqual(client.sent, [])  # nothing left the building
        row = OutboxReply.objects.get()
        self.assertEqual((row.status, row.reply_comment_id), (OutboxReply.Status.SENT, "selftest"))

    def test_real_comment_ids_are_untouched(self):
        comment_event("@remindme.in 2 saat", comment_id="17918718562020960")
        pipeline.process_inbox(FakeClient())
        client = FakeClient()
        pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        self.assertEqual(len(client.sent), 1)


@override_settings(REMINDME=SETTINGS)
class ReplyLoopTests(TestCase):
    """The bot replied to its own reply seven times in a row, because the
    confirmation contained "@remindme.in same" and the mentions webhook carries
    no author to recognise ourselves by."""

    def test_confirmation_does_not_mention_the_bot(self):
        comment_event("@remindme.in 1 hour")
        pipeline.process_inbox(FakeClient())
        confirm = OutboxReply.objects.get(kind=OutboxReply.Kind.CONFIRM)
        self.assertNotIn("@remindme.in", confirm.message)

    @override_settings(REMINDME=conf(IG_USER_ID="bot-ig-id", SAME_HINT=True))
    def test_hint_is_available_but_opt_in(self):
        comment_event("@remindme.in 1 hour")
        pipeline.process_inbox(FakeClient())
        self.assertIn("@remindme.in", OutboxReply.objects.get(kind=OutboxReply.Kind.CONFIRM).message)

    def test_our_own_reply_coming_back_as_a_mention_is_ignored(self):
        # Post a reply, remember its id, then let Instagram hand it back to us.
        comment_event("@remindme.in 1 hour", comment_id="user-comment")
        pipeline.process_inbox(FakeClient())
        client = FakeClient()
        pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        our_reply_id = OutboxReply.objects.get().reply_comment_id
        self.assertTrue(our_reply_id)

        mention_event(comment_id=our_reply_id, media_id="m1")
        pipeline.process_inbox(mention_client("⏰ Tamam, 26 Kasım Perşembe 22:40'ta hatırlatırım."))
        self.assertEqual(Reminder.objects.count(), 1)  # no second reminder

    def test_text_starting_with_our_marker_is_ignored(self):
        mention_event(comment_id="unknown-id")
        client = mention_client("⏰ Got it — I'll remind you on Thu 26 Nov, 22:40. @remindme.in same")
        pipeline.process_inbox(client)
        self.assertFalse(Reminder.objects.filter(status=Reminder.Status.PENDING).exists())

    @override_settings(REMINDME=conf(IG_USER_ID="bot-ig-id", MAX_REPLIES_PER_MEDIA_PER_HOUR=2))
    def test_one_post_cannot_be_flooded(self):
        for index in range(4):
            comment_event(f"@remindme.in {index + 1} hours", comment_id=f"c{index}", media_id="same-post")
        pipeline.process_inbox(FakeClient())
        self.assertEqual(Reminder.objects.count(), 4)

        client = FakeClient()
        counters = pipeline.send_outbox(client, sleep=NEVER_SLEEP)
        self.assertEqual(counters["sent"], 2)
        self.assertEqual(counters["throttled"], 2)
        deferred = OutboxReply.objects.filter(status=OutboxReply.Status.PENDING)
        self.assertEqual(deferred.count(), 2)
        self.assertIn("per-media cap", deferred.first().last_error)
