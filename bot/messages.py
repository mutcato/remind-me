"""Reply text. Templates, not an LLM — cheaper, controllable, and each kind has a
couple of variants so repeated replies are not byte-identical (HANDOFF.md §3)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MAX_COMMENT_CHARS = 2200  # Instagram's own limit

TR_MONTHS = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)
TR_WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")

# Turkish locative suffix, picked from how the minute is read out loud:
# 21:14 -> "on dört'TE", 21:30 -> "otuz'DA", 21:40 -> "kırk'TA".
_LOCATIVE_ONES = {1: "de", 2: "de", 3: "te", 4: "te", 5: "te", 6: "da", 7: "de", 8: "de", 9: "da"}
_LOCATIVE_TENS = {0: "da", 1: "da", 2: "de", 3: "da", 4: "ta", 5: "de"}

TR_UNITS = (("yıl", 31536000), ("ay", 2592000), ("hafta", 604800), ("gün", 86400), ("saat", 3600), ("dakika", 60))
EN_UNITS = (("year", 31536000), ("month", 2592000), ("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60))

CONFIRM = {
    "tr": (
        "⏰ Tamam, {when}{loc} hatırlatırım.{note}\nSen de hatırlatılmak istersen \"{handle} aynı\" yaz.",
        "⏰ Not aldım: {when}{loc} buraya tekrar yazacağım.{note}",
    ),
    "en": (
        "⏰ Got it — I'll remind you on {when}.{note}\nWant the same nudge? Comment \"{handle} same\".",
        "⏰ Noted. I'll come back to this comment on {when}.{note}",
    ),
}

REMIND = {
    "tr": (
        "🔔 Hatırlatma: {ago} önce bu postu işaretlemiştin.{note}",
        "🔔 Hatırlatma! {ago} önce buraya dönmek istemiştin.{note}",
    ),
    "en": (
        "🔔 Reminder: {ago} ago you asked me to bring you back to this post.{note}",
        "🔔 Here's your nudge — you flagged this post {ago} ago.{note}",
    ),
}

NOTE_SUFFIX = {"tr": " Notun: “{note}”", "en": " Your note: “{note}”"}

HELP = {
    "tr": "Selam! Ne zaman hatırlatayım? Örnek: “{handle} 3 gün”, “{handle} yarın 09:00”, “{handle} cuma akşam”.",
    "en": "Hi! Tell me when: “{handle} 3 days”, “{handle} tomorrow 09:00”, “{handle} friday evening”.",
}

TOO_FAR = {
    "tr": "En fazla {days} gün sonrasına hatırlatma kurabiliyorum. Daha yakın bir süre yazar mısın?",
    "en": "I can only set reminders up to {days} days ahead. Could you pick a closer time?",
}


def locative(minute: int) -> str:
    """The apostrophe suffix for a time in Turkish: 21:14 -> “'te”."""
    ones, tens = minute % 10, minute // 10
    suffix = _LOCATIVE_ONES[ones] if ones else _LOCATIVE_TENS.get(tens, "da")
    return f"'{suffix}"


def format_when(due_at: datetime, tz: str, locale: str) -> tuple[str, str]:
    """Return the human date and (for Turkish) its locative suffix."""
    local = due_at.astimezone(ZoneInfo(tz))
    if locale == "tr":
        when = f"{local.day} {TR_MONTHS[local.month - 1]} {TR_WEEKDAYS[local.weekday()]} {local:%H:%M}"
        return when, locative(local.minute)
    return f"{local:%a %d %b, %H:%M}", ""


def humanize_delta(delta: timedelta, locale: str) -> str:
    """Largest sensible unit only: “3 gün”, “2 hours”."""
    seconds = max(int(delta.total_seconds()), 60)
    table = TR_UNITS if locale == "tr" else EN_UNITS
    for name, size in table:
        if seconds >= size:
            amount = round(seconds / size)
            if locale == "tr":
                return f"{amount} {name}"
            return f"{amount} {name}" if amount == 1 else f"{amount} {name}s"
    return "1 dakika" if locale == "tr" else "1 minute"


def _variant(options: tuple, key: str) -> str:
    """Stable per-comment choice, so a retry sends the same text."""
    return options[sum(ord(ch) for ch in key) % len(options)]


def _note_part(note: str, locale: str) -> str:
    return NOTE_SUFFIX[locale].format(note=note) if note else ""


def _clip(text: str) -> str:
    return text if len(text) <= MAX_COMMENT_CHARS else text[: MAX_COMMENT_CHARS - 1] + "…"


def confirm_text(*, due_at: datetime, locale: str, tz: str, handle: str, note: str, key: str) -> str:
    locale = locale if locale in CONFIRM else "tr"
    when, loc = format_when(due_at, tz, locale)
    template = _variant(CONFIRM[locale], key)
    return _clip(template.format(when=when, loc=loc, handle=handle, note=_note_part(note, locale)))


def remind_text(*, elapsed: timedelta, phrase: str, locale: str, note: str, key: str) -> str:
    locale = locale if locale in REMIND else "tr"
    ago = phrase or humanize_delta(elapsed, locale)
    template = _variant(REMIND[locale], key)
    return _clip(template.format(ago=ago, note=_note_part(note, locale)))


def help_text(*, locale: str, handle: str) -> str:
    return _clip(HELP.get(locale, HELP["tr"]).format(handle=handle))


def too_far_text(*, locale: str, days: int) -> str:
    return _clip(TOO_FAR.get(locale, TOO_FAR["tr"]).format(days=days))
