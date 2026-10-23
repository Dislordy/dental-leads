import datetime as dt

from leadgen import age, scoring
from leadgen.config import load_config
from leadgen.db import DAY, LeadDB
from leadgen.places import Cell, build_cells
from leadgen.pipeline import apply_exclusions, chain_key


def _lead(**kw):
    base = dict(age_years=1.5, age_basis="website says est. 2025", rating=4.9, review_count=60,
                velocity=4.0, velocity_source="tracked", practice_types=["general", "family"],
                dentist_count=1, head_dentist="Dr. Jane Smith", business_status="OPERATIONAL",
                exclude_reason="", website="https://x.com", rating_change=None)
    base.update(kw)
    return base


def test_ideal_lead_scores_100_and_explains_itself():
    cfg = load_config()
    score, credits = scoring.score_lead(_lead(rating=5.0), cfg)
    assert score == 100.0
    why = scoring.why_this_lead(_lead(), credits, cfg)
    assert why.startswith("New practice") and "solo practice (Dr. Jane Smith)" in why and "Watch" not in why


def test_old_big_practice_scores_low_and_excluded_is_zero():
    cfg = load_config()
    old, credits = scoring.score_lead(_lead(age_years=25, review_count=900, velocity=1, dentist_count=6), cfg)
    assert old < 45
    assert "Watch:" in scoring.why_this_lead(_lead(age_years=25, review_count=900), credits, cfg)
    excl, _ = scoring.score_lead(_lead(exclude_reason="corporate chain/DSO (aspen dental)"), cfg)
    assert excl == 0


def test_age_estimate_uses_reviews_as_lower_bound():
    today = dt.date(2026, 10, 1)
    years, opened, basis = age.estimate_age(None, dt.date(2025, 1, 1), dt.date(2019, 6, 1), today)
    assert opened == dt.date(2019, 6, 1) and years > 7 and "reviews go back" in basis
    years, _, basis = age.estimate_age(2024, dt.date(2010, 1, 1), dt.date(2024, 9, 1), today)
    assert 2 < years < 2.5 and basis == "website says est. 2024"
    assert age.estimate_age(None, None, None, today) == (None, None, "unknown")


def test_growth_tracking(tmp_path):
    db = LeadDB(tmp_path / "t.sqlite")
    now = 1_800_000_000.0
    db.record_snapshot(1, "p1", "A", "Plano", now - 60 * DAY, 4.7, 40, "OPERATIONAL")
    db.record_snapshot(1, "p1", "A", "Plano", now - 30 * DAY, 4.8, 46, "OPERATIONAL")
    db.record_snapshot(2, "p1", "A", "Plano", now, 4.9, 52, "OPERATIONAL")
    g = db.growth("p1", now, 52, 4.9)
    assert g["reviews_then"] == 46 and abs(g["velocity"] - 6.09) < 0.1 and g["rating_change"] == 0.1
    # Same observation re-recorded (cached data) is ignored; too-recent history gives no velocity.
    db.record_snapshot(3, "p1", "A", "Plano", now, 4.9, 52, "OPERATIONAL")
    assert db.growth("p2", now, 10, 5.0) is None


def test_grid_cells_cover_box_centre_first():
    cells = build_cells("Plano", {"low": [33.0, -96.8], "high": [33.1, -96.6]}, 4)
    assert len(cells) > 4
    c = cells[0]
    assert c.low[0] <= 33.05 <= c.high[0] + 0.03
    assert len(Cell("x", (0, 0), (1, 1)).quarters()) == 4


def test_chain_exclusions():
    cfg = load_config()
    cities = list(cfg["cities"])
    mk = lambda name, domain="", status="OPERATIONAL": {"name": name, "domain": domain, "business_status": status}
    leads = [mk("Aspen Dental - Plano", "aspendental.com"), mk("Smile Hub - Frisco", "smilehub.com"),
             mk("Smile Hub McKinney", "smilehub.com"), mk("Smile Hub (Allen)", "smilehub.com"),
             mk("Bright Creek Dental", "brightcreekdental.com"), mk("Gone Dental", "", "CLOSED_PERMANENTLY")]
    apply_exclusions(leads, cfg, cities)
    reasons = [l["exclude_reason"] for l in leads]
    assert reasons[0].startswith("corporate chain")
    assert all(r.startswith("multi-location") for r in reasons[1:4])
    assert reasons[4] == "" and reasons[5] == "permanently closed"
    assert chain_key("Smile Hub - Frisco", cities) == chain_key("Smile Hub McKinney", cities) == "smile hub"
