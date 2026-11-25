import socket

from django.test import SimpleTestCase

from bot import net

V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("157.240.238.63", 443))
V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2a03:2880::1", 443, 0, 0))


class ForceIpv4Tests(SimpleTestCase):
    def setUp(self):
        self._real_getaddrinfo = socket.getaddrinfo
        net.restore()

    def tearDown(self):
        net.restore()
        socket.getaddrinfo = self._real_getaddrinfo  # the fake resolver must not outlive the test

    def patch_resolver(self, results, v4_results=None):
        calls = []

        def fake(host, port, family=0, type=0, proto=0, flags=0):
            calls.append(family)
            if family == socket.AF_INET:
                return v4_results if v4_results is not None else [V4]
            return results

        net.socket.getaddrinfo = fake
        net._original_getaddrinfo = None
        return calls

    def test_only_ipv4_is_returned(self):
        self.patch_resolver([V4, V6])
        self.assertTrue(net.force_ipv4())
        self.assertEqual(socket.getaddrinfo("graph.facebook.com", 443), [V4])

    def test_calling_twice_does_not_stack_wrappers(self):
        self.patch_resolver([V4, V6])
        self.assertTrue(net.force_ipv4())
        self.assertFalse(net.force_ipv4())

    def test_ipv6_only_host_still_resolves(self):
        self.patch_resolver([V6], v4_results=[])
        net.force_ipv4()
        self.assertEqual(socket.getaddrinfo("v6only.example", 443), [V6])

    def test_restore_puts_the_resolver_back(self):
        original = socket.getaddrinfo
        self.patch_resolver([V4, V6])
        patched = socket.getaddrinfo
        net.force_ipv4()
        net.restore()
        self.assertIs(socket.getaddrinfo, patched)
        socket.getaddrinfo = original
