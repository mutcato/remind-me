from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from bot import messages

DUE = datetime(2026, 11, 26, 18, 14, tzinfo=timezone.utc)  # 21:14 in Istanbul


class MessageTests(SimpleTestCase):
    def test_turkish_date_and_locative(self):
        when, loc = messages.format_when(DUE, "Europe/Istanbul", "tr")
        self.assertEqual(when, "26 Kasım Perşembe 21:14")
        self.assertEqual(loc, "'te")

    def test_locative_follows_vowel_harmony(self):
        self.assertEqual([messages.locative(m) for m in (0, 14, 30, 40)], ["'da", "'te", "'da", "'ta"])

    def test_confirm_states_the_resolved_time(self):
        text = messages.confirm_text(
            due_at=DUE, locale="tr", tz="Europe/Istanbul", handle="@remindme.in", note="", key="1"
        )
        self.assertIn("26 Kasım Perşembe 21:14", text)

    def test_note_is_echoed_back(self):
        text = messages.confirm_text(
            due_at=DUE, locale="tr", tz="Europe/Istanbul", handle="@remindme.in", note="tarifi dene", key="1"
        )
        self.assertIn("tarifi dene", text)

    def test_variant_is_stable_per_comment_but_differs_across_comments(self):
        kwargs = dict(due_at=DUE, locale="tr", tz="Europe/Istanbul", handle="@remindme.in", note="")
        first = messages.confirm_text(key="17918718562020960", **kwargs)
        self.assertEqual(first, messages.confirm_text(key="17918718562020960", **kwargs))
        variants = {messages.confirm_text(key=str(i), **kwargs) for i in range(10)}
        self.assertGreater(len(variants), 1)

    def test_reminder_uses_the_users_own_phrase(self):
        text = messages.remind_text(elapsed=timedelta(days=3), phrase="3 gün", locale="tr", note="", key="1")
        self.assertIn("3 gün önce", text)

    def test_reminder_humanizes_when_there_is_no_phrase(self):
        text = messages.remind_text(elapsed=timedelta(days=2, hours=3), phrase="", locale="en", note="", key="1")
        self.assertIn("2 days ago", text)

    def test_long_text_is_clipped_to_instagram_limit(self):
        clipped = messages.remind_text(
            elapsed=timedelta(days=1), phrase="x" * 3000, locale="tr", note="", key="1"
        )
        self.assertLessEqual(len(clipped), messages.MAX_COMMENT_CHARS)
