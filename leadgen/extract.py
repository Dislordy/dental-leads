"""Pure functions that pull dentist names, emails, founding years and specialties out of page HTML."""
import datetime as dt
import re
from collections import Counter, defaultdict
from urllib.parse import unquote

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def soup_of(html):
    return BeautifulSoup(html or "", "lxml")


def visible_text(soup):
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def heading_text(soup):
    parts = [t.get_text(" ") for t in soup.find_all(["title", "h1", "h2", "h3"])]
    return re.sub(r"\s+", " ", " ".join(parts))


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
JUNK_EMAIL_DOMAINS = {
    "example.com", "domain.com", "email.com", "yourdomain.com", "yoursite.com", "sentry.io",
    "wixpress.com", "sentry.wixpress.com", "sentry-next.wixpress.com", "godaddy.com",
    "squarespace.com", "schema.org", "w3.org", "mysite.com", "website.com", "company.com",
}
JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico")
JUNK_LOCAL_RE = re.compile(r"^[0-9a-f]{20,}$|^(no-?reply|donotreply|privacy|webmaster|abuse)$")


def decode_cfemail(hexstr):
    """Decode a Cloudflare-obfuscated email (data-cfemail / /cdn-cgi/l/email-protection#...)."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def clean_email(e):
    e = unquote(e).strip().strip(".,;:()<>[]\"'").lower()
    if e.startswith("mailto:"):
        e = e[7:]
    e = e.split("?")[0]
    if not EMAIL_RE.fullmatch(e):
        return None
    local, domain = e.rsplit("@", 1)
    if domain in JUNK_EMAIL_DOMAINS or domain.endswith(JUNK_SUFFIXES) or JUNK_LOCAL_RE.match(local):
        return None
    return e


def extract_emails(html, soup=None):
    """All plausible emails on a page: mailto links, Cloudflare-protected, and plain text."""
    soup = soup or soup_of(html)
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            found.extend(href[7:].split(","))
        elif "/cdn-cgi/l/email-protection#" in href:
            found.append(decode_cfemail(href.split("#", 1)[1]))
    for tag in soup.find_all(attrs={"data-cfemail": True}):
        found.append(decode_cfemail(tag["data-cfemail"]))
    text = html or ""
    text = re.sub(r"\s*[\[(]\s*at\s*[\])]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[(]\s*dot\s*[\])]\s*", ".", text, flags=re.I)
    found.extend(EMAIL_RE.findall(text))
    out = []
    for e in found:
        e = clean_email(e or "")
        if e and e not in out:
            out.append(e)
    return out


GENERIC_LOCALS = ("info", "office", "contact", "frontdesk", "front.desk", "appointments", "appt",
                  "hello", "reception", "admin", "smile", "smiles", "team", "schedule")
LOW_VALUE_LOCALS = ("hr", "careers", "jobs", "billing", "marketing", "accounts", "insurance")


def rank_email(email, site_domain=None, dentist=None):
    local, domain = email.split("@")
    score = 0
    if site_domain and (domain == site_domain or domain.endswith("." + site_domain)):
        score += 3
    if dentist:
        first, last = dentist_first_last(dentist)
        if last and last.lower() in local:
            score += 4
        elif first and len(first) > 2 and first.lower() in local:
            score += 2
    if local.startswith(GENERIC_LOCALS):
        score += 1
    if local.startswith(LOW_VALUE_LOCALS):
        score -= 2
    return score


# ---------------------------------------------------------------------------
# Dentist names
# ---------------------------------------------------------------------------

TOKEN = r"(?:[A-Z][a-zA-Z'’\-]+|[A-Z]\.)"
DR_RE = re.compile(rf"\bDr\.?\s+({TOKEN}(?:\s+{TOKEN}){{0,3}})")
CRED_RE = re.compile(rf"\b({TOKEN}(?:\s+{TOKEN}){{1,4}}),?\s+(?:D\.?D\.?S|D\.?M\.?D)\b\.?")
SENTENCE_END_RE = re.compile(r"(?<!\bDr)(?<!\bMr)(?<!\bMs)(?<!\bMrs)(?<!\b[A-Z])[.!?](?=\s|$)")
ASSOCIATE_RE = re.compile(r"associate|hygienist|assistant|joined", re.I)
OWNER_RE = re.compile(r"founder|founded|owner|owns|lead dentist|head dentist|principal dentist|"
                      r"opened (?:her|his|their|the|our) (?:own )?practice|established", re.I)
STOPWORDS = {
    "the", "our", "and", "is", "was", "has", "have", "will", "can", "we", "you", "your", "meet", "about",
    "office", "team", "dental", "dentistry", "dentist", "dentists", "family", "smile", "smiles", "care",
    "center", "centre", "clinic", "orthodontics", "orthodontist", "pediatric", "cosmetic", "implant",
    "implants", "services", "service", "contact", "read", "more", "learn", "book", "call", "schedule",
    "appointment", "today", "welcome", "home", "patients", "patient", "new", "plano", "frisco",
    "mckinney", "allen", "wylie", "texas", "tx", "dallas", "graduated", "received", "earned", "attended",
    "loves", "enjoys", "joined", "practice", "doctor", "doctors", "dr", "dr.", "dds", "dmd", "ms", "pa",
    "pllc", "llc", "in", "at", "of", "for", "with", "on", "to", "from", "by", "who", "what", "why",
    "how", "when", "where", "she", "he", "they", "her", "his", "their", "general", "group", "associates",
    "a", "an", "as", "also", "after", "before", "since", "if", "this", "that", "here", "view", "bio",
    "profile", "dentures", "braces", "invisalign", "facebook", "instagram", "reviews", "review",
    "university", "college", "school", "board", "certified", "american", "association", "academy",
    "member", "fellow", "faq", "blog", "privacy", "policy", "hours", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "jr", "sr", "ii", "iii", "iv", "md", "rdh", "phd",
}
DOCTOR_PAGE_RE = re.compile(r"doctor|dr-|dr_|/dr\b|meet|team|about|staff|dentist", re.I)


def _tokens(name):
    return [t for t in re.split(r"\s+", name.strip()) if t]


def _is_stop(tok):
    if re.fullmatch(r"[A-Z]\.", tok):   # a middle initial, not the word "a"
        return False
    return tok.lower().rstrip(".,") in STOPWORDS


def _clean_after_dr(raw):
    toks = []
    for t in _tokens(raw):
        if _is_stop(t):
            break
        toks.append(t)
    return _finish(toks)


def _clean_before_cred(raw):
    toks = []
    for t in reversed(_tokens(raw)):
        if _is_stop(t):
            break
        toks.insert(0, t)
    return _finish(toks)


def _finish(toks):
    while toks and re.fullmatch(r"[A-Z]\.", toks[-1]):
        toks.pop()
    while toks and re.fullmatch(r"[A-Z]\.", toks[0]) and len(toks) > 2:
        toks.pop(0)
    if not toks or len(toks) > 3:
        return None
    return " ".join(toks)


def dentist_first_last(name):
    toks = [t for t in _tokens(name) if not re.fullmatch(r"[A-Z]\.", t)]
    if not toks:
        return None, None
    if len(toks) == 1:
        return None, toks[0]
    return toks[0], toks[-1]


def find_dentist_mentions(text, headings="", page_url="", is_google_name=False):
    """Yield (name, points, has_credential, on_doctor_page) for every Dr./DDS/DMD mention in text."""
    doctor_page = bool(DOCTOR_PAGE_RE.search(page_url or ""))
    for regex, cleaner, cred in ((DR_RE, _clean_after_dr, False), (CRED_RE, _clean_before_cred, True)):
        for m in regex.finditer(text):
            name = cleaner(m.group(1))
            if not name:
                continue
            pts = 1.0 + (1.0 if doctor_page else 0) + (2.0 if cred else 0) + (3.0 if is_google_name else 0)
            window = _sentence(text, m.start(), m.end())
            if OWNER_RE.search(window):
                pts += 3
            if ASSOCIATE_RE.search(window):
                pts -= 1
            if name in headings:
                pts += 2
            yield name, pts, cred, doctor_page


def _sentence(text, start, end, reach=160):
    """The sentence around text[start:end] (not split on "Dr." or initials)."""
    before = text[max(0, start - reach):start]
    ends = list(SENTENCE_END_RE.finditer(before))
    if ends:
        before = before[ends[-1].end():]
    after = text[end:end + reach]
    m = SENTENCE_END_RE.search(after)
    if m:
        after = after[:m.start()]
    return before + text[start:end] + after


def pick_head_dentist(mentions):
    """Merge mentions ("Dr. Smith" + "Jane Smith, DDS") and return (head_name, dentist_count, ranked)."""
    full = defaultdict(float)
    confirmed = set()       # names that look like real dentists at this practice
    seen = Counter()
    last_only = defaultdict(float)
    for name, pts, cred, doctor_page in mentions:
        first, last = dentist_first_last(name)
        if first:
            full[(first, last)] += pts
            seen[(first, last)] += 1
            if cred or doctor_page:
                confirmed.add((first, last))
        elif last:
            last_only[last] += pts
    # Fold "Dr. Smith" mentions into the full name with that surname.
    for last, pts in list(last_only.items()):
        matches = [k for k in full if k[1] == last]
        if matches:
            best = max(matches, key=lambda k: full[k])
            full[best] += pts
            del last_only[last]
    # Collapse "Jane Smith" vs "Jane A. Smith" variants — they share first+last already.
    ranked = sorted(full.items(), key=lambda kv: -kv[1])
    names = [(f"{f} {l}", s) for (f, l), s in ranked]
    names += sorted(((f"Dr. {l}", s) for l, s in last_only.items()), key=lambda kv: -kv[1])
    if not names:
        return None, None, []
    head = names[0][0]
    head = head if head.startswith("Dr. ") else f"Dr. {head}"
    # Count dentists: names with a DDS/DMD or on a doctor/team page, or mentioned repeatedly.
    count = len({k for k in full if k in confirmed or seen[k] >= 2}) or 1
    return head, count, names


# ---------------------------------------------------------------------------
# Founding year
# ---------------------------------------------------------------------------

YEAR = r"((?:19|20)\d{2})"
EST_PATTERNS = [
    (3, re.compile(rf"\b(?:established|founded|opened(?:\s+(?:our|its|the|her|his)\s+doors)?)\b[^.!?\n]{{0,45}}?\b{YEAR}\b", re.I)),
    (2, re.compile(rf"\best\.?\s*(?:in\s+)?{YEAR}\b", re.I)),
    (2, re.compile(rf"\b(?:serving|caring for|proudly serving|in business)\b[^.!?\n]{{0,60}}?\bsince\s+{YEAR}\b", re.I)),
]


def extract_established_year(text, today=None):
    this_year = (today or dt.date.today()).year
    hits = []
    for weight, regex in EST_PATTERNS:
        for m in regex.finditer(text):
            year = int(m.group(1))
            if 1950 <= year <= this_year:
                hits.append((weight, year))
    if not hits:
        return None
    best_weight = max(w for w, _ in hits)
    counts = Counter(y for w, y in hits if w == best_weight)
    return max(counts, key=lambda y: (counts[y], y))


# ---------------------------------------------------------------------------
# Practice type
# ---------------------------------------------------------------------------

SPECIALTY_NAME = {
    "pediatric": re.compile(r"pediatric|paediatric|kids|children|tots|\bpedo", re.I),
    "orthodontics": re.compile(r"orthodont|braces", re.I),
    "periodontics": re.compile(r"periodont|\bperio\b", re.I),
    "endodontics": re.compile(r"endodont|\bendo\b|root canal", re.I),
    "oral_surgery": re.compile(r"oral (?:&|and )?(?:maxillofacial )?surg|oral surgeon", re.I),
}
TEXT_TYPES = {
    "family": re.compile(r"family dent", re.I),
    "general": re.compile(r"general dent", re.I),
    "cosmetic": re.compile(r"cosmetic|veneers|smile makeover", re.I),
    "implants": re.compile(r"dental implant", re.I),
}


def classify_practice(name, text=""):
    """Return the set of practice types this looks like."""
    specialties = {t for t, rx in SPECIALTY_NAME.items() if rx.search(name or "")}
    if specialties:
        return specialties
    types = {"general"}
    for t, rx in TEXT_TYPES.items():
        if rx.search(name or "") or len(rx.findall(text or "")) >= 2:
            types.add(t)
    if "family" in (name or "").lower():
        types.add("family")
    return types
