"""Three tables, one per stage of the pipeline.

    InboxEvent   webhook landed, nothing interpreted yet (idempotency lives here)
    Reminder     a parsed request: "reply under this comment at this time"
    OutboxReply  one outgoing comment, sent by a single rate-limited worker
"""

from django.db import models
from django.utils import timezone


class Source(models.TextChoices):
    """Which webhook field delivered the comment."""

    MENTIONS = "mentions", "mentions"  # @mention on someone else's media
    COMMENTS = "comments", "comments"  # comment on our own media


class InboxEvent(models.Model):
    """A raw webhook change. `comment_id` is unique: Meta retries deliveries."""

    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        DONE = "done", "done"
        FAILED = "failed", "failed"

    source = models.CharField(max_length=16, choices=Source.choices)
    comment_id = models.CharField(max_length=64, unique=True)
    media_id = models.CharField(max_length=64, blank=True)
    payload = models.JSONField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self) -> str:
        return f"{self.source}:{self.comment_id} ({self.status})"


class Reminder(models.Model):
    """A reminder parsed out of one comment."""

    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        SENT = "sent", "sent"
        FAILED = "failed", "failed"
        IGNORED = "ignored", "ignored"

    comment_id = models.CharField(max_length=64, unique=True)
    media_id = models.CharField(max_length=64)
    source = models.CharField(max_length=16, choices=Source.choices)
    permalink = models.URLField(blank=True)
    requester_username = models.CharField(max_length=64, blank=True)
    comment_text = models.TextField()
    note = models.CharField(max_length=280, blank=True)
    # How the user phrased the delay ("3 gün"), echoed back in the reminder.
    delay_phrase = models.CharField(max_length=64, blank=True)
    locale = models.CharField(max_length=5, default="tr")
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    ignored_reason = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "due_at"])]

    def __str__(self) -> str:
        return f"{self.comment_id} -> {self.due_at:%Y-%m-%d %H:%M} ({self.status})" if self.due_at else self.comment_id


class OutboxReply(models.Model):
    """One comment waiting to be posted. The only place that talks back to Instagram."""

    class Kind(models.TextChoices):
        CONFIRM = "confirm", "confirm"  # "got it, I'll remind you at X"
        REMIND = "remind", "remind"  # the reminder itself, at due time
        HELP = "help", "help"  # usage hint when nothing could be parsed

    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        SENT = "sent", "sent"
        FAILED = "failed", "failed"

    reminder = models.ForeignKey(Reminder, on_delete=models.CASCADE, null=True, blank=True, related_name="replies")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    target_comment_id = models.CharField(max_length=64)
    media_id = models.CharField(max_length=64)
    source = models.CharField(max_length=16, choices=Source.choices)
    message = models.TextField()
    send_after = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    reply_comment_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # At most one confirm and one remind per reminder, however often the
            # scheduler runs or the webhook replays.
            models.UniqueConstraint(fields=["reminder", "kind"], name="one_reply_per_kind_per_reminder"),
        ]
        indexes = [models.Index(fields=["status", "send_after", "next_attempt_at"])]

    def __str__(self) -> str:
        return f"{self.kind} -> {self.target_comment_id} ({self.status})"
