"""Google Places API (New) Text Search over a grid of cells per city."""
import logging
import math
import re
from dataclasses import dataclass

from .net import make_key

log = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
BASE_FIELDS = [
    "places.id", "places.displayName", "places.formattedAddress", "places.addressComponents",
    "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.websiteUri",
    "places.rating", "places.userRatingCount", "places.businessStatus", "places.types",
    "places.primaryType", "places.googleMapsUri", "places.location",
]
DENTAL_TYPES = {"dentist", "dental_clinic", "orthodontist"}
DENTAL_NAME_RE = re.compile(r"dent|ortho|smile|teeth|tooth|oral|endo|perio|DDS|DMD", re.I)


class PlacesError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cell:
    city: str
    low: tuple
    high: tuple
    depth: int = 0

    def rectangle(self):
        return {"rectangle": {
            "low": {"latitude": round(self.low[0], 5), "longitude": round(self.low[1], 5)},
            "high": {"latitude": round(self.high[0], 5), "longitude": round(self.high[1], 5)},
        }}

    def quarters(self):
        mid_lat = (self.low[0] + self.high[0]) / 2
        mid_lng = (self.low[1] + self.high[1]) / 2
        return [
            Cell(self.city, (la0, lo0), (la1, lo1), self.depth + 1)
            for la0, la1 in ((self.low[0], mid_lat), (mid_lat, self.high[0]))
            for lo0, lo1 in ((self.low[1], mid_lng), (mid_lng, self.high[1]))
        ]


def build_cells(city, bbox, cell_km):
    """Split a city's bounding box into roughly cell_km x cell_km cells, centre-out."""
    (lat0, lng0), (lat1, lng1) = bbox["low"], bbox["high"]
    mid_lat = (lat0 + lat1) / 2
    height_km = (lat1 - lat0) * 110.574
    width_km = (lng1 - lng0) * 111.320 * math.cos(math.radians(mid_lat))
    n_lat = max(1, math.ceil(height_km / cell_km))
    n_lng = max(1, math.ceil(width_km / cell_km))
    dlat, dlng = (lat1 - lat0) / n_lat, (lng1 - lng0) / n_lng
    cells = [
        Cell(city, (lat0 + i * dlat, lng0 + j * dlng), (lat0 + (i + 1) * dlat, lng0 + (j + 1) * dlng))
        for i in range(n_lat) for j in range(n_lng)
    ]
    centre = ((lat0 + lat1) / 2, (lng0 + lng1) / 2)
    cells.sort(key=lambda c: ((c.low[0] + c.high[0]) / 2 - centre[0]) ** 2
               + ((c.low[1] + c.high[1]) / 2 - centre[1]) ** 2)
    return cells


def plan_searches(cfg, cities):
    """All (query, cell) pairs to run, ordered so each city's centre is searched first."""
    plan = []
    for city in cities:
        cells = build_cells(city, cfg["cities"][city], cfg["grid_cell_km"])
        for cell in cells:
            for q in cfg["queries"]:
                plan.append((q.format(city=city), cell))
    return plan


class PlacesClient:
    def __init__(self, http, cfg):
        self.http = http
        self.cfg = cfg
        pcfg = cfg["places"]
        fields = list(BASE_FIELDS) + (["places.reviews"] if pcfg.get("include_reviews", True) else [])
        self.field_mask = ",".join(fields + ["nextPageToken"])
        self.page_size = min(int(pcfg.get("page_size", 20)), 20)
        self.max_pages = min(int(pcfg.get("max_pages", 3)), 3)
        self.ttl = cfg["cache"]["places_ttl_days"]

    def _body(self, query, cell):
        return {"textQuery": query, "pageSize": self.page_size, "locationRestriction": cell.rectangle()}

    def cache_key(self, query, cell, page):
        return make_key("places", "POST", SEARCH_URL, self._body(query, cell),
                        extra={"page": page, "fields": self.field_mask})

    def first_page_cached(self, query, cell):
        return self.http.is_cached(self.cache_key(query, cell, 0), self.ttl)

    def search(self, query, cell, max_results=None, _retry=True):
        """Run one query in one cell, following page tokens. Returns (places, fetched_at, saturated).

        max_results stops paging early once that many results are in hand (used by --limit)."""
        places, fetched_at, token = [], None, None
        for page in range(self.max_pages):
            data, ts = self._page(query, cell, page, token)
            if data is None:
                if not _retry:
                    break
                # A cached first page's token can expire; refetch the whole chain once.
                log.debug("page token expired for %r, refetching", query)
                return self._search_fresh(query, cell, max_results)
            batch = (data or {}).get("places", [])
            places.extend(batch)
            fetched_at = max(fetched_at or ts, ts)
            token = (data or {}).get("nextPageToken")
            if not token or len(batch) < self.page_size or (max_results and len(places) >= max_results):
                break
        saturated = len(places) >= self.page_size * self.max_pages
        return places, fetched_at, saturated

    def _search_fresh(self, query, cell, max_results):
        old = self.http.refresh
        self.http.refresh = True
        try:
            return self.search(query, cell, max_results, _retry=False)
        finally:
            self.http.refresh = old

    def _page(self, query, cell, page, token):
        body = self._body(query, cell)
        if token:
            body["pageToken"] = token
        headers = {
            "X-Goog-Api-Key": self.cfg["google_api_key"],
            "X-Goog-FieldMask": self.field_mask,
            "Content-Type": "application/json",
        }
        resp = self.http.request("POST", SEARCH_URL, namespace="places", ttl_days=self.ttl,
                                 json_body=body, headers=headers,
                                 cache_key=self.cache_key(query, cell, page), cache_statuses=(200,))
        if resp.ok:
            return resp.json(), resp.fetched_at
        if page > 0 and resp.status == 400:
            return None, resp.fetched_at
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:300]
        raise PlacesError(f"Places API returned HTTP {resp.status}: {msg}")


def locality(place):
    for comp in place.get("addressComponents", []):
        if "locality" in comp.get("types", []):
            return comp.get("longText") or comp.get("shortText")
    return None


def is_dental(place):
    if DENTAL_TYPES & set(place.get("types", [])):
        return True
    return bool(DENTAL_NAME_RE.search(place.get("displayName", {}).get("text", "")))


def collect(client, plan, target_cities, limit=None, split_saturated=True):
    """Run the plan, dedupe by place id and keep dental places located in target cities.

    Returns {place_id: {"place": raw, "city": str, "observed_at": ts, "queries": [..]}}.
    """
    wanted = {c.lower(): c for c in target_cities}
    found = {}
    queue = list(plan)
    done = 0
    while queue:
        query, cell = queue.pop(0)
        places, fetched_at, saturated = client.search(query, cell, (limit - len(found)) if limit else None)
        done += 1
        new = 0
        for p in places:
            city = wanted.get((locality(p) or "").lower())
            if not city or not is_dental(p):
                continue
            entry = found.get(p["id"])
            if entry is None:
                found[p["id"]] = {"place": p, "city": city, "observed_at": fetched_at, "queries": [query]}
                new += 1
            else:
                entry["queries"].append(query)
                if fetched_at > entry["observed_at"]:
                    entry["place"], entry["observed_at"] = p, fetched_at
        log.info("[%d/%d] %-32s cell %s: %d results, %d new (total %d)",
                 done, done + len(queue), query, _cell_label(cell), len(places), new, len(found))
        if saturated and split_saturated and cell.depth == 0:
            log.info("  cell hit the 60-result cap; splitting it into 4 smaller cells")
            queue[0:0] = [(query, sub) for sub in cell.quarters()]
        if limit and len(found) >= limit:
            log.info("reached --limit %d, stopping search", limit)
            break
    if limit:
        found = dict(list(found.items())[:limit])
    return found


def _cell_label(cell):
    return f"({(cell.low[0] + cell.high[0]) / 2:.3f},{(cell.low[1] + cell.high[1]) / 2:.3f})"
