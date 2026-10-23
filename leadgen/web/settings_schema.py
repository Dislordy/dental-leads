"""Which config.yaml values the Settings page edits, and how to parse them from the form."""

PRACTICE_TYPES = ["any", "general", "family", "cosmetic", "pediatric", "orthodontics",
                  "periodontics", "endodontics", "oral_surgery", "implants"]

# (section title, [(config path, label, kind, help)])
# kinds: int, float, bool, text, lines (one item per line), types (practice type checkboxes)
SECTIONS = [
    ("Ideal customer profile", [
        (("icp", "practice_types"), "Practice types", "types", "Which kinds of practice fit. Choose \"any\" to accept all."),
        (("icp", "target_max_age_years"), "Full points if practice is this new (years)", "float", ""),
        (("icp", "newness_zero_at_years"), "No newness points at this age (years)", "float", ""),
        (("icp", "min_rating"), "Minimum Google rating", "float", ""),
        (("icp", "review_count_min"), "Review count: minimum", "int", ""),
        (("icp", "review_count_max"), "Review count: maximum", "int", ""),
        (("icp", "target_review_velocity"), "New reviews per month for full points", "float", ""),
        (("icp", "max_dentists"), "Maximum dentists (practice size)", "int", ""),
        (("icp", "selling"), "What you're selling", "text", "Used in the \"why this lead\" text."),
    ]),
    ("Scoring weights", [
        (("weights", "newness"), "Newness", "float", "Weights are relative; they don't need to add up to 100."),
        (("weights", "rating"), "Rating", "float", ""),
        (("weights", "review_count"), "Review count", "float", ""),
        (("weights", "review_velocity"), "Review velocity", "float", ""),
        (("weights", "practice_type"), "Practice type", "float", ""),
        (("weights", "size"), "Size", "float", ""),
    ]),
    ("Exclusions", [
        (("exclude_chains",), "Chains and DSOs to exclude", "lines",
         "One per line. Matched against practice names and website domains."),
        (("chain_location_threshold",), "Exclude names seen at this many locations", "int", "0 turns this off."),
    ]),
    ("Search", [
        (("queries",), "Search queries", "lines", "One per line. {city} is replaced with each city name."),
        (("grid_cell_km",), "Grid cell size (km)", "float",
         "Smaller cells find more practices but cost more API calls."),
        (("split_saturated_cells",), "Split cells that hit Google's 60-result cap", "bool", ""),
        (("places", "include_reviews"), "Fetch reviews (needed for review dates; pricier SKU)", "bool", ""),
        (("places", "price_per_1000"), "Price per 1,000 Google requests (USD, for estimates)", "float", ""),
        (("cache", "places_ttl_days"), "Reuse Google results for this many days", "float",
         "Reruns inside this window cost nothing."),
    ]),
    ("Emails and websites", [
        (("email", "guess_generic_email"), "Fill in info@domain as a labelled guess when no email is found", "bool", ""),
        (("hunter", "enabled"), "Use Hunter.io when a key is set", "bool", ""),
        (("hunter", "use_email_finder"), "Also use Hunter Email Finder (1 credit per hit)", "bool", ""),
        (("crawl", "user_agent"), "Crawler user agent", "text", "Include your own contact email."),
        (("crawl", "delay_seconds"), "Delay between requests to the same site (seconds)", "float", ""),
        (("crawl", "max_pages_per_site"), "Max pages per website", "int", ""),
    ]),
]


def get_path(cfg, path):
    for k in path:
        cfg = (cfg or {}).get(k)
    return cfg


def set_path(d, path, value):
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def field_name(path):
    return ".".join(path)


def parse_form(form):
    """Return (overrides dict, errors list)."""
    overrides, errors = {}, []
    for _, fields in SECTIONS:
        for path, label, kind, _ in fields:
            name = field_name(path)
            raw = form.get(name, "")
            try:
                if kind == "bool":
                    value = form.get(name) == "on"
                elif kind == "int":
                    value = int(raw)
                    if value < 0:
                        raise ValueError
                elif kind == "float":
                    value = float(raw)
                    if value < 0:
                        raise ValueError
                elif kind == "lines":
                    value = [line.strip() for line in raw.splitlines() if line.strip()]
                elif kind == "types":
                    value = [t for t in form.getlist(name) if t in PRACTICE_TYPES]
                    if not value:
                        errors.append("Choose at least one practice type.")
                else:
                    value = raw.strip()
            except ValueError:
                errors.append(f"{label}: enter a number of 0 or more.")
                continue
            set_path(overrides, path, value)

    icp, weights = overrides.get("icp", {}), overrides.get("weights", {})
    if icp.get("review_count_min", 0) > icp.get("review_count_max", 0):
        errors.append("Review count minimum can't be larger than the maximum.")
    if icp.get("min_rating", 0) > 5:
        errors.append("Minimum rating must be 5 or less.")
    if icp.get("target_max_age_years", 0) >= icp.get("newness_zero_at_years", 1):
        errors.append("\"No newness points\" age must be larger than the \"full points\" age.")
    if not sum(weights.values()):
        errors.append("At least one scoring weight must be above 0.")
    if not overrides.get("queries"):
        errors.append("Add at least one search query.")
    if overrides.get("grid_cell_km", 1) < 0.5:
        errors.append("Grid cell size must be at least 0.5 km.")
    return overrides, errors
