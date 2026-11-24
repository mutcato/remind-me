"""Shared test scaffolding."""

from django.conf import settings

from bot.instagram import InstagramError


def conf(**overrides) -> dict:
    merged = dict(settings.REMINDME)
    merged.update(overrides)
    return merged


class FakeClient:
    """Stands in for InstagramClient: records what would be posted."""

    dry_run = False

    def __init__(self, *, comment: dict | None = None, error: InstagramError | None = None):
        self.comment = comment or {}
        self.error = error
        self.sent: list[tuple[str, str, str]] = []

    def fetch_mentioned_comment(self, comment_id: str) -> dict:
        if self.error:
            raise self.error
        return self.comment

    def reply_to_mention(self, *, media_id: str, comment_id: str, message: str) -> str:
        if self.error:
            raise self.error
        self.sent.append(("mentions", comment_id, message))
        return f"reply-{len(self.sent)}"

    def reply_to_comment(self, *, comment_id: str, message: str) -> str:
        if self.error:
            raise self.error
        self.sent.append(("comments", comment_id, message))
        return f"reply-{len(self.sent)}"
