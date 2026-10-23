"""Optional Texas State Board of Dental Examiners license matching from a local CSV.

The TSBDE online license lookup is an interactive search form, so this tool does not query it
automatically. Instead, point config.yaml -> tsbde.license_csv at a licensee file you've
downloaded or requested from TSBDE (see README) and leads are matched against it offline.
"""
import csv
import logging
from collections import defaultdict
from pathlib import Path

from .extract import dentist_first_last

log = logging.getLogger(__name__)


class LicenseIndex:
    def __init__(self, cfg, project_dir):
        t = cfg.get("tsbde") or {}
        self.enabled = False
        self.by_name = defaultdict(list)
        path = t.get("license_csv")
        if not path:
            return
        path = Path(path)
        path = path if path.is_absolute() else project_dir / path
        if not path.exists():
            log.warning("TSBDE license CSV %s not found; skipping license checks", path)
            return
        first_col, last_col = t["name_columns"]["first"], t["name_columns"]["last"]
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            for row in csv.DictReader(f):
                key = ((row.get(first_col) or "").strip().lower(), (row.get(last_col) or "").strip().lower())
                if key[1]:
                    self.by_name[key].append(row)
        self.date_col = t.get("license_date_column")
        self.number_col = t.get("license_number_column")
        self.city_col = t.get("city_column")
        self.enabled = True
        log.info("loaded %d TSBDE license records", sum(len(v) for v in self.by_name.values()))

    def lookup(self, dentist, city=None):
        """Return 'License #123 (2021-06-01)' or '' when there's no single confident match."""
        if not self.enabled or not dentist:
            return ""
        first, last = dentist_first_last(dentist.removeprefix("Dr. "))
        if not first:
            return ""
        rows = self.by_name.get((first.lower(), last.lower()), [])
        if len(rows) > 1 and city and self.city_col:
            local = [r for r in rows if (r.get(self.city_col) or "").strip().lower() == city.lower()]
            rows = local or rows
        if len(rows) != 1:
            return "multiple matches" if rows else "no match"
        r = rows[0]
        return f"License {r.get(self.number_col, '?')} (licensed {r.get(self.date_col, '?')})"
