"""The only module that talks to Instagram.

Three calls carry the whole bot (verified against the Instagram Platform docs):

    GET  /{ig-user-id}?fields=mentioned_comment.comment_id(<id>){...}  read the comment
    POST /{ig-user-id}/mentions                                        reply to an @mention
    POST /{ig-comment-id}/replies                                      reply on our own media
"""

import logging

import requests
from django.conf import settings

from .net import force_ipv4

log = logging.getLogger(__name__)

# A host with AAAA records and no IPv6 route fails like this. The fix is known,
# so the client applies it itself rather than waiting for a config change.
UNROUTABLE = ("network is unreachable", "errno 101", "address family not supported")

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
# 1,2 = transient/unknown  4,17,32,613 = rate limits  341 = application limit
RETRYABLE_CODES = {1, 2, 4, 17, 32, 341, 613}

MENTIONED_COMMENT_FIELDS = "id,text,timestamp,media{id,permalink}"


def redact(text: str, *secrets: str) -> str:
    """Never let an access token reach a log line or an exception message."""
    cleaned = str(text)
    for secret in secrets:
        if secret and len(secret) > 8:
            cleaned = cleaned.replace(secret, f"{secret[:4]}...REDACTED")
    return cleaned


class InstagramError(Exception):
    def __init__(self, message, *, status=None, code=None, subcode=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode
        self.retryable = retryable

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.status:
            parts.append(f"http={self.status}")
        if self.code:
            parts.append(f"code={self.code}")
        if self.subcode:
            parts.append(f"subcode={self.subcode}")
        parts.append("retryable" if self.retryable else "permanent")
        return " ".join(parts)


class InstagramClient:
    def __init__(self, *, base_url=None, version=None, ig_user_id=None, access_token=None,
                 timeout=None, dry_run=None, session=None):
        conf = settings.REMINDME
        self.base_url = (base_url or conf["GRAPH_BASE_URL"]).rstrip("/")
        self.version = version or conf["GRAPH_VERSION"]
        self.ig_user_id = ig_user_id or conf["IG_USER_ID"]
        self.access_token = access_token or conf["ACCESS_TOKEN"]
        self.timeout = timeout if timeout is not None else conf["REQUEST_TIMEOUT"]
        self.dry_run = conf["DRY_RUN"] if dry_run is None else dry_run
        self.session = session or requests.Session()

    @property
    def root(self) -> str:
        return f"{self.base_url}/{self.version}"

    def _call(self, method: str, path: str, *, params=None, data=None) -> dict:
        url = f"{self.root}/{path.lstrip('/')}"
        payload = dict(data or {})
        query = dict(params or {})
        (payload if method == "POST" else query)["access_token"] = self.access_token
        def send():
            return self.session.request(
                method, url, params=query or None, data=payload if method == "POST" else None, timeout=self.timeout
            )

        try:
            response = send()
        except requests.RequestException as exc:  # DNS, TLS, timeout — always worth a retry
            # No IPv6 route: resolve IPv4-only from here on and try once more.
            # force_ipv4() returns False if it is already in effect, so a real
            # outage still fails instead of looping.
            if any(hint in str(exc).lower() for hint in UNROUTABLE) and force_ipv4():
                log.warning("IPv6 unroutable from this host; resolving IPv4-only from now on")
                try:
                    response = send()
                except requests.RequestException as retry_exc:
                    raise InstagramError(
                        f"request failed after IPv4 fallback: {redact(retry_exc, self.access_token)}",
                        retryable=True,
                    ) from None
            else:
                # The exception text embeds the request URL, token and all.
                raise InstagramError(
                    f"request failed: {redact(exc, self.access_token)}", retryable=True
                ) from None

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or "error" in body:
            error = body.get("error") or {}
            code = error.get("code")
            raise InstagramError(
                redact(error.get("message") or f"HTTP {response.status_code}", self.access_token),
                status=response.status_code,
                code=code,
                subcode=error.get("error_subcode"),
                retryable=response.status_code in RETRYABLE_STATUS or code in RETRYABLE_CODES,
            )
        return body

    # --- reads ---------------------------------------------------------------
    def fetch_mentioned_comment(self, comment_id: str) -> dict:
        """Text and media of a comment we were @mentioned in.

        Note: this endpoint returns id/text/timestamp/like_count/media only —
        there is no `from`, so the commenter's username is not available here.
        """
        body = self._call(
            "GET",
            self.ig_user_id,
            params={"fields": f"mentioned_comment.comment_id({comment_id}){{{MENTIONED_COMMENT_FIELDS}}}"},
        )
        comment = body.get("mentioned_comment")
        if not comment:
            raise InstagramError("mentioned_comment missing from response (deleted or comments disabled)")
        return comment

    def fetch_account(self) -> dict:
        """Who the token belongs to. Cheapest possible token check.

        `account_type` is not offered by every setup, so a rejected field falls
        back to the two fields every setup has rather than failing the check.
        """
        try:
            return self._call("GET", self.ig_user_id, params={"fields": "id,username,account_type"})
        except InstagramError as exc:
            if exc.code != 100:  # 100 = unknown/invalid field
                raise
        return self._call("GET", self.ig_user_id, params={"fields": "id,username"})

    def list_subscriptions(self) -> dict:
        return self._call("GET", f"{self.ig_user_id}/subscribed_apps")

    # --- writes --------------------------------------------------------------
    def reply_to_mention(self, *, media_id: str, comment_id: str, message: str) -> str:
        """Public reply to a comment that @mentioned us, on anybody's media."""
        if self.dry_run:
            log.info("DRY_RUN reply_to_mention media=%s comment=%s: %s", media_id, comment_id, message)
            return f"dryrun-{comment_id}"
        body = self._call(
            "POST", f"{self.ig_user_id}/mentions",
            data={"media_id": media_id, "comment_id": comment_id, "message": message},
        )
        return body.get("id", "")

    def reply_to_comment(self, *, comment_id: str, message: str) -> str:
        """Reply to a comment on our own media."""
        if self.dry_run:
            log.info("DRY_RUN reply_to_comment comment=%s: %s", comment_id, message)
            return f"dryrun-{comment_id}"
        body = self._call("POST", f"{comment_id}/replies", data={"message": message})
        return body.get("id", "")

    def subscribe(self, fields: str = "comments,mentions") -> dict:
        if self.dry_run:
            log.info("DRY_RUN subscribe fields=%s", fields)
            return {"success": True, "dry_run": True}
        return self._call("POST", f"{self.ig_user_id}/subscribed_apps", data={"subscribed_fields": fields})
