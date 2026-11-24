"""Turn "@remindme.in 3 gün tarifi dene" into a due time plus a note.

Attempts run cheapest-first, the order agreed in HANDOFF.md §3:

    1. fixed rules  — relative durations, yarın/tomorrow, weekday names, next week
    2. dateparser   — Turkish and English free text
    3. nothing      — Result(kind="none"); the caller decides whether to answer

Everything is resolved in the user's timezone (Europe/Istanbul by default) and
returned as an aware datetime, so callers can store UTC without thinking about it.
"""

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import dateparser

# Uppercase -> lowercase without changing string length, so match spans stay
# valid. str.lower() breaks this for "İ", which lowercases to two codepoints.
_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZİIŞĞÜÖÇ", "abcdefghijklmnopqrstuvwxyzıışğüöç")

MENTION_RE = re.compile(r"@[\w.]+")
TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")
AMPM_RE = re.compile(r"\b(\d{1,2})\s*(am|pm)\b")

UNIT_TOKENS = {
    "second": ("second", "seconds", "sec", "secs", "saniye", "saniyeye", "sn"),
    "minute": ("minute", "minutes", "min", "mins", "dakika", "dakikaya", "dk"),
    "hour": ("hour", "hours", "hr", "hrs", "saat", "saate", "saatte"),
    "day": ("day", "days", "gün", "gun", "güne", "gune"),
    "week": ("week", "weeks", "hafta", "haftaya"),
    "month": ("month", "months", "ay", "aya"),
    "year": ("year", "years", "yıl", "yil", "yıla", "yila"),
}
# Single letters are accepted too ("3d", "2w"), but only glued to a number.
SHORT_UNITS = {"m": "minute", "h": "hour", "d": "day", "w": "week", "y": "year"}
TR_TOKENS = {
    "dakika", "dakikaya", "dk", "saat", "saate", "saatte", "gün", "gun", "güne", "gune",
    "hafta", "haftaya", "ay", "aya", "yıl", "yil", "yıla", "yila", "yarın", "yarin",
    "bugün", "bugun", "sonra", "haftaya", "gelecek", "önümüzdeki", "onumuzdeki", "aynı", "ayni",
}
UNIT_BY_TOKEN = {token: unit for unit, tokens in UNIT_TOKENS.items() for token in tokens}

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "half": 0.5,
    "bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4, "beş": 5, "bes": 5,
    "altı": 6, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10, "yarım": 0.5, "yarim": 0.5,
}
TR_NUMBER_WORDS = {"bir", "iki", "üç", "uc", "dört", "dort", "beş", "bes", "altı", "alti", "yedi", "sekiz", "dokuz", "on", "yarım", "yarim"}

WEEKDAYS = {
    "pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2, "perşembe": 3,
    "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6, "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}
TR_WEEKDAYS = {"pazartesi", "salı", "sali", "çarşamba", "carsamba", "perşembe", "persembe", "cuma", "cumartesi", "pazar"}

DAYPARTS = {
    "sabah": time(9, 0), "sabaha": time(9, 0), "öğlen": time(12, 0), "oglen": time(12, 0),
    "öğle": time(12, 0), "ogle": time(12, 0), "akşam": time(20, 0), "aksam": time(20, 0),
    "akşama": time(20, 0), "aksama": time(20, 0), "gece": time(23, 0), "geceye": time(23, 0),
    "morning": time(9, 0), "noon": time(12, 0), "afternoon": time(15, 0),
    "evening": time(20, 0), "night": time(23, 0), "tonight": time(21, 0),
}
TR_DAYPARTS = {"sabah", "sabaha", "öğlen", "oglen", "öğle", "ogle", "akşam", "aksam", "akşama", "aksama", "gece", "geceye"}

SAME_TOKENS = {"aynı", "ayni", "aynısı", "aynisi", "same", "metoo", "+1"}
TOMORROW_TOKENS = {"yarın", "yarin", "tomorrow"}
TODAY_TOKENS = {"bugün", "bugun", "today", "tonight"}
NEXT_WEEK_PATTERNS = ("gelecek hafta", "önümüzdeki hafta", "onumuzdeki hafta", "next week", "haftaya")
NEXT_MONTH_PATTERNS = ("gelecek ay", "önümüzdeki ay", "onumuzdeki ay", "next month", "aya")

# Glue words that are never a useful note on their own.
NOTE_STOPWORDS = {
    "in", "after", "later", "please", "pls", "remind", "me", "my", "the", "this", "that", "at", "on",
    "sonra", "sonraya", "içinde", "icinde", "lütfen", "lutfen", "bana", "beni", "bunu", "şunu", "sunu",
    "hatırlat", "hatirlat", "hatırlatır", "hatirlatir", "hatırlatırmısın", "hatirlatirmisin",
    "mısın", "misin", "musun", "müsün", "ol", "olur",
}


@dataclass
class Result:
    """What the parser understood. `due_at` is aware, in the user's timezone."""

    kind: str  # delay | absolute | same | none | too_far
    due_at: datetime | None = None
    note: str = ""
    locale: str = "tr"
    phrase: str = ""  # the time expression as the user wrote it
    units: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.kind in {"delay", "absolute", "same"}


def _fold(text: str) -> str:
    """Lowercase while keeping the string length (so regex spans stay usable)."""
    return text.translate(_FOLD)


def strip_handles(text: str) -> str:
    """Drop every @mention; the command is what is left."""
    return MENTION_RE.sub(" ", text)


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


def _add_units(base: datetime, units: dict) -> datetime:
    dt = base
    months = int(round(units.get("year", 0) * 12 + units.get("month", 0)))
    if months:
        dt = _add_months(dt, months)
    return dt + timedelta(
        seconds=units.get("second", 0),
        weeks=units.get("week", 0),
        days=units.get("day", 0),
        hours=units.get("hour", 0),
        minutes=units.get("minute", 0),
    )


def _explicit_time(folded: str, spans: list) -> time | None:
    """Find "21:14", "9.30" or "6pm" and record its span."""
    match = TIME_RE.search(folded)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour < 24 and minute < 60:
            spans.append(match.span())
            return time(hour, minute)
    match = AMPM_RE.search(folded)
    if match:
        hour = int(match.group(1)) % 12
        if match.group(2) == "pm":
            hour += 12
        spans.append(match.span())
        return time(hour, 0)
    return None


def _daypart(folded: str, spans: list) -> tuple[time | None, bool]:
    for token, at in DAYPARTS.items():
        match = re.search(rf"\b{re.escape(token)}\b", folded)
        if match:
            spans.append(match.span())
            return at, token in TR_DAYPARTS
    return None, False


def _relative_units(folded: str, spans: list) -> tuple[dict, bool]:
    """Collect every "<amount> <unit>" pair: "1 hafta 2 gün" adds up."""
    units: dict = {}
    turkish = False
    pattern = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?|[a-zçğıöşü]+)\s*([a-zçğıöşü]+)\b")
    for match in pattern.finditer(folded):
        raw_amount, raw_unit = match.group(1), match.group(2)
        unit = UNIT_BY_TOKEN.get(raw_unit)
        if unit is None:
            continue
        if raw_amount.replace(",", ".").replace(".", "", 1).isdigit():
            amount = float(raw_amount.replace(",", "."))
        else:
            if raw_amount not in NUMBER_WORDS:
                continue
            amount = NUMBER_WORDS[raw_amount]
            turkish = turkish or raw_amount in TR_NUMBER_WORDS
        units[unit] = units.get(unit, 0) + amount
        turkish = turkish or raw_unit in TR_TOKENS
        spans.append(match.span())

    # Glued short forms: "3d", "90m", "2w".
    for match in re.finditer(r"(?<![\w.])(\d+)\s*([mhdwy])\b", folded):
        unit = SHORT_UNITS[match.group(2)]
        if any(start <= match.start() < end for start, end in spans):
            continue
        units[unit] = units.get(unit, 0) + int(match.group(1))
        spans.append(match.span())
    return units, turkish


def _note_from(body: str, spans: list, max_chars: int) -> str:
    """Whatever the user wrote beyond the time expression."""
    keep = []
    for index, char in enumerate(body):
        if any(start <= index < end for start, end in spans):
            continue
        keep.append(char)
    leftover = "".join(keep)
    tokens = [t for t in re.split(r"\s+", leftover) if t]
    cleaned = [t for t in tokens if _fold(t).strip(".,!?;:\"'()") not in NOTE_STOPWORDS]
    note = " ".join(cleaned).strip(" .,!?;:-–—")
    note = re.sub(r"\s{2,}", " ", note)
    return note[:max_chars].strip()


def _detect_locale(folded: str, turkish_hit: bool, default: str) -> str:
    if turkish_hit:
        return "tr"
    words = set(re.findall(r"[a-zçğıöşü]+", folded))
    if words & TR_TOKENS or words & TR_WEEKDAYS or words & TR_DAYPARTS or words & TR_NUMBER_WORDS:
        return "tr"
    if any(unicodedata.name(ch, "").startswith("LATIN SMALL LETTER DOTLESS") for ch in folded):
        return "tr"
    ascii_words = {w for w in words if w.isascii()}
    if ascii_words & set(UNIT_BY_TOKEN) or ascii_words & set(WEEKDAYS) or ascii_words & set(DAYPARTS):
        return "en"
    return default


def parse(
    text: str,
    *,
    now: datetime,
    tz: str = "Europe/Istanbul",
    min_delay_seconds: int = 60,
    max_delay_days: int = 365,
    max_note_chars: int = 140,
    default_locale: str = "tr",
) -> Result:
    """Parse a comment into a due time. `now` may be naive-UTC-free: it is converted."""
    zone = ZoneInfo(tz)
    local_now = now.astimezone(zone)
    body = strip_handles(text).strip()
    folded = _fold(body)
    spans: list = []

    def finish(
        kind: str,
        due: datetime | None,
        turkish: bool,
        phrase: str = "",
        units: dict | None = None,
        note: str | None = None,
    ) -> Result:
        locale = _detect_locale(folded, turkish, default_locale)
        note = _note_from(body, spans, max_note_chars) if note is None else note
        if due is not None:
            earliest = local_now + timedelta(seconds=min_delay_seconds)
            if due < earliest:
                due = earliest
            if due > local_now + timedelta(days=max_delay_days):
                return Result(kind="too_far", due_at=None, note=note, locale=locale, phrase=phrase)
        return Result(kind=kind, due_at=due, note=note, locale=locale, phrase=phrase, units=units or {})

    # "aynı" / "same": join the last reminder on this post. The caller resolves it.
    for token in SAME_TOKENS:
        match = re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", folded)
        if match:
            spans.append(match.span())
            return finish("same", None, token in {"aynı", "ayni", "aynısı", "aynisi"}, phrase=token)

    # 1a. Relative durations: "3 gün", "2 hafta 3 saat", "90m".
    units, turkish = _relative_units(folded, spans)
    if units:
        phrase = " ".join(body[start:end].strip() for start, end in sorted(spans)) or body
        return finish("delay", _add_units(local_now, units), turkish, phrase=phrase.strip(), units=units)

    # 1b. Calendar keywords, each optionally carrying a time or a daypart.
    at_time = _explicit_time(folded, spans)
    daypart, daypart_tr = _daypart(folded, spans) if at_time is None else (None, False)
    clock = at_time or daypart

    def on_day(target: datetime) -> datetime:
        return target.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0) if clock else target

    for token in TOMORROW_TOKENS:
        match = re.search(rf"\b{re.escape(token)}\b", folded)
        if match:
            spans.append(match.span())
            return finish("absolute", on_day(local_now + timedelta(days=1)), daypart_tr or token != "tomorrow", phrase=token)

    for token in TODAY_TOKENS:
        match = re.search(rf"\b{re.escape(token)}\b", folded)
        if match and clock:
            spans.append(match.span())
            return finish("absolute", on_day(local_now), daypart_tr or token not in {"today", "tonight"}, phrase=token)

    for phrase_text in NEXT_WEEK_PATTERNS:
        match = re.search(rf"\b{re.escape(phrase_text)}\b", folded)
        if match:
            spans.append(match.span())
            return finish("absolute", on_day(local_now + timedelta(days=7)), "hafta" in phrase_text, phrase=phrase_text)

    for phrase_text in NEXT_MONTH_PATTERNS:
        match = re.search(rf"\b{re.escape(phrase_text)}\b", folded)
        if match:
            spans.append(match.span())
            return finish("absolute", on_day(_add_months(local_now, 1)), " ay" in phrase_text or phrase_text == "aya", phrase=phrase_text)

    for token, weekday in WEEKDAYS.items():
        match = re.search(rf"\b{re.escape(token)}\b", folded)
        if not match:
            continue
        spans.append(match.span())
        ahead = (weekday - local_now.weekday()) % 7
        candidate = on_day(local_now + timedelta(days=ahead))
        if candidate <= local_now:  # today, but the moment already passed
            candidate = on_day(local_now + timedelta(days=ahead + 7))
        return finish("absolute", candidate, daypart_tr or token in TR_WEEKDAYS, phrase=token)

    # A bare time with no day: the next time the clock shows it.
    if clock:
        candidate = on_day(local_now)
        if candidate <= local_now:
            candidate = on_day(local_now + timedelta(days=1))
        return finish("absolute", candidate, daypart_tr, phrase=f"{clock:%H:%M}")

    # 2. dateparser, for anything the rules above do not cover.
    spans.clear()
    parsed = dateparser.parse(
        body,
        languages=["tr", "en"],
        settings={
            "TIMEZONE": tz,
            "TO_TIMEZONE": tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": local_now.replace(tzinfo=None),
        },
    )
    if parsed is not None:
        return finish("absolute", parsed.astimezone(zone), False, phrase=body, note="")

    # 3. Nothing understood.
    return Result(kind="none", due_at=None, note="", locale=_detect_locale(folded, False, default_locale))
