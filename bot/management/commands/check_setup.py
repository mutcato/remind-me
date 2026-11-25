"""Is the Meta side wired up? Checks config, token, account and subscriptions.

    python manage.py check_setup
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from bot.instagram import InstagramClient, InstagramError

REQUIRED = [
    ("IG_APP_SECRET", "APP_SECRET", "webhook imzasını doğrulamak için"),
    ("IG_VERIFY_TOKEN", "VERIFY_TOKEN", "Meta webhook formundaki verify token"),
    ("IG_USER_ID", "IG_USER_ID", "@remindme.in hesabının Instagram id'si"),
    ("IG_ACCESS_TOKEN", "ACCESS_TOKEN", "uzun ömürlü access token"),
]
WANTED_FIELDS = {"comments", "mentions"}
# Values shipped in .env.example. Present but unedited is the same as missing,
# and the IG id one is easy to miss because it looks like a real id.
PLACEHOLDERS = {"changeme", "17841400000000000", "dev-only-insecure-key-change-me"}


def shape_of(value) -> str:
    """Describe a secret without printing it: length, ends, and anything odd."""
    raw = str(value or "")
    suspicious = []
    if raw != raw.strip():
        suspicious.append("baş/son boşluk")
    if any(ch.isspace() for ch in raw.strip()):
        suspicious.append("içinde boşluk veya satır sonu")
    if raw[:1] in {"'", '"'} or raw[-1:] in {"'", '"'}:
        suspicious.append("tırnak işareti")
    if "#" in raw:
        suspicious.append("# karakteri")
    if not raw.startswith("IGAA"):
        suspicious.append("'IGAA' ile başlamıyor (Instagram token'ları öyle başlar)")
    shape = f"uzunluk={len(raw)} baş={raw[:4]!r} son={raw[-4:]!r}"
    return shape + (f"\n         ŞÜPHELİ: {'; '.join(suspicious)}" if suspicious else "")


def is_filled(value) -> bool:
    text = str(value or "").strip()
    return bool(text) and text not in PLACEHOLDERS and not text.startswith("local-")


class Command(BaseCommand):
    help = "Verify the Meta app, token and webhook subscriptions this bot needs."

    def handle(self, *args, **options):
        conf = settings.REMINDME
        ok = self.style.SUCCESS("  ok  ")
        bad = self.style.ERROR(" eksik")
        warn = self.style.WARNING(" uyarı")
        failures = 0

        self.stdout.write(self.style.MIGRATE_HEADING("\n1. .env değerleri"))
        for env_name, key, why in REQUIRED:
            present = is_filled(conf[key])
            self.stdout.write(f"  [{ok if present else bad}] {env_name:18} {why}")
            failures += int(not present)

        # Django tarafı: üretimde varsayılan anahtar ve DEBUG açık olmamalı.
        if not is_filled(settings.SECRET_KEY):
            self.stdout.write(f"  [{bad}] DJANGO_SECRET_KEY  üretim için kendi anahtarını üret")
            failures += 1
        if settings.DEBUG:
            self.stdout.write(f"  [{warn}] DJANGO_DEBUG açık: üretimde false olmalı")

        self.stdout.write(f"\n  endpoint: {conf['GRAPH_BASE_URL']}/{conf['GRAPH_VERSION']}")
        self.stdout.write(f"  handle:   {conf['BOT_HANDLE']}   timezone: {conf['DEFAULT_TIMEZONE']}")
        if conf["DRY_RUN"]:
            self.stdout.write(f"  [{warn}] DRY_RUN açık: hiçbir yanıt Instagram'a gönderilmez")

        if failures:
            self.stdout.write(self.style.ERROR("\n.env tamamlanmadan token kontrolü yapılamaz.\n"))
            raise SystemExit(1)

        client = InstagramClient()

        self.stdout.write(self.style.MIGRATE_HEADING("\n2. Token ve hesap"))
        try:
            account = client.fetch_account()
            kind = account.get("account_type")
            self.stdout.write(f"  [{ok}] token geçerli, hesap: @{account.get('username', '?')} ({account.get('id')})")
            if kind:
                professional = kind.upper() in {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}
                self.stdout.write(f"  [{ok if professional else bad}] hesap tipi: {kind}")
                if not professional:
                    self.stdout.write(self.style.WARNING("         professional (Business/Creator) olmalı"))
                    failures += 1
        except InstagramError as exc:
            self.stdout.write(f"  [{bad}] Instagram reddetti: {exc}")
            self.stdout.write(f"         çağrı: GET {client.root}/{conf['IG_USER_ID']}?fields=...")
            if exc.code == 190:
                self.stdout.write(f"         token: {shape_of(conf['ACCESS_TOKEN'])}")
            self.stdout.write(
                self.style.WARNING(
                    "         kod 190 = token geçersiz/süresi dolmuş · kod 100 = IG_USER_ID yanlış\n"
                    "         kod 10/200 = izin eksik (instagram_manage_comments)"
                )
            )
            raise SystemExit(1)

        self.stdout.write(self.style.MIGRATE_HEADING("\n3. Webhook abonelikleri"))
        try:
            data = client.list_subscriptions().get("data") or []
            subscribed = {f for row in data for f in (row.get("subscribed_fields") or [])}
            self.stdout.write(f"  abone olunan alanlar: {', '.join(sorted(subscribed)) or '(hiçbiri)'}")
            for field in sorted(WANTED_FIELDS):
                here = field in subscribed
                self.stdout.write(f"  [{ok if here else bad}] {field}")
                failures += int(not here)
            if failures:
                self.stdout.write(
                    self.style.WARNING("\n  düzeltmek için: manage.py subscribe_webhook --fields comments,mentions")
                )
        except InstagramError as exc:
            self.stdout.write(f"  [{bad}] abonelikler okunamadı: {exc}")
            failures += 1

        if failures:
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("\nHazır: webhook geldiğinde bot çalışacak.\n"))
