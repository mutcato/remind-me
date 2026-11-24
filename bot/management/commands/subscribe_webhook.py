"""Subscribe the Instagram account to the webhook fields the bot needs."""

from django.core.management.base import BaseCommand

from bot.instagram import InstagramClient, InstagramError


class Command(BaseCommand):
    help = "Show and set the webhook fields this Instagram account is subscribed to."

    def add_arguments(self, parser):
        parser.add_argument("--fields", default="comments,mentions")
        parser.add_argument("--show", action="store_true", help="Only print current subscriptions.")

    def handle(self, *args, **options):
        client = InstagramClient()
        try:
            self.stdout.write(f"current: {client.list_subscriptions()}")
            if options["show"]:
                return
            self.stdout.write(f"subscribing to: {options['fields']}")
            self.stdout.write(self.style.SUCCESS(str(client.subscribe(options["fields"]))))
        except InstagramError as exc:
            raise SystemExit(f"Instagram rejected the call: {exc}")
