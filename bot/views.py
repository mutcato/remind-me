"""The webhook endpoint. Verify, store, return 200 — nothing heavier.

Interpreting an event means calling Instagram back, which is far slower than the
few seconds Meta allows, so the work happens in the worker (HANDOFF.md §3).
"""

import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import InboxEvent, OutboxReply, Reminder, Source
from .pipeline import enqueue
from .signature import verify_signature

log = logging.getLogger(__name__)
HANDLED_FIELDS = {Source.MENTIONS, Source.COMMENTS}


class InstagramWebhookView(APIView):
    """GET verifies the subscription, POST receives changes."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        conf = settings.REMINDME
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token") or ""
        challenge = request.query_params.get("hub.challenge") or ""
        expected = conf["VERIFY_TOKEN"]
        if mode == "subscribe" and expected and hmac.compare_digest(token, expected):
            return HttpResponse(challenge, content_type="text/plain")
        log.warning("webhook verification rejected (mode=%s)", mode)
        return Response({"detail": "verification failed"}, status=status.HTTP_403_FORBIDDEN)

    def post(self, request):
        raw = request.body
        if not verify_signature(settings.REMINDME["APP_SECRET"], raw, request.headers.get("X-Hub-Signature-256")):
            log.warning("webhook rejected: bad signature")
            return Response({"detail": "invalid signature"}, status=status.HTTP_403_FORBIDDEN)

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response({"detail": "invalid json"}, status=status.HTTP_400_BAD_REQUEST)

        if payload.get("object") != "instagram":
            return Response({"status": "ignored"})

        accepted = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                field = change.get("field")
                if field not in HANDLED_FIELDS:
                    continue
                if enqueue(field, change.get("value") or {}) is not None:
                    accepted += 1
        return Response({"status": "ok", "accepted": accepted})


class HealthView(APIView):
    """Queue depth at a glance — handy on a box you only reach over SSH."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "inbox_pending": InboxEvent.objects.filter(status=InboxEvent.Status.PENDING).count(),
                "inbox_failed": InboxEvent.objects.filter(status=InboxEvent.Status.FAILED).count(),
                "reminders_pending": Reminder.objects.filter(status=Reminder.Status.PENDING).count(),
                "reminders_sent": Reminder.objects.filter(status=Reminder.Status.SENT).count(),
                "reminders_failed": Reminder.objects.filter(status=Reminder.Status.FAILED).count(),
                "outbox_pending": OutboxReply.objects.filter(status=OutboxReply.Status.PENDING).count(),
                "outbox_failed": OutboxReply.objects.filter(status=OutboxReply.Status.FAILED).count(),
                "dry_run": settings.REMINDME["DRY_RUN"],
            }
        )
