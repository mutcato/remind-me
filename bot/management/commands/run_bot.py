"""The worker: inbox -> schedule -> send, forever."""

import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from bot.instagram import InstagramClient
from bot.pipeline import run_once

log = logging.getLogger("bot.worker")


class Command(BaseCommand):
    help = "Process webhook events, queue due reminders and send replies."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=settings.REMINDME["POLL_INTERVAL_SECONDS"])
        parser.add_argument("--once", action="store_true", help="One pass, then exit (for cron).")

    def handle(self, *args, **options):
        stopping = {"now": False}

        def stop(signum, _frame):
            log.info("signal %s received, finishing this pass", signum)
            stopping["now"] = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

        client = InstagramClient()
        # An idle worker logs nothing, which reads as a dead worker. Say hello.
        log.info(
            "worker started: interval=%ss handle=%s endpoint=%s dry_run=%s",
            options["interval"], settings.REMINDME["BOT_HANDLE"], client.root, client.dry_run,
        )
        if client.dry_run:
            self.stdout.write(self.style.WARNING("DRY_RUN is on: nothing will be posted to Instagram."))

        while True:
            close_old_connections()  # uzun ömürlü döngü, Postgres yeniden başlayabilir
            counters = run_once(client)
            if any(counters.values()):
                log.info("pass: %s", counters)
            if options["once"] or stopping["now"]:
                break
            time.sleep(options["interval"])
        self.stdout.write(self.style.SUCCESS("worker stopped"))
