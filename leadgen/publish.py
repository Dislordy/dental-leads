"""Password-protected static site for GitHub Pages, plus encrypted run history for GitHub Actions.

    python -m leadgen.publish restore   # state/leads.sqlite.enc -> data/leads.sqlite (before a run)
    python -m leadgen.publish build     # latest run -> docs/ ; data/leads.sqlite -> state/leads.sqlite.enc

Both read the password from LEADGEN_SITE_PASSWORD. Lead data is encrypted (AES-256-GCM with a
PBKDF2-SHA256 key) before it's written, so the repository and the site can be public while only
people with the password can read the leads.
"""
import argparse
import base64
import io
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import PROJECT_DIR, load_config
from .db import LeadDB
from .export import write_csv, write_xlsx

log = logging.getLogger("leadgen.publish")

WEB_DIR = Path(__file__).resolve().parent / "web"
SITE_DIR = PROJECT_DIR / "docs"
STATE_FILE = PROJECT_DIR / "state" / "leads.sqlite.enc"
ITERATIONS = 600_000


def _key(password, salt, iterations):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def encrypt(data: bytes, password: str, iterations=ITERATIONS) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(password, salt, iterations)).encrypt(iv, data, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": iterations, "salt": b64(salt), "iv": b64(iv), "data": b64(ct)}


def decrypt(bundle: dict, password: str) -> bytes:
    b = lambda k: base64.b64decode(bundle[k])
    return AESGCM(_key(password, b("salt"), bundle["iter"])).decrypt(b("iv"), b("data"), None)


def build_site(cfg, password, site_dir=SITE_DIR):
    """Write index.html, app.css, app.js and the encrypted data.enc.json for the latest finished run."""
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(WEB_DIR / "templates"), autoescape=select_autoescape())
    (site_dir / "index.html").write_text(env.get_template("published.html").render(), encoding="utf-8")
    for name in ("app.css", "app.js"):
        shutil.copyfile(WEB_DIR / "static" / name, site_dir / name)
    (site_dir / ".nojekyll").write_text("")

    db = LeadDB(cfg.path("database"))
    run_id = db.latest_finished_run()
    if not run_id:
        db.close()
        log.warning("no finished search in the database yet; published the site without data")
        return None
    run = next(r for r in db.recent_runs(1000) if r["id"] == run_id)
    leads = db.leads_for_run(run_id)
    db.close()

    csv_buf, xlsx_buf = io.BytesIO(), io.BytesIO()
    write_csv(leads, csv_buf)
    write_xlsx(leads, xlsx_buf)
    payload = {
        "generated_at": time.time(),
        "run": {k: run[k] for k in ("id", "started_at", "finished_at", "cities", "lead_count")},
        "leads": leads,
        "csv_b64": base64.b64encode(csv_buf.getvalue()).decode("ascii"),
        "xlsx_b64": base64.b64encode(xlsx_buf.getvalue()).decode("ascii"),
    }
    raw = json.dumps(payload, default=str).encode("utf-8")
    (site_dir / "data.enc.json").write_text(json.dumps(encrypt(raw, password)), encoding="utf-8")
    log.info("published run %s (%d practices) to %s", run_id, len(leads), site_dir)
    return run_id


def save_state(cfg, password, state_file=STATE_FILE):
    """Encrypt the SQLite history so growth tracking survives between GitHub Actions runs."""
    db_path = cfg.path("database")
    if not db_path.exists():
        return False
    state_file = Path(state_file)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(encrypt(db_path.read_bytes(), password)), encoding="utf-8")
    log.info("saved encrypted history to %s", state_file)
    return True


def restore_state(cfg, password, state_file=STATE_FILE):
    state_file = Path(state_file)
    if not state_file.exists():
        log.info("no saved history yet; starting fresh")
        return False
    data = decrypt(json.loads(state_file.read_text(encoding="utf-8")), password)
    db_path = cfg.path("database")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(data)
    log.info("restored history to %s", db_path)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leadgen.publish", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["restore", "build"])
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    password = os.getenv("LEADGEN_SITE_PASSWORD", "")
    if len(password) < 8:
        sys.exit("Set LEADGEN_SITE_PASSWORD (at least 8 characters). It protects the published leads.")
    cfg = load_config()
    if args.action == "restore":
        try:
            restore_state(cfg, password)
        except Exception:
            sys.exit("Couldn't unlock state/leads.sqlite.enc. Was LEADGEN_SITE_PASSWORD changed? "
                     "Put the old password back, or delete that file to start the history over.")
    else:
        build_site(cfg, password)
        save_state(cfg, password)


if __name__ == "__main__":
    main()
