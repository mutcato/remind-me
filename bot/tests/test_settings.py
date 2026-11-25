from django.test import SimpleTestCase

from config.settings import parse_database_url


class DatabaseUrlTests(SimpleTestCase):
    def test_full_url(self):
        entry = parse_database_url("postgres://remindme:s3cret@127.0.0.1:5432/remindme")
        self.assertEqual(entry["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(entry["NAME"], "remindme")
        self.assertEqual(entry["USER"], "remindme")
        self.assertEqual(entry["PASSWORD"], "s3cret")
        self.assertEqual(entry["HOST"], "127.0.0.1")
        self.assertEqual(entry["PORT"], "5432")

    def test_percent_encoded_password_survives(self):
        entry = parse_database_url("postgresql://u:p%40ss%2Fword@db.internal:5432/remindme")
        self.assertEqual(entry["PASSWORD"], "p@ss/word")
        self.assertEqual(entry["HOST"], "db.internal")

    def test_query_parameters_become_options(self):
        entry = parse_database_url("postgres://u:p@h:5432/db?sslmode=require&connect_timeout=5")
        self.assertEqual(entry["OPTIONS"], {"sslmode": "require", "connect_timeout": "5"})

    def test_unknown_scheme_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_database_url("mysql://u:p@h/db")


class DotenvTests(SimpleTestCase):
    """A mangled .env value is the likeliest cause of a "cannot parse" token."""

    def parse(self, body: str) -> dict:
        import os
        import tempfile
        from pathlib import Path

        from config.settings import load_dotenv

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(body, encoding="utf-8")
            keys = [line.split("=")[0].strip().removeprefix("export ") for line in body.splitlines() if "=" in line]
            for key in keys:
                os.environ.pop(key, None)
            load_dotenv(path)
            values = {key: os.environ.get(key) for key in keys}
            for key in keys:
                os.environ.pop(key, None)
            return values

    def test_plain_value(self):
        self.assertEqual(self.parse("IG_ACCESS_TOKEN=IGAAabc123\n")["IG_ACCESS_TOKEN"], "IGAAabc123")

    def test_trailing_comment_is_dropped(self):
        self.assertEqual(self.parse("IG_ACCESS_TOKEN=IGAAabc123 # panelden\n")["IG_ACCESS_TOKEN"], "IGAAabc123")

    def test_quotes_are_stripped_but_inner_hash_survives(self):
        self.assertEqual(self.parse('IG_APP_SECRET="a#b#c"\n')["IG_APP_SECRET"], "a#b#c")

    def test_export_prefix(self):
        self.assertEqual(self.parse("export IG_USER_ID=17841408569723823\n")["IG_USER_ID"], "17841408569723823")

    def test_surrounding_whitespace(self):
        self.assertEqual(self.parse("IG_USER_ID =  17841408569723823  \n")["IG_USER_ID"], "17841408569723823")
