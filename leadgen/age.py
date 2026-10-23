"""Practice-age signals: RDAP domain registration date, website 'established' year, review dates."""
import datetime as dt
import logging

log = logging.getLogger(__name__)

RDAP_URL = "https://rdap.org/domain/{domain}"


def parse_date(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def domain_created(http, domain, ttl_days):
    """Registration date of a domain from RDAP, or None."""
    if not domain:
        return None
    resp = http.request("GET", RDAP_URL.format(domain=domain), namespace="rdap", ttl_days=ttl_days,
                        headers={"Accept": "application/rdap+json"})
    if not resp.ok:
        log.debug("RDAP %s for %s", resp.status, domain)
        return None
    try:
        events = resp.json().get("events", [])
    except ValueError:
        return None
    for ev in events:
        if ev.get("eventAction") == "registration":
            return parse_date(ev.get("eventDate"))
    return None


def review_dates(place):
    dates = [parse_date(r.get("publishTime")) for r in place.get("reviews", [])]
    return sorted(d for d in dates if d)


def estimate_age(established_year, domain_date, oldest_review, today=None):
    """Combine the signals into (age_years, opened_estimate, basis).

    Priority: the website's own "established" year, then the domain registration date.
    The oldest Google review is a hard lower bound: the practice existed by then.
    """
    today = today or dt.date.today()
    opened, basis = None, []
    if established_year:
        opened = dt.date(established_year, 7, 1) if established_year < today.year else dt.date(today.year, 1, 1)
        basis.append(f"website says est. {established_year}")
    elif domain_date:
        opened = domain_date
        basis.append(f"domain registered {domain_date:%Y-%m}")
    if oldest_review and (opened is None or oldest_review < opened):
        if opened is None:
            basis.append(f"oldest review {oldest_review:%Y-%m} (at least this old)")
        else:
            basis.append(f"but reviews go back to {oldest_review:%Y-%m}")
        opened = oldest_review
    if opened is None:
        return None, None, "unknown"
    age = max(0.0, (today - opened).days / 365.25)
    return round(age, 1), opened, "; ".join(basis)
