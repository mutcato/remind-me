"""Outbound network tweaks.

A Raspberry Pi on a home connection often has AAAA records resolving fine while
no IPv6 route exists, so every other call dies with "Network is unreachable".
Fixing /etc/gai.conf is the tidier cure; this flag is the one that travels with
the app and needs no root.
"""

import socket

_original_getaddrinfo = None


def force_ipv4() -> bool:
    """Make every outbound connection resolve to IPv4 only. Idempotent."""
    global _original_getaddrinfo
    if _original_getaddrinfo is not None:
        return False
    _original_getaddrinfo = socket.getaddrinfo

    def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        results = _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        if not results:  # no A record at all: fall back rather than fail hard
            return _original_getaddrinfo(host, port, family, type, proto, flags)
        return results

    socket.getaddrinfo = ipv4_only
    return True


def restore() -> None:
    global _original_getaddrinfo
    if _original_getaddrinfo is not None:
        socket.getaddrinfo = _original_getaddrinfo
        _original_getaddrinfo = None
