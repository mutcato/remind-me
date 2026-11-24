import json

from django.test import TestCase, override_settings
from django.urls import reverse

from bot.models import InboxEvent, Source
from bot.signature import sign

from .helpers import conf

SECRET = "app-secret"
TOKEN = "verify-token"
SETTINGS = conf(APP_SECRET=SECRET, VERIFY_TOKEN=TOKEN, IG_USER_ID="17841400000000000")

MENTION_PAYLOAD = {
    "object": "instagram",
    "entry": [
        {
            "id": "17841400000000000",
            "time": 1795527327,
            "changes": [{"field": "mentions", "value": {"media_id": "media-1", "comment_id": "comment-1"}}],
        }
    ],
}


@override_settings(REMINDME=SETTINGS)
class VerificationTests(TestCase):
    def test_challenge_is_echoed(self):
        response = self.client.get(
            reverse("instagram-webhook"),
            {"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "1158201444"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"1158201444")

    def test_wrong_token_is_refused(self):
        response = self.client.get(
            reverse("instagram-webhook"),
            {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
        )
        self.assertEqual(response.status_code, 403)


@override_settings(REMINDME=SETTINGS)
class DeliveryTests(TestCase):
    def post(self, payload, *, secret=SECRET, header=None):
        body = json.dumps(payload).encode()
        return self.client.post(
            reverse("instagram-webhook"),
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": header or sign(secret, body)},
        )

    def test_mention_is_stored(self):
        response = self.post(MENTION_PAYLOAD)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accepted"], 1)
        event = InboxEvent.objects.get()
        self.assertEqual((event.comment_id, event.media_id, event.source), ("comment-1", "media-1", Source.MENTIONS))
        self.assertEqual(event.status, InboxEvent.Status.PENDING)

    def test_replayed_delivery_is_ignored(self):
        self.post(MENTION_PAYLOAD)
        again = self.post(MENTION_PAYLOAD)
        self.assertEqual(again.json()["accepted"], 0)
        self.assertEqual(InboxEvent.objects.count(), 1)

    def test_comment_on_own_media_is_stored(self):
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "17841400000000000",
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": "comment-2",
                                "text": "@remindme.in 3 gün",
                                "from": {"id": "user-9", "username": "ahmet"},
                                "media": {"id": "media-2"},
                            },
                        }
                    ],
                }
            ],
        }
        self.assertEqual(self.post(payload).json()["accepted"], 1)
        self.assertEqual(InboxEvent.objects.get().source, Source.COMMENTS)

    def test_bad_signature_is_refused(self):
        self.assertEqual(self.post(MENTION_PAYLOAD, secret="wrong").status_code, 403)
        self.assertEqual(self.post(MENTION_PAYLOAD, header="sha256=deadbeef").status_code, 403)
        self.assertFalse(InboxEvent.objects.exists())

    def test_unknown_object_is_acknowledged_but_dropped(self):
        response = self.post({"object": "page", "entry": []})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(InboxEvent.objects.exists())

    def test_unhandled_field_is_dropped(self):
        payload = {
            "object": "instagram",
            "entry": [{"changes": [{"field": "story_insights", "value": {"id": "x"}}]}],
        }
        self.assertEqual(self.post(payload).json()["accepted"], 0)
        self.assertFalse(InboxEvent.objects.exists())

    def test_invalid_json_is_a_bad_request(self):
        body = b"{not json"
        response = self.client.post(
            reverse("instagram-webhook"),
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sign(SECRET, body)},
        )
        self.assertEqual(response.status_code, 400)

    def test_health_reports_queue_depth(self):
        self.post(MENTION_PAYLOAD)
        body = self.client.get(reverse("healthz")).json()
        self.assertEqual(body["inbox_pending"], 1)
        self.assertEqual(body["reminders_pending"], 0)
