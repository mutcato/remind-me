from django.test import SimpleTestCase

from bot.signature import sign, verify_signature

SECRET = "app-secret"
BODY = b'{"object":"instagram"}'


class SignatureTests(SimpleTestCase):
    def test_round_trip(self):
        self.assertTrue(verify_signature(SECRET, BODY, sign(SECRET, BODY)))

    def test_rejects_other_secret(self):
        self.assertFalse(verify_signature(SECRET, BODY, sign("other", BODY)))

    def test_rejects_tampered_body(self):
        self.assertFalse(verify_signature(SECRET, BODY + b" ", sign(SECRET, BODY)))

    def test_rejects_missing_or_malformed_header(self):
        self.assertFalse(verify_signature(SECRET, BODY, None))
        self.assertFalse(verify_signature(SECRET, BODY, "sha1=deadbeef"))
        self.assertFalse(verify_signature(SECRET, BODY, "sha256="))

    def test_rejects_empty_secret(self):
        self.assertFalse(verify_signature("", BODY, sign("", BODY)))
