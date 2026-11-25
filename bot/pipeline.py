"""Webhook -> reminder -> reply.

The worker calls three functions in order, and each one is safe to call again:

    process_inbox()   interpret webhook events that landed, create reminders
    schedule_due()    reminders whose time has come -> outgoing replies
    send_outbox()     the single rate-limited sender that posts to Instagram

Nothing here runs inside the webhook request (HANDOFF.md §3): the view only
writes an InboxEvent and returns 200.
"""

import logging
import time as time_module
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from . import messages, router, timeparse
from .instagram import InstagramClient, InstagramError
from .models import InboxEvent, OutboxReply, Reminder, Source

log = logging.getLogger(__name__)

# Replies to these comment ids are built but never posted: the id is synthetic,
# so Instagram would reject it. Used by `manage.py selftest_webhook`.
SELFTEST_PREFIX = "selftest-"


@dataclass
class Comment:
    comment_id: str
    media_id: str
    source: str
    text: str
    username: str = ""
    permalink: str = ""
    created_at: datetime | None = None


def _parse_timestamp(raw: str | None) -> datetime | None:
    """Graph API sends "2026-11-24T18:14:00+0000"."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _backoff(attempts: int) -> timedelta:
    conf = settings.REMINDME
    seconds = min(conf["RETRY_BASE_SECONDS"] * (2 ** max(attempts - 1, 0)), conf["RETRY_MAX_SECONDS"])
    return timedelta(seconds=seconds)


# --- 1. the webhook door -----------------------------------------------------
def enqueue(source: str, value: dict) -> InboxEvent | None:
    """Store one webhook change. Returns None when we have seen this comment."""
    comment_id = str(value.get("comment_id") or value.get("id") or "")
    if not comment_id:
        return None
    media = value.get("media") or {}
    media_id = str(value.get("media_id") or media.get("id") or "")
    event, created = InboxEvent.objects.get_or_create(
        comment_id=comment_id,
        defaults={"source": source, "media_id": media_id, "payload": value},
    )
    if not created:
        log.info("duplicate webhook delivery for comment %s, ignored", comment_id)
        return None
    return event


# --- 2. interpreting events --------------------------------------------------
def _comment_from_event(event: InboxEvent, client: InstagramClient) -> Comment:
    value = event.payload or {}
    if event.source == Source.COMMENTS:
        sender = value.get("from") or {}
        media = value.get("media") or {}
        return Comment(
            comment_id=event.comment_id,
            media_id=str(value.get("media_id") or media.get("id") or ""),
            source=event.source,
            text=value.get("text") or "",
            username=sender.get("username") or "",
            created_at=_parse_timestamp(value.get("timestamp")),
        )

    # The mentions webhook carries only ids, so the text has to be fetched.
    data = client.fetch_mentioned_comment(event.comment_id)
    media = data.get("media") or {}
    return Comment(
        comment_id=event.comment_id,
        media_id=str(media.get("id") or event.media_id or ""),
        source=event.source,
        text=data.get("text") or "",
        permalink=media.get("permalink") or "",
        created_at=_parse_timestamp(data.get("timestamp")),
    )


def _is_self(event: InboxEvent) -> bool:
    """Never react to our own comments — that is how reply loops start."""
    sender = (event.payload or {}).get("from") or {}
    sender_id = str(sender.get("id") or "")
    own_id = str(settings.REMINDME["IG_USER_ID"] or "")
    return bool(sender_id) and bool(own_id) and sender_id == own_id


def process_inbox(client: InstagramClient | None = None, limit: int | None = None) -> dict:
    conf = settings.REMINDME
    client = client or InstagramClient()
    now = timezone.now()
    counters: dict = {"processed": 0, "scheduled": 0, "ignored": 0, "retried": 0, "failed": 0}

    events = InboxEvent.objects.filter(
        status=InboxEvent.Status.PENDING, next_attempt_at__lte=now
    ).order_by("received_at")[: limit or conf["INBOX_BATCH_SIZE"]]

    for event in list(events):
        try:
            outcome = _process_event(event, client)
        except InstagramError as exc:
            event.attempts += 1
            event.last_error = str(exc)
            if exc.retryable and event.attempts < conf["MAX_ATTEMPTS"]:
                event.next_attempt_at = timezone.now() + _backoff(event.attempts)
                counters["retried"] += 1
            else:
                event.status = InboxEvent.Status.FAILED
                counters["failed"] += 1
            event.save(update_fields=["attempts", "last_error", "next_attempt_at", "status"])
            log.warning("inbox event %s failed: %s", event.comment_id, exc)
            continue

        event.status = InboxEvent.Status.DONE
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at"])
        counters["processed"] += 1
        counters["scheduled" if outcome == "scheduled" else "ignored"] += 1
        log.info("inbox event %s -> %s", event.comment_id, outcome)
    return counters


def _process_event(event: InboxEvent, client: InstagramClient) -> str:
    if _is_self(event):
        return "ignored:self"

    comment = _comment_from_event(event, client)
    if not comment.text.strip():
        return "ignored:empty"
    # Cheap filters first, before any parsing or paid call (HANDOFF.md §3).
    if len(comment.text) > settings.REMINDME["MAX_COMMENT_CHARS"]:
        return "ignored:too_long"

    match = router.resolve(comment.text)
    if match is None:
        return "ignored:no_handle"
    handle, handler = match
    return handler(comment)


# --- 3. the @remindme.in command --------------------------------------------
def _queue_reply(reminder: Reminder, kind: str, message: str, send_after=None) -> OutboxReply:
    row, _ = OutboxReply.objects.get_or_create(
        reminder=reminder,
        kind=kind,
        defaults={
            "target_comment_id": reminder.comment_id,
            "media_id": reminder.media_id,
            "source": reminder.source,
            "message": message,
            "send_after": send_after or timezone.now(),
        },
    )
    return row


def _ignore(comment: Comment, result, reason: str, reply: str | None) -> str:
    reminder = Reminder.objects.create(
        comment_id=comment.comment_id,
        media_id=comment.media_id,
        source=comment.source,
        permalink=comment.permalink,
        requester_username=comment.username,
        comment_text=comment.text,
        note=getattr(result, "note", "") or "",
        locale=getattr(result, "locale", settings.REMINDME["DEFAULT_LOCALE"]),
        status=Reminder.Status.IGNORED,
        ignored_reason=reason,
    )
    if reply:
        _queue_reply(reminder, OutboxReply.Kind.HELP, reply)
    return f"ignored:{reason}"


def handle_reminder(comment: Comment) -> str:
    """Parse one comment and, if it holds a time, store the reminder."""
    conf = settings.REMINDME
    if Reminder.objects.filter(comment_id=comment.comment_id).exists():
        return "ignored:duplicate"

    base = comment.created_at or timezone.now()
    result = timeparse.parse(
        comment.text,
        now=base,
        tz=conf["DEFAULT_TIMEZONE"],
        min_delay_seconds=conf["MIN_DELAY_SECONDS"],
        max_delay_days=conf["MAX_DELAY_DAYS"],
        max_note_chars=conf["MAX_NOTE_CHARS"],
        default_locale=conf["DEFAULT_LOCALE"],
    )

    if result.kind == "too_far":
        return _ignore(comment, result, "too_far",
                       messages.too_far_text(locale=result.locale, days=conf["MAX_DELAY_DAYS"]))

    if result.kind == "none":
        reply = messages.help_text(locale=result.locale, handle=conf["BOT_HANDLE"]) if conf["REPLY_ON_UNPARSED"] else None
        return _ignore(comment, result, "unparsed", reply)

    due_at = result.due_at
    phrase = result.phrase if result.kind == "delay" else ""
    if result.kind == "same":
        # "aynı": join the most recent pending reminder on this same post.
        sibling = (
            Reminder.objects.filter(media_id=comment.media_id, status=Reminder.Status.PENDING, due_at__isnull=False)
            .order_by("-created_at")
            .first()
        )
        if sibling is None:
            reply = messages.help_text(locale=result.locale, handle=conf["BOT_HANDLE"]) if conf["REPLY_ON_UNPARSED"] else None
            return _ignore(comment, result, "same_without_sibling", reply)
        due_at = sibling.due_at

    if comment.username:
        active = Reminder.objects.filter(
            requester_username=comment.username, status=Reminder.Status.PENDING
        ).count()
        if active >= conf["MAX_ACTIVE_PER_USER"]:
            return _ignore(comment, result, "user_limit", None)

    reminder = Reminder.objects.create(
        comment_id=comment.comment_id,
        media_id=comment.media_id,
        source=comment.source,
        permalink=comment.permalink,
        requester_username=comment.username,
        comment_text=comment.text,
        note=result.note,
        delay_phrase=phrase,
        locale=result.locale,
        due_at=due_at,
        status=Reminder.Status.PENDING,
    )
    if conf["CONFIRM_REPLY"]:
        _queue_reply(
            reminder,
            OutboxReply.Kind.CONFIRM,
            messages.confirm_text(
                due_at=due_at,
                locale=result.locale,
                tz=conf["DEFAULT_TIMEZONE"],
                handle=conf["BOT_HANDLE"],
                note=result.note,
                key=comment.comment_id,
            ),
        )
    return "scheduled"


router.register(settings.REMINDME["BOT_HANDLE"], handle_reminder)


# --- 4. due reminders -> outgoing replies ------------------------------------
def schedule_due(now: datetime | None = None) -> int:
    conf = settings.REMINDME
    now = now or timezone.now()
    created = 0
    due = Reminder.objects.filter(
        status=Reminder.Status.PENDING, due_at__isnull=False, due_at__lte=now
    ).order_by("due_at")[: conf["SEND_BATCH_SIZE"] * 2]

    for reminder in list(due):
        message = messages.remind_text(
            elapsed=now - reminder.created_at,
            phrase=reminder.delay_phrase,
            locale=reminder.locale,
            note=reminder.note,
            key=reminder.comment_id,
        )
        before = OutboxReply.objects.filter(reminder=reminder, kind=OutboxReply.Kind.REMIND).exists()
        _queue_reply(reminder, OutboxReply.Kind.REMIND, message, send_after=now)
        created += int(not before)
    return created


# --- 5. the single rate-limited sender ---------------------------------------
def _post_reply(client: InstagramClient, row: OutboxReply, *, swap: bool = False) -> str:
    """Post one reply. `swap` picks the other edge.

    Which edge is right depends on the Meta setup: with Facebook Login the
    `comments` webhook only covers our own media, while Instagram Login folds
    @mentions into it too — and those need the /mentions edge instead.
    """
    if (row.source == Source.MENTIONS) != swap:
        return client.reply_to_mention(
            media_id=row.media_id, comment_id=row.target_comment_id, message=row.message
        )
    return client.reply_to_comment(comment_id=row.target_comment_id, message=row.message)



def send_outbox(client: InstagramClient | None = None, now: datetime | None = None,
                limit: int | None = None, sleep=time_module.sleep) -> dict:
    conf = settings.REMINDME
    client = client or InstagramClient()
    now = now or timezone.now()
    counters = {"sent": 0, "retried": 0, "failed": 0, "throttled": 0}

    sent_last_hour = OutboxReply.objects.filter(
        status=OutboxReply.Status.SENT, sent_at__gte=now - timedelta(hours=1)
    ).count()
    budget = min(limit or conf["SEND_BATCH_SIZE"], max(conf["SEND_MAX_PER_HOUR"] - sent_last_hour, 0))
    if budget <= 0:
        counters["throttled"] = 1
        log.warning("hourly send budget exhausted (%s already sent)", sent_last_hour)
        return counters

    rows = list(
        OutboxReply.objects.filter(
            status=OutboxReply.Status.PENDING, send_after__lte=now, next_attempt_at__lte=now
        ).order_by("send_after", "id")[:budget]
    )
    for index, row in enumerate(rows):
        if index:
            sleep(conf["SEND_INTERVAL_SECONDS"])  # one sender, paced, as agreed
        if row.target_comment_id.startswith(SELFTEST_PREFIX):
            log.info("selftest reply suppressed (%s): %s", row.kind, row.message.splitlines()[0])
            _reply_sent(row, "selftest")
            counters["sent"] += 1
            continue
        try:
            reply_id = _post_reply(client, row)
        except InstagramError as exc:
            if exc.retryable:
                counters[_reply_failed(row, exc)] += 1
                log.warning("reply %s (%s) failed: %s", row.pk, row.kind, exc)
                continue
            try:  # wrong edge for this setup? try the other one before giving up
                reply_id = _post_reply(client, row, swap=True)
                log.info("reply %s went through the other edge", row.pk)
            except InstagramError as second:
                counters[_reply_failed(row, second)] += 1
                log.warning("reply %s (%s) failed on both edges: %s", row.pk, row.kind, second)
                continue
        _reply_sent(row, reply_id)
        counters["sent"] += 1
    return counters


def _reply_sent(row: OutboxReply, reply_id: str) -> None:
    row.status = OutboxReply.Status.SENT
    row.reply_comment_id = reply_id or ""
    row.sent_at = timezone.now()
    row.attempts += 1
    row.save(update_fields=["status", "reply_comment_id", "sent_at", "attempts"])
    if row.kind == OutboxReply.Kind.REMIND and row.reminder_id:
        Reminder.objects.filter(pk=row.reminder_id).update(
            status=Reminder.Status.SENT, sent_at=row.sent_at
        )


def _reply_failed(row: OutboxReply, exc: InstagramError) -> str:
    conf = settings.REMINDME
    row.attempts += 1
    row.last_error = str(exc)
    outcome = "retried"
    if exc.retryable and row.attempts < conf["MAX_ATTEMPTS"]:
        row.next_attempt_at = timezone.now() + _backoff(row.attempts)
    else:
        # Comment deleted, comments disabled, token revoked: stop trying.
        row.status = OutboxReply.Status.FAILED
        outcome = "failed"
        if row.kind == OutboxReply.Kind.REMIND and row.reminder_id:
            Reminder.objects.filter(pk=row.reminder_id).update(status=Reminder.Status.FAILED)
    row.save(update_fields=["attempts", "last_error", "next_attempt_at", "status"])
    return outcome


# --- 6. one full pass --------------------------------------------------------
def run_once(client: InstagramClient | None = None) -> dict:
    client = client or InstagramClient()
    counters = process_inbox(client)
    counters["due_queued"] = schedule_due()
    counters.update(send_outbox(client))
    return counters
