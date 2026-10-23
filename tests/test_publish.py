"""Encrypted GitHub Pages site and history, built from a real (fake-server) run."""
import io
import json

import openpyxl
import pytest
from cryptography.exceptions import InvalidTag

from leadgen import age, pipeline, places, publish
from leadgen.config import load_config
from test_end_to_end import servers  # noqa: F401  (fixture)


def test_encrypt_round_trip_and_wrong_password():
    bundle = publish.encrypt(b"secret leads", "correct horse", iterations=1000)
    assert b"secret" not in json.dumps(bundle).encode()
    assert publish.decrypt(bundle, "correct horse") == b"secret leads"
    with pytest.raises(InvalidTag):
        publish.decrypt(bundle, "wrong password")


def test_build_site_and_state(servers, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(places, "SEARCH_URL", servers.api + "/v1/places:searchText")
    monkeypatch.setattr(age, "RDAP_URL", servers.api + "/rdap/{domain}")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    cfg = load_config(env_path=tmp_path / "none.env")
    cfg.update(grid_cell_km=100, queries=["dentist in {city}, TX"], database=str(tmp_path / "leads.sqlite"),
               output_dir=str(tmp_path / "out"))
    cfg["cache"]["path"] = str(tmp_path / "cache.sqlite")
    cfg["places"]["page_size"] = 2
    cfg["crawl"]["delay_seconds"] = 0
    site, state = tmp_path / "docs", tmp_path / "state" / "leads.sqlite.enc"

    # Before any run: the page exists, but there's no data yet.
    assert publish.build_site(cfg, "pw-12345678", site) is None
    assert (site / "index.html").exists() and not (site / "data.enc.json").exists()

    pipeline.run(cfg, ["Plano"], yes=True)
    run_id = publish.build_site(cfg, "pw-12345678", site)
    assert run_id
    html = (site / "index.html").read_text()
    assert 'data-page="published"' in html and 'id="leads-table"' in html and "noindex" in html
    assert (site / "app.js").exists() and (site / "app.css").exists() and (site / ".nojekyll").exists()

    raw = (site / "data.enc.json").read_text()
    assert "Bright Creek" not in raw and "brightcreek" not in raw          # nothing readable without the password
    payload = json.loads(publish.decrypt(json.loads(raw), "pw-12345678"))
    assert payload["run"]["id"] == run_id and payload["run"]["cities"] == "Plano"
    assert {l["name"] for l in payload["leads"]} >= {"Bright Creek Dental", "Aspen Dental - Plano"}
    wb = openpyxl.load_workbook(io.BytesIO(__import__("base64").b64decode(payload["xlsx_b64"])))
    assert wb.sheetnames == ["Leads", "Excluded"]

    # History round trip: save, wipe, restore.
    assert publish.save_state(cfg, "pw-12345678", state)
    original = cfg.path("database").read_bytes()
    cfg.path("database").unlink()
    assert publish.restore_state(cfg, "pw-12345678", state)
    assert cfg.path("database").read_bytes() == original
    with pytest.raises(InvalidTag):
        publish.restore_state(cfg, "not-the-password", state)
