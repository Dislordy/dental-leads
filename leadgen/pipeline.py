"""Runs the whole thing: search -> crawl/enrich -> track growth -> score -> export."""
import datetime as dt
import logging
import math
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import age as agemod
from . import extract, scoring
from .config import PROJECT_DIR
from .db import LeadDB
from .export import write_outputs
from .hunter import Hunter
from .net import HttpClient, ResponseCache
from .places import PlacesClient, PlacesError, collect, plan_searches
from .tsbde import LicenseIndex
from .website import SiteCrawler, SiteInfo, registrable_domain

log = logging.getLogger("leadgen")

# Website hosts that aren't the practice's own domain: don't RDAP them or treat them as chains.
SHARED_HOSTS = {
    "facebook.com", "instagram.com", "business.site", "wixsite.com", "square.site", "godaddysites.com",
    "google.com", "weebly.com", "wordpress.com", "yelp.com", "linktr.ee", "zocdoc.com",
    "healthgrades.com", "squarespace.com", "carrd.co", "webflow.io", "site123.me", "page.link",
}
SOCIAL_HOSTS = {"facebook.com", "instagram.com", "yelp.com", "linktr.ee", "zocdoc.com", "healthgrades.com"}


# ---------------------------------------------------------------------------
# Estimate + confirmation
# ---------------------------------------------------------------------------

def estimate(cfg, plan, places_client, limit):
    max_pages = places_client.max_pages
    cached = sum(1 for q, cell in plan if places_client.first_page_cached(q, cell))
    uncached = len(plan) - cached
    by_city = Counter(cell.city for _, cell in plan)
    price = float(cfg["places"].get("price_per_1000", 0)) / 1000
    lines = ["", "Planned Google Places Text Search requests"]
    for city, n in by_city.items():
        lines.append(f"  {city:<10} {n // len(cfg['queries']):>3} grid cells x {len(cfg['queries'])} queries"
                     f" = {n} searches")
    lines.append(f"  First pages already cached (free): {cached} of {len(plan)}")
    lo, hi = uncached, uncached * max_pages
    lines.append(f"  Billable requests: {lo} to {hi} (each search can need up to {max_pages} pages;"
                 " crowded cells that hit the 60-result cap are split, adding a few more)")
    if limit:
        likely = min(hi, max(1, math.ceil(limit / 5)))
        lines.append(f"  --limit {limit}: the search stops as soon as {limit} practices are found,"
                     f" so expect {'about 1' if likely <= 1 else f'roughly 1 to {likely}'} request(s)")
        lo, hi = min(1, lo), likely
    lines.append(f"  Estimated cost: ${lo * price:.2f} to ${hi * price:.2f}"
                 f" at ${price * 1000:.0f} per 1,000 requests, before Google's monthly free usage")
    pages = cfg["crawl"]["max_pages_per_site"]
    n_places = f"~{limit} sites" if limit else "each site"
    lines.append("Other requests (no Google cost)")
    lines.append(f"  Practice websites: {n_places} x up to {pages} pages + robots.txt,"
                 f" {cfg['crawl']['delay_seconds']}s apart per site")
    lines.append("  RDAP domain lookups: 1 per practice domain (cached for"
                 f" {cfg['cache']['rdap_ttl_days']} days)")
    if cfg["hunter_api_key"] and cfg["hunter"].get("enabled", True):
        per = 2 if cfg["hunter"].get("use_email_finder", True) else 1
        lines.append(f"  Hunter.io: up to {per} credit(s) per practice whose website lists no email")
    else:
        lines.append("  Hunter.io: skipped (no HUNTER_API_KEY)")
    return "\n".join(lines), hi


def estimate_for(cfg, cities, limit=None):
    """Estimate text and the worst-case billable request count, without making requests."""
    http = HttpClient(ResponseCache(cfg.cache_path), cfg["crawl"]["user_agent"])
    return estimate(cfg, plan_searches(cfg, cities), PlacesClient(http, cfg), limit)


def confirm(prompt="Proceed? [y/N] "):
    if not sys.stdin.isatty():
        print("No terminal to confirm on; re-run with --yes to proceed.")
        return False
    return input(prompt).strip().lower() in ("y", "yes")


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def chain_key(name, cities):
    n = re.split(r"\s[-|–:]\s|\(", name.lower())[0]
    for c in cities:
        n = n.replace(c.lower(), " ")
    n = re.sub(r"\b(of|in|at|tx|texas)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


class Enricher:
    def __init__(self, cfg, http, all_cities):
        self.cfg = cfg
        self.http = http
        self.crawler = SiteCrawler(http, cfg)
        self.hunter = None
        if cfg["hunter_api_key"] and cfg["hunter"].get("enabled", True):
            self.hunter = Hunter(http, cfg["hunter_api_key"], cfg["cache"]["hunter_ttl_days"],
                                 cfg["hunter"].get("use_email_finder", True))
        self.licenses = LicenseIndex(cfg, PROJECT_DIR)
        self.all_cities = all_cities

    def enrich(self, entry):
        p = entry["place"]
        name = p.get("displayName", {}).get("text", "")
        website = p.get("websiteUri") or ""
        domain = registrable_domain(website) if website else ""
        own_domain = domain if domain and domain not in SHARED_HOSTS else ""

        if website and domain not in SOCIAL_HOSTS:
            site = self.crawler.crawl(website, name)
        else:
            site = SiteInfo(url=website)
            site.mentions = list(extract.find_dentist_mentions(name, name, is_google_name=True))
        head, count, ranked = extract.pick_head_dentist(site.mentions)

        created = agemod.domain_created(self.http, own_domain, self.cfg["cache"]["rdap_ttl_days"])
        reviews = agemod.review_dates(p)
        oldest = reviews[0] if reviews else None
        age_years, _, age_basis = agemod.estimate_age(site.established_year, created, oldest)

        email, email_source, email_status, others = self._email(site, own_domain, head)
        types = extract.classify_practice(name, site.text)

        return {
            "place_id": p["id"],
            "name": name,
            "city": entry["city"],
            "address": p.get("formattedAddress", ""),
            "phone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or "",
            "website": website,
            "domain": domain,
            "rating": p.get("rating"),
            "review_count": p.get("userRatingCount") or 0,
            "business_status": p.get("businessStatus", ""),
            "maps_url": p.get("googleMapsUri", ""),
            "observed_at": entry["observed_at"],
            "head_dentist": head or "",
            "dentist_count": count,
            "other_dentists": ", ".join(n for n, _ in ranked[1:6]),
            "email": email,
            "email_source": email_source,
            "email_status": email_status,
            "other_emails": ", ".join(others),
            "established_year": site.established_year,
            "domain_created": created.isoformat() if created else "",
            "oldest_review": oldest.isoformat() if oldest else "",
            "age_years": age_years,
            "age_basis": age_basis,
            "practice_types": sorted(types),
            "pages_crawled": len(site.pages),
            "robots_blocked": site.robots_blocked,
            "crawl_error": site.error,
            "tx_license": self.licenses.lookup(head, entry["city"]) if self.licenses.enabled else "",
        }

    def _email(self, site, own_domain, head):
        ranked = sorted(site.emails, key=lambda e: -extract.rank_email(e, own_domain, head))
        if ranked:
            best = ranked[0]
            return best, "website", f"verified on site ({site.email_pages[best]})", ranked[1:4]
        if self.hunter and own_domain:
            first, last = extract.dentist_first_last((head or "").removeprefix("Dr. "))
            found = self.hunter.find(own_domain, first, last)
            if found:
                return found[0], found[1], found[2], []
        if own_domain and self.cfg.get("email", {}).get("guess_generic_email", True):
            return (f"info@{own_domain}", "guessed",
                    "pattern guess, unverified (common info@ address, not seen anywhere)", [])
        return "", "", "", []


def apply_exclusions(leads, cfg, cities):
    patterns = [c.lower() for c in cfg.get("exclude_chains", [])]
    threshold = int(cfg.get("chain_location_threshold", 0) or 0)
    name_counts = Counter(chain_key(l["name"], cities) for l in leads)
    domain_counts = Counter(l["domain"] for l in leads if l["domain"] and l["domain"] not in SHARED_HOSTS)
    for l in leads:
        reason = ""
        hay = f"{l['name'].lower()} {l['domain'].replace('-', ' ')} {l['domain'].replace('.', ' ')}"
        hay_compact = l["name"].lower().replace(" ", "") + " " + l["domain"]
        hit = next((p for p in patterns if p in hay or p.replace(" ", "") in hay_compact), None)
        if l["business_status"] == "CLOSED_PERMANENTLY":
            reason = "permanently closed"
        elif hit:
            reason = f"corporate chain/DSO ({hit})"
        elif threshold and name_counts[chain_key(l["name"], cities)] >= threshold:
            reason = f"multi-location group ({name_counts[chain_key(l['name'], cities)]} locations found)"
        elif threshold and l["domain"] in domain_counts and domain_counts[l["domain"]] >= threshold:
            reason = f"multi-location group ({domain_counts[l['domain']]} locations share {l['domain']})"
        l["exclude_reason"] = reason


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run(cfg, cities, limit=None, yes=False, refresh=False, dry_run=False, args=None,
        progress=None, stop=None):
    """Run the whole pipeline. Returns the list of leads, or None if it didn't complete.

    progress(event: dict) is called as work advances (used by the web UI); stop is an optional
    threading.Event that ends the run early, keeping whatever practices were fully processed.
    """
    progress = progress or (lambda event: None)
    out_dir = cfg.path("output_dir")
    cache = ResponseCache(cfg.cache_path)
    http = HttpClient(cache, cfg["crawl"]["user_agent"], timeout=cfg["crawl"]["timeout_seconds"],
                      refresh=refresh)
    places_client = PlacesClient(http, cfg)
    plan = plan_searches(cfg, cities)
    text, _ = estimate(cfg, plan, places_client, limit)
    print(text)
    if dry_run:
        return None
    if not cfg["google_api_key"]:
        log.error("GOOGLE_PLACES_API_KEY is not set. Copy .env.example to .env and add your key (see README).")
        return None
    if not yes and not confirm():
        print("Cancelled.")
        return None

    db = LeadDB(cfg.path("database"))
    run_id = db.start_run(cities, args or {})
    started = time.time()
    progress({"phase": "search", "done": 0, "total": len(plan), "run_id": run_id})

    try:
        found = collect(places_client, plan, cities, limit=limit,
                        split_saturated=cfg.get("split_saturated_cells", True),
                        progress=lambda done, total: progress({"phase": "search", "done": done, "total": total}),
                        stop=stop)
    except PlacesError as e:
        log.error("%s", e)
        return None
    if stop and stop.is_set():
        log.warning("stopped during the search; nothing saved (responses so far are cached, so a rerun is cheap)")
        return None
    log.info("found %d unique dental practices; crawling websites...", len(found))
    progress({"phase": "crawl", "done": 0, "total": len(found)})

    enricher = Enricher(cfg, http, list(cfg["cities"]))
    leads = []
    with ThreadPoolExecutor(max_workers=int(cfg["crawl"].get("workers", 4))) as pool:
        futures = {pool.submit(enricher.enrich, e): e for e in found.values()}
        for i, fut in enumerate(as_completed(futures), 1):
            lead = fut.result()
            leads.append(lead)
            log.info("[%d/%d] %s | %s | %s (%s) | age %s", i, len(futures), lead["name"],
                     lead["head_dentist"] or "dentist ?", lead["email"] or "no email",
                     lead["email_source"] or "-", lead["age_years"] if lead["age_years"] is not None else "?")
            progress({"phase": "crawl", "done": i, "total": len(futures)})
            if stop and stop.is_set():
                log.warning("stopped by user; saving the %d practices finished so far", len(leads))
                pool.shutdown(wait=True, cancel_futures=True)
                break

    apply_exclusions(leads, cfg, list(cfg["cities"]))
    for lead in leads:
        _finish_lead(lead, db, run_id, cfg)
    db.commit()

    csv_path, xlsx_path = write_outputs(leads, out_dir)
    db.finish_run(run_id, len(leads))
    calls = dict(http.network_calls)
    log.info("done in %.0fs. Network requests made this run (cached ones excluded): %s",
             time.time() - started, calls or "none")
    if enricher.hunter and enricher.hunter.disabled_reason:
        log.warning("Hunter: %s", enricher.hunter.disabled_reason)
    print_summary(leads, csv_path, xlsx_path)
    progress({"phase": "done", "done": len(leads), "total": len(leads)})
    return leads


def _finish_lead(lead, db, run_id, cfg):
    db.record_snapshot(run_id, lead["place_id"], lead["name"], lead["city"], lead["observed_at"],
                       lead["rating"], lead["review_count"], lead["business_status"])
    g = db.growth(lead["place_id"], lead["observed_at"], lead["review_count"], lead["rating"])
    lead["rating_change"] = g["rating_change"] if g else None
    if g and g["velocity"] is not None:
        lead["velocity"], lead["velocity_source"] = g["velocity"], "tracked"
        since = dt.date.fromtimestamp(g["since"]).isoformat()
        lead["velocity_display"] = f"{g['velocity']:+.1f}/mo (tracked since {since})"
    elif lead["age_years"] and lead["age_years"] >= 0.25 and lead["review_count"]:
        v = round(lead["review_count"] / (lead["age_years"] * 12), 2)
        lead["velocity"], lead["velocity_source"] = v, "lifetime"
        lead["velocity_display"] = f"~{v:.1f}/mo (lifetime avg; tracked after next run)"
    else:
        lead["velocity"], lead["velocity_source"], lead["velocity_display"] = None, "", ""
    lead["age_display"] = f"~{lead['age_years']:g} yrs" if lead["age_years"] is not None else "unknown"
    lead["practice_types_display"] = "/".join(lead["practice_types"])
    lead["score"], credits = scoring.score_lead(lead, cfg)
    lead["why"] = scoring.why_this_lead(lead, credits, cfg)
    db.save_lead(run_id, lead["place_id"], lead["score"], lead)


def print_summary(leads, csv_path, xlsx_path, top=15):
    keep = sorted((l for l in leads if not l["exclude_reason"]), key=lambda l: -l["score"])
    excluded = len(leads) - len(keep)
    print(f"\n{len(keep)} leads ({excluded} excluded). Top {min(top, len(keep))}:\n")
    print(f"{'Score':>5}  {'Practice':<34} {'City':<9} {'Head dentist':<22} {'Email':<32} {'Source':<8}")
    for l in keep[:top]:
        print(f"{l['score']:>5}  {l['name'][:34]:<34} {l['city'][:9]:<9} {l['head_dentist'][:22]:<22} "
              f"{(l['email'] or '-')[:32]:<32} {l['email_source'] or '-':<8}")
        print(f"       {l['why'][:150]}")
    print(f"\nSaved {csv_path}\n      {xlsx_path}")
