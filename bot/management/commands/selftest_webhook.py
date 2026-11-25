"""Prove our own stack end to end, without Instagram.

    python manage.py selftest_webhook

Posts a correctly signed webhook payload to the public callback URL, exactly as
Meta would, then watches the database for the event to turn into a reminder and
a reply. It answers the one question the logs cannot: is the silence ours or
Meta's? Nothing is sent to Instagram — the synthetic comment id is recognised by
the sender and the reply is built but suppressed.
"""

import json
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from bot.models import InboxEvent, OutboxReply, Reminder, Source
from bot.pipeline import SELFTEST_PREFIX
from bot.signature import sign


class Command(BaseCommand):
    help = "Send a signed test webhook to the public callback URL and follow it through the pipeline."

    def add_arguments(self, parser):
        parser.add_argument("--url", default="https://remindme.nudgewire.com/webhooks/instagram/")
        parser.add_argument("--text", default=None, help="Comment text (default: '<handle> 1 hour').")
        parser.add_argument("--wait", type=float, default=45.0, help="Seconds to wait for the worker.")
        parser.add_argument("--keep", action="store_true", help="Leave the test rows in the database.")

    def handle(self, *args, **options):
        conf = settings.REMINDME
        secret = conf["APP_SECRET"]
        if not secret:
            raise SystemExit("IG_APP_SECRET boş — imza üretilemez.")

        comment_id = f"{SELFTEST_PREFIX}{timezone.now():%Y%m%d%H%M%S}"
        text = options["text"] or f"{conf['BOT_HANDLE']} 1 hour"
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": conf["IG_USER_ID"],
                    "time": int(timezone.now().timestamp()),
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": comment_id,
                                "text": text,
                                "from": {"id": "selftest-user", "username": "selftest"},
                                "media": {"id": f"{SELFTEST_PREFIX}media"},
                                "timestamp": timezone.now().strftime("%Y-%m-%dT%H:%M:%S+0000"),
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode()

        self.stdout.write(f"POST {options['url']}\n  comment_id={comment_id}\n  text={text!r}")
        try:
            response = requests.post(
                options["url"],
                data=body,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign(secret, body)},
                timeout=20,
            )
        except requests.RequestException as exc:
            raise SystemExit(f"  [HATA] istek gitmedi: {exc}\n  -> nginx/DNS/TLS katmanına bak")

        self.stdout.write(f"  HTTP {response.status_code} {response.text[:200]}")
        if response.status_code == 403:
            raise SystemExit("  [HATA] imza reddedildi -> .env'deki IG_APP_SECRET paneldekiyle aynı değil")
        if response.status_code != 200:
            raise SystemExit("  [HATA] endpoint 200 dönmedi -> Django log'una bak")

        if not InboxEvent.objects.filter(comment_id=comment_id).exists():
            raise SystemExit("  [HATA] 200 döndü ama InboxEvent yok -> web servisi başka bir veritabanına yazıyor")
        self.stdout.write(self.style.SUCCESS("  InboxEvent yazıldı"))

        deadline = time.monotonic() + options["wait"]
        self.stdout.write(f"  worker bekleniyor ({options['wait']:.0f}s)...")
        reminder = None
        while time.monotonic() < deadline:
            reminder = Reminder.objects.filter(comment_id=comment_id).first()
            if reminder:
                break
            time.sleep(2)

        if reminder is None:
            raise SystemExit(
                "  [HATA] olay işlenmedi -> worker çalışmıyor olabilir:\n"
                "         systemctl status remindme-worker"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"  Reminder: status={reminder.status} due={reminder.due_at:%Y-%m-%d %H:%M} "
                f"phrase={reminder.delay_phrase!r}"
            )
        )
        for row in OutboxReply.objects.filter(target_comment_id=comment_id).order_by("id"):
            self.stdout.write(f"  OutboxReply [{row.kind}] {row.status}:\n    {row.message.splitlines()[0]}")

        if not options["keep"]:
            Reminder.objects.filter(comment_id=comment_id).delete()
            InboxEvent.objects.filter(comment_id=comment_id).delete()
            self.stdout.write("  test kayıtları temizlendi (--keep ile saklanabilir)")

        self.stdout.write(
            self.style.SUCCESS(
                "\nYığın sağlam: nginx -> imza -> Django -> kuyruk -> worker -> yanıt metni.\n"
                "Gerçek yorumda sessizlik varsa sebep Meta tarafında: abonelik veya Live modu."
            )
        )
