"""Subscribe the Instagram account to the webhook fields the bot needs.

`mentions` is only a separate field in some Meta setups — with Instagram
business login it arrives inside the `comments` notification instead. Asking for
a field the setup does not know is rejected outright, so an invalid-field error
falls back to the fields that are valid rather than leaving nothing subscribed.
"""

from django.core.management.base import BaseCommand

from bot.instagram import InstagramClient, InstagramError

SETUP_DEPENDENT = "mentions"


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

            fields = [f.strip() for f in options["fields"].split(",") if f.strip()]
            self.stdout.write(f"subscribing to: {','.join(fields)}")
            try:
                result = client.subscribe(",".join(fields))
            except InstagramError as exc:
                if exc.code != 100 or SETUP_DEPENDENT not in fields:
                    raise
                remaining = [f for f in fields if f != SETUP_DEPENDENT]
                self.stdout.write(
                    self.style.WARNING(
                        f"  '{SETUP_DEPENDENT}' bu kurulumda geçerli bir alan değil ({exc}).\n"
                        f"  Bu kurulumda @mention'lar 'comments' bildiriminin içinde geliyor;\n"
                        f"  bot ikisini de karşılıyor. {','.join(remaining)} ile devam ediliyor."
                    )
                )
                result = client.subscribe(",".join(remaining))
            self.stdout.write(self.style.SUCCESS(str(result)))
            self.stdout.write(
                "\nBu çağrı aboneliğin HESAP katmanıdır (POST /{ig-user-id}/subscribed_apps).\n"
                "Teslimat için diğer iki koşul panelden sağlanmalı:\n"
                "  1. App Dashboard -> API setup with Unified login -> Webhooks: alan (mentions) işaretli\n"
                "  2. Uygulama Live (published) olmalı — Development modda app user'lara bildirim gitmez"
            )
        except InstagramError as exc:
            raise SystemExit(f"Instagram rejected the call: {exc}")
