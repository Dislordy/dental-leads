"""Local web app: change settings, start searches, watch progress and browse/download leads."""
import datetime as dt
import hmac
import io
import os
from urllib.parse import urlsplit

from flask import (Flask, abort, flash, jsonify, make_response, redirect, render_template, request, send_file,
                   url_for)

from .. import config as configmod
from ..db import LeadDB
from ..export import write_csv, write_xlsx
from ..pipeline import estimate_for
from . import settings_schema as schema
from .jobs import Busy, JobManager


def create_app(password=None):
    app = Flask(__name__)
    app.secret_key = os.urandom(24)
    app.config["PASSWORD"] = password or os.getenv("LEADGEN_WEB_PASSWORD", "")
    jobs = JobManager(lambda: configmod.load_config())
    app.extensions["jobs"] = jobs

    def cfg():
        return configmod.load_config()

    def db():
        return LeadDB(cfg().path("database"))

    # ------------------------------------------------------------------ guards

    @app.before_request
    def guard():
        pw = app.config["PASSWORD"]
        if pw:
            auth = request.authorization
            if not auth or not hmac.compare_digest(auth.password or "", pw):
                return ("Password required", 401, {"WWW-Authenticate": 'Basic realm="Dental Leads"'})
        # Block other websites from POSTing to this app from your browser.
        if request.method == "POST":
            origin = request.headers.get("Origin") or request.headers.get("Referer")
            if origin and urlsplit(origin).netloc != request.host:
                abort(403)

    @app.template_filter("when")
    def when(ts):
        return dt.datetime.fromtimestamp(ts).strftime("%b %d, %Y %I:%M %p") if ts else ""

    @app.context_processor
    def globals_():
        return {"nav": request.endpoint}

    # ------------------------------------------------------------------ pages

    @app.get("/")
    def home():
        c = cfg()
        d = db()
        runs = d.recent_runs(10)
        d.close()
        job = jobs.current.to_dict() if jobs.current else None
        return render_template("home.html", cities=list(c["cities"]), runs=runs, job=job,
                               google_key=bool(c["google_api_key"]), hunter_key=bool(c["hunter_api_key"]))

    @app.get("/leads")
    def leads():
        d = db()
        runs = [r for r in d.recent_runs(50) if r["finished_at"]]
        run_id = request.args.get("run", type=int) or (runs[0]["id"] if runs else None)
        rows = d.leads_for_run(run_id) if run_id else []
        d.close()
        run = next((r for r in runs if r["id"] == run_id), None)
        return render_template("leads.html", runs=runs, run=run, run_id=run_id, leads=rows)

    @app.get("/leads/<int:run_id>.<fmt>")
    def download(run_id, fmt):
        d = db()
        rows = d.leads_for_run(run_id)
        d.close()
        if not rows:
            abort(404)
        buf = io.BytesIO()
        if fmt == "csv":
            write_csv(rows, buf)
            mime = "text/csv"
        elif fmt == "xlsx":
            write_xlsx(rows, buf)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            abort(404)
        buf.seek(0)
        return send_file(buf, mimetype=mime, as_attachment=True, download_name=f"dental-leads-run{run_id}.{fmt}")

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        c = cfg()
        form_values, errors = None, []
        if request.method == "POST":
            if request.form.get("action") == "reset":
                if configmod.LOCAL_SETTINGS.exists():
                    configmod.LOCAL_SETTINGS.unlink()
                flash("Settings reset to the defaults in config.yaml.")
                return redirect(url_for("settings"))
            overrides, errors = schema.parse_form(request.form)
            if not errors:
                configmod.save_local_settings(overrides)
                keys = {}
                for k in configmod.ENV_KEYS:
                    new = request.form.get(k, "").strip()
                    if request.form.get(f"clear_{k}") == "on":
                        keys[k] = ""
                    elif new:
                        keys[k] = new
                if keys:
                    configmod.save_env(keys)
                flash("Settings saved.")
                return redirect(url_for("settings"))
            form_values = request.form
        return render_template("settings.html", sections=_render_sections(c, form_values), errors=errors,
                               types=schema.PRACTICE_TYPES, local_exists=configmod.LOCAL_SETTINGS.exists(),
                               keys={"GOOGLE_PLACES_API_KEY": _mask(c["google_api_key"]),
                                     "HUNTER_API_KEY": _mask(c["hunter_api_key"])})

    # ------------------------------------------------------------------ API

    def _job_args():
        body = request.get_json(silent=True) or {}
        c = cfg()
        known = {x.lower(): x for x in c["cities"]}
        cities = [known[x.lower()] for x in body.get("cities", []) if x.lower() in known]
        if not cities:
            _bad("Choose at least one city.")
        limit = body.get("limit")
        try:
            limit = int(limit) if limit not in (None, "") else None
        except (TypeError, ValueError):
            _bad("Limit must be a whole number.")
        if limit is not None and limit < 1:
            _bad("Limit must be at least 1.")
        return c, cities, limit, bool(body.get("refresh"))

    @app.post("/api/estimate")
    def api_estimate():
        c, cities, limit, refresh = _job_args()
        text, hi = estimate_for(c, cities, limit)
        price = float(c["places"].get("price_per_1000", 0))
        return jsonify(text=text.strip(), max_requests=hi, max_cost=round(hi * price / 1000, 2),
                       has_key=bool(c["google_api_key"]))

    @app.post("/api/run")
    def api_run():
        c, cities, limit, refresh = _job_args()
        if not c["google_api_key"]:
            return jsonify(error="Add your Google Places API key on the Settings page first."), 400
        try:
            job = jobs.start(cities, limit, refresh)
        except Busy as e:
            return jsonify(error=str(e)), 409
        return jsonify(job.to_dict())

    @app.get("/api/job")
    def api_job():
        if not jobs.current:
            return jsonify(None)
        return jsonify(jobs.current.to_dict(after=request.args.get("after", 0, type=int)))

    @app.post("/api/job/stop")
    def api_stop():
        if jobs.current and jobs.current.status == "running":
            jobs.current.stop.set()
        return jsonify(jobs.current.to_dict() if jobs.current else None)

    return app


def _bad(message):
    abort(make_response(jsonify(error=message), 400))


def _mask(key):
    return f"set, ending in …{key[-4:]}" if key else ""


def _render_sections(c, form_values):
    """Settings fields with their current values (or the values just submitted, on error)."""
    out = []
    for title, fields in schema.SECTIONS:
        rows = []
        for path, label, kind, help_ in fields:
            name = schema.field_name(path)
            if form_values is not None:
                if kind == "bool":
                    value = form_values.get(name) == "on"
                elif kind == "types":
                    value = form_values.getlist(name)
                else:
                    value = form_values.get(name, "")
            else:
                value = schema.get_path(c, path)
                if kind == "lines":
                    value = "\n".join(value or [])
                elif kind == "float" and isinstance(value, float) and value.is_integer():
                    value = int(value)
            rows.append({"name": name, "label": label, "kind": kind, "help": help_, "value": value})
        out.append((title, rows))
    return out
