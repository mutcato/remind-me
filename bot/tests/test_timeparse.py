from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from bot import timeparse

# A Tuesday, 00:14 in Europe/Istanbul.
NOW = datetime(2026, 11, 24, 21, 14, tzinfo=timezone.utc)


def parse(text: str, **kwargs):
    return timeparse.parse(text, now=NOW, **kwargs)


class RelativeDelayTests(SimpleTestCase):
    def test_turkish_days(self):
        result = parse("@remindme.in 3 gün")
        self.assertEqual(result.kind, "delay")
        self.assertEqual(result.locale, "tr")
        self.assertEqual(result.phrase, "3 gün")
        self.assertEqual(result.due_at - NOW, timedelta(days=3))

    def test_english_days_with_filler_word(self):
        result = parse("@remindme.in in 3 days")
        self.assertEqual(result.kind, "delay")
        self.assertEqual(result.locale, "en")
        self.assertEqual(result.due_at - NOW, timedelta(days=3))

    def test_units_add_up(self):
        self.assertEqual(parse("@remindme.in 1 hafta 2 gün").due_at - NOW, timedelta(days=9))

    def test_glued_short_form(self):
        self.assertEqual(parse("@remindme.in 90m").due_at - NOW, timedelta(minutes=90))

    def test_number_word(self):
        self.assertEqual(parse("@remindme.in bir saat sonra").due_at - NOW, timedelta(hours=1))

    def test_month_is_calendar_aware(self):
        # 25 Nov local + 1 month = 25 Dec local, not +30 days.
        due = parse("@remindme.in 1 ay").due_at
        self.assertEqual((due.month, due.day), (12, 25))

    def test_note_is_whatever_is_left(self):
        result = parse("@remindme.in 3 gün tarifi dene")
        self.assertEqual(result.note, "tarifi dene")

    def test_sub_minute_is_clamped_to_minimum(self):
        result = parse("@remindme.in 10 saniye", min_delay_seconds=60)
        self.assertEqual(result.due_at - NOW, timedelta(seconds=60))

    def test_beyond_the_horizon_is_rejected(self):
        self.assertEqual(parse("@remindme.in 5 yıl", max_delay_days=365).kind, "too_far")


class CalendarTests(SimpleTestCase):
    def test_tomorrow_with_clock(self):
        due = parse("@remindme.in yarın 09:00").due_at
        self.assertEqual((due.day, due.hour, due.minute), (26, 9, 0))

    def test_weekday_with_daypart(self):
        # Tuesday -> Friday 20:00 local.
        due = parse("@remindme.in cuma akşam").due_at
        self.assertEqual((due.day, due.hour), (27, 20))

    def test_weekday_with_ampm(self):
        due = parse("@remindme.in friday 6pm").due_at
        self.assertEqual((due.day, due.hour), (27, 18))

    def test_next_week(self):
        self.assertEqual(parse("@remindme.in next week").due_at - NOW, timedelta(days=7))

    def test_bare_clock_rolls_to_next_occurrence(self):
        due = parse("@remindme.in 09:00").due_at
        self.assertEqual((due.day, due.hour), (25, 9))

    def test_dateparser_handles_explicit_dates(self):
        result = parse("@remindme.in 24 aralık")
        self.assertEqual(result.kind, "absolute")
        self.assertEqual((result.due_at.month, result.due_at.day), (12, 24))
        self.assertEqual(result.note, "")


class SpecialCommandTests(SimpleTestCase):
    def test_same(self):
        for text in ("@remindme.in aynı", "@remindme.in same"):
            self.assertEqual(parse(text).kind, "same")

    def test_nothing_to_parse(self):
        result = parse("@remindme.in çok güzelmiş")
        self.assertEqual(result.kind, "none")
        self.assertIsNone(result.due_at)

    def test_other_handles_are_stripped(self):
        result = parse("@ahmet bak @remindme.in 2 saat")
        self.assertEqual(result.due_at - NOW, timedelta(hours=2))
        self.assertEqual(result.note, "bak")
