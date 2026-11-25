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
