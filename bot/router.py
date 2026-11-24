"""Which bot owns a comment.

One Meta app and one server can serve several handles (HANDOFF.md §2), so the
handle in the comment picks the handler. @remindme.in registers itself in
`bot.pipeline`; a second bot (e.g. @haritama) only has to call `register()`.
"""

import re
from typing import Callable

_HANDLERS: dict[str, Callable] = {}


def register(handle: str, handler: Callable) -> None:
    _HANDLERS[handle.lower().lstrip("@")] = handler


def registered_handles() -> list[str]:
    return [f"@{h}" for h in _HANDLERS]


def resolve(text: str) -> tuple[str, Callable] | None:
    """Return (handle, handler) for the first registered handle mentioned."""
    mentioned = {m.lower() for m in re.findall(r"@([\w.]+)", text or "")}
    for handle, handler in _HANDLERS.items():
        if handle in mentioned:
            return f"@{handle}", handler
    return None
