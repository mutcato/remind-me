import requests
from django.test import SimpleTestCase

from bot import net
from bot.instagram import InstagramClient, InstagramError

UNREACHABLE = requests.ConnectionError(
    "HTTPSConnectionPool(host='graph.facebook.com', port=443): Max retries exceeded with url: "
    "/v27.0/1?access_token=EAAgSECRETTOKENVALUE123456 "
    "(Caused by NewConnectionError('Failed to establish a new connection: [Errno 101] Network is unreachable'))"
)


class Response:
    status_code = 200

    def json(self):
        return {"id": "1", "username": "remindme.in"}


class Session:
    """Fails the first n calls with the unroutable-IPv6 error, then succeeds."""

    def __init__(self, failures, error=UNREACHABLE):
        self.failures = failures
        self.error = error
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return Response()


class Ipv4FallbackTests(SimpleTestCase):
    # force_ipv4 patches socket globally, so a test that leaves it on changes
    # the result of the next one. Clean both ends.
    def setUp(self):
        net.restore()

    def tearDown(self):
        net.restore()

    def make_client(self, session):  # not `client`: SimpleTestCase already owns that name
        return InstagramClient(ig_user_id="1", access_token="EAAgSECRETTOKENVALUE123456", session=session)

    def test_unroutable_ipv6_is_retried_over_ipv4(self):
        session = Session(failures=1)
        account = self.make_client(session).fetch_account()
        self.assertEqual(account["username"], "remindme.in")
        self.assertEqual(session.calls, 2)  # failed once, retried once

    def test_second_failure_surfaces_as_retryable_without_the_token(self):
        session = Session(failures=2)
        with self.assertRaises(InstagramError) as caught:
            self.make_client(session).fetch_account()
        self.assertTrue(caught.exception.retryable)
        self.assertIn("after IPv4 fallback", str(caught.exception))
        self.assertNotIn("EAAgSECRETTOKENVALUE123456", str(caught.exception))

    def test_fallback_is_applied_once_per_process(self):
        self.assertEqual(self.make_client(Session(failures=1)).fetch_account()["id"], "1")
        # Already forced: a second unroutable error must fail rather than loop.
        session = Session(failures=1)
        with self.assertRaises(InstagramError):
            self.make_client(session).fetch_account()
        self.assertEqual(session.calls, 1)

    def test_unrelated_network_errors_do_not_trigger_the_fallback(self):
        session = Session(failures=1, error=requests.ConnectionError("Read timed out"))
        with self.assertRaises(InstagramError) as caught:
            self.make_client(session).fetch_account()
        self.assertEqual(session.calls, 1)
        self.assertNotIn("IPv4 fallback", str(caught.exception))
