"""Polite crawler for a practice's own website (homepage + about/team/contact pages)."""
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from . import extract

log = logging.getLogger(__name__)

LINK_HINTS = [
    (3, re.compile(r"meet|doctor|dr-|/dr\b|our-dentist|dentists?\b|team|staff|about", re.I)),
    (2, re.compile(r"contact|location|office|our-practice|practice", re.I)),
]
SKIP_LINK_RE = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|mp4)$|blog|/tag/|/category/|wp-login|"
                          r"facebook|instagram|twitter|youtube|google\.com|yelp|tel:|mailto:|#", re.I)


@dataclass
class SiteInfo:
    url: str
    pages: list = field(default_factory=list)
    emails: list = field(default_factory=list)          # in order found
    email_pages: dict = field(default_factory=dict)     # email -> page url
    mentions: list = field(default_factory=list)        # (name, points, credentialed)
    established_year: int = None
    text: str = ""
    robots_blocked: bool = False
    error: str = ""


def registrable_domain(url_or_host):
    host = urlsplit(url_or_host).netloc if "//" in url_or_host else url_or_host
    host = host.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org", "gov", "ac"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class SiteCrawler:
    def __init__(self, http, cfg):
        self.http = http
        c = cfg["crawl"]
        self.user_agent = c["user_agent"]
        self.delay = float(c["delay_seconds"])
        self.max_pages = int(c["max_pages_per_site"])
        self.paths = c["paths"]
        self.ttl = cfg["cache"]["website_ttl_days"]

    def _get(self, url):
        return self.http.request("GET", url, namespace="website", ttl_days=self.ttl,
                                 min_interval=self.delay, html_only=True)

    def _robots(self, origin):
        rp = RobotFileParser()
        resp = self.http.request("GET", origin + "/robots.txt", namespace="website", ttl_days=self.ttl,
                                 min_interval=self.delay, cache_statuses=(200, 401, 403, 404, 410))
        if resp.ok:
            rp.parse(resp.text.splitlines())
        elif 400 <= resp.status < 500:
            rp.allow_all = True        # no robots.txt = no restrictions (RFC 9309)
        else:
            rp.disallow_all = True     # server error or unreachable: be conservative
        return rp

    def crawl(self, url, place_name=""):
        info = SiteInfo(url=url)
        try:
            self._crawl(info, url)
        except Exception as e:  # one broken site shouldn't stop the run
            log.warning("crawl failed for %s: %s", url, e)
            info.error = str(e)
        if place_name:
            info.mentions.extend(extract.find_dentist_mentions(place_name, place_name, is_google_name=True))
        info.established_year = extract.extract_established_year(info.text)
        return info

    def _crawl(self, info, url):
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        robots = self._robots(origin)
        if not robots.can_fetch(self.user_agent, url):
            info.robots_blocked = True
            log.info("robots.txt disallows %s", url)
            return

        home = self._get(url)
        if not home.ok or not home.text:
            info.error = f"homepage returned HTTP {home.status}"
            return
        # Follow redirects (e.g. http -> https, bare -> www) for the rest of the crawl.
        final = urlsplit(home.url)
        origin = urlunsplit((final.scheme, final.netloc, "", "", ""))
        if final.netloc != parts.netloc:
            robots = self._robots(origin)

        queue = self._candidate_links(home.text, home.url, origin)
        self._absorb(info, home.url, home.text)
        seen = {self._norm(home.url)}
        attempts = 0
        for link in queue:
            if len(info.pages) >= self.max_pages or attempts >= self.max_pages * 2:
                break
            if self._norm(link) in seen:
                continue
            seen.add(self._norm(link))
            attempts += 1
            if not robots.can_fetch(self.user_agent, link):
                log.debug("robots.txt disallows %s", link)
                continue
            resp = self._get(link)
            if resp.ok and resp.text:
                if self._norm(resp.url) in seen and resp.url != link:
                    continue   # redirected back to a page we already have
                seen.add(self._norm(resp.url))
                self._absorb(info, resp.url, resp.text)

    @staticmethod
    def _norm(url):
        p = urlsplit(url)
        return (p.netloc.lower().removeprefix("www."), p.path.rstrip("/").lower())

    def _candidate_links(self, html, base_url, origin):
        """Links from the homepage that look like doctor/about/contact pages, then config paths."""
        soup = extract.soup_of(html)
        host = registrable_domain(origin)
        scored = {}
        for a in soup.find_all("a", href=True):
            href = urljoin(base_url, a["href"].strip()).split("#")[0]
            if not href.startswith("http") or registrable_domain(href) != host or SKIP_LINK_RE.search(href):
                continue
            label = f"{urlsplit(href).path} {a.get_text(' ')}"
            for weight, rx in LINK_HINTS:
                if rx.search(label):
                    scored[href] = max(scored.get(href, 0), weight)
                    break
        links = sorted(scored, key=lambda h: (-scored[h], len(h)))
        links += [origin + p for p in self.paths]
        return links

    def _absorb(self, info, url, html):
        soup = extract.soup_of(html)
        emails = extract.extract_emails(html, soup)
        headings = extract.heading_text(soup)
        text = extract.visible_text(soup)
        info.pages.append(url)
        for e in emails:
            if e not in info.email_pages:
                info.emails.append(e)
                info.email_pages[e] = url
        info.mentions.extend(extract.find_dentist_mentions(text, headings, url))
        info.text += " " + text
