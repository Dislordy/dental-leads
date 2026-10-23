"""Optional Hunter.io lookups (only when HUNTER_API_KEY is set)."""
import logging
from urllib.parse import urlencode

log = logging.getLogger(__name__)

API = "https://api.hunter.io/v2"


class Hunter:
    def __init__(self, http, api_key, ttl_days, use_email_finder=True):
        self.http = http
        self.key = api_key
        self.ttl = ttl_days
        self.use_finder = use_email_finder
        self.disabled_reason = ""

    def _get(self, endpoint, params):
        if self.disabled_reason:
            return None
        url = f"{API}/{endpoint}?{urlencode(params)}"
        resp = self.http.request("GET", url, namespace="hunter", ttl_days=self.ttl,
                                 headers={"X-API-KEY": self.key}, cache_statuses=(200,))
        if resp.status in (401, 403):
            self.disabled_reason = f"Hunter rejected the API key (HTTP {resp.status})"
            log.warning(self.disabled_reason + "; skipping Hunter for the rest of the run")
            return None
        if resp.status == 429:
            log.warning("Hunter rate limit / credits exhausted; skipping Hunter for the rest of the run")
            self.disabled_reason = "Hunter credits or rate limit exhausted"
            return None
        if not resp.ok:
            log.debug("Hunter %s returned %s", endpoint, resp.status)
            return None
        return resp.json().get("data") or {}

    def find(self, domain, first=None, last=None):
        """Return (email, source, status) or None.

        source is "Hunter" for addresses Hunter saw published somewhere, or "guessed" when the
        address is only built from the company's email pattern.
        """
        data = self._get("domain-search", {"domain": domain, "limit": 10})
        if data is None:
            return None
        emails = data.get("emails") or []
        if last:
            for e in emails:
                if (e.get("last_name") or "").lower() == last.lower():
                    return self._label(e.get("value"), e.get("confidence"), e.get("sources"))
        if first and last and self.use_finder:
            found = self._get("email-finder", {"domain": domain, "first_name": first, "last_name": last})
            if found and found.get("email"):
                return self._label(found["email"], found.get("score"), found.get("sources"))
        if first and last and data.get("pattern"):
            guess = apply_pattern(data["pattern"], first, last, domain)
            if guess:
                return guess, "guessed", f"pattern guess, unverified (Hunter pattern {data['pattern']})"
        published = [e for e in emails if e.get("sources")]
        if published:
            best = max(published, key=lambda e: (e.get("type") == "generic", e.get("confidence") or 0))
            return self._label(best.get("value"), best.get("confidence"), best.get("sources"))
        return None

    @staticmethod
    def _label(email, confidence, sources):
        if not email:
            return None
        if sources:
            return email, "Hunter", f"found by Hunter on {len(sources)} web page(s), {confidence or '?'}% confidence"
        return email, "guessed", f"pattern guess, unverified (Hunter {confidence or '?'}% confidence)"


def apply_pattern(pattern, first, last, domain):
    first, last = first.lower().strip("."), last.lower().strip(".")
    if not first or not last:
        return None
    local = (pattern.replace("{first}", first).replace("{last}", last)
             .replace("{f}", first[0]).replace("{l}", last[0]))
    return f"{local}@{domain}" if "{" not in local else None
