(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, body) {
    const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }

  // ------------------------------------------------------------------ Search page

  function initHome() {
    const form = $("#search-form");
    const msg = $("#form-msg");
    const estimateBox = $("#estimate");
    let lastRequest = null;

    function readForm() {
      const fd = new FormData(form);
      return { cities: fd.getAll("cities"), limit: fd.get("limit") || null, refresh: fd.get("refresh") === "on" };
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.textContent = "Calculating…";
      try {
        lastRequest = readForm();
        const est = await api("/api/estimate", lastRequest);
        $("#estimate-text").textContent = est.text;
        $("#cost-line").innerHTML = est.max_requests === 0
          ? "<b>No billable Google requests:</b> everything needed is already saved from an earlier search."
          : `Up to <b>${est.max_requests.toLocaleString()}</b> billable Google requests, <b>at most $${est.max_cost.toFixed(2)}</b> (before Google's monthly free usage).`;
        const start = $("#start-btn");
        start.disabled = !est.has_key;
        start.title = est.has_key ? "" : "Add your Google Places API key in Settings first";
        if (!est.has_key) $("#cost-line").innerHTML += `<br><span class="pill bad">Add your Google key in <a href="/settings#keys">Settings</a> to start.</span>`;
        estimateBox.hidden = false;
        msg.textContent = "";
      } catch (err) {
        msg.textContent = err.message;
      }
    });

    form.addEventListener("change", () => { estimateBox.hidden = true; });
    $("#cancel-estimate").addEventListener("click", () => { estimateBox.hidden = true; });

    $("#start-btn").addEventListener("click", async () => {
      $("#start-btn").disabled = true;
      try {
        await api("/api/run", lastRequest || readForm());
        estimateBox.hidden = true;
        logIndex = 0;
        $("#job-log").textContent = "";
        poll();
      } catch (err) {
        msg.textContent = err.message;
      } finally {
        $("#start-btn").disabled = false;
      }
    });

    $("#job-stop").addEventListener("click", async () => {
      if (!confirm("Stop this search? Practices already processed are kept.")) return;
      $("#job-stop").disabled = true;
      await api("/api/job/stop", {}).catch(() => {});
    });

    let logIndex = 0;
    let timer = null;
    let sawRunning = false;
    const STATUS = {
      running: ["info", "Running"], done: ["good", "Finished"], failed: ["bad", "Failed"], stopped: ["warn", "Stopped"],
    };
    const PHASE = { starting: "Starting…", search: "Searching Google", crawl: "Reading practice websites", done: "Finished" };

    async function poll() {
      clearTimeout(timer);
      let job;
      try {
        job = await api(`/api/job?after=${logIndex}`);
      } catch {
        timer = setTimeout(poll, 3000);
        return;
      }
      if (!job) return;
      const box = $("#job");
      box.hidden = false;
      const [cls, label] = STATUS[job.status] || ["neutral", job.status];
      const pill = $("#job-status");
      pill.className = `pill ${cls}`;
      pill.textContent = job.stopping && job.status === "running" ? "Stopping…" : label;

      const bar = $("#job-progress");
      const pct = job.total ? Math.round((100 * job.done) / job.total) : 0;
      bar.classList.toggle("indeterminate", job.status === "running" && !job.total);
      bar.firstElementChild.style.width = job.status === "done" ? "100%" : `${pct}%`;
      const phase = PHASE[job.phase] || job.phase;
      $("#job-phase").textContent = job.status === "running"
        ? `${phase}${job.total ? ` · ${job.done} of ${job.total}` : ""}`
        : job.lead_count != null ? `${job.lead_count} practices processed.` : "";

      const log = $("#job-log");
      const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      for (const line of job.log) {
        const div = document.createElement("div");
        div.className = line.level;
        div.textContent = `${new Date(line.t * 1000).toLocaleTimeString()}  ${line.msg}`;
        log.appendChild(div);
      }
      if (atBottom) log.scrollTop = log.scrollHeight;
      logIndex = job.next;

      $("#job-stop").hidden = job.status !== "running";
      $("#job-stop").disabled = job.stopping;
      const view = $("#job-view");
      view.hidden = !(job.status !== "running" && job.lead_count != null && job.run_id);
      view.href = `/leads?run=${job.run_id}`;

      if (job.status === "running") {
        sawRunning = true;
        timer = setTimeout(poll, 1500);
      } else if (sawRunning) {
        location.reload();  // refresh "Recent searches" and the status cards
      }
    }
    poll();
  }

  // ------------------------------------------------------------------ Leads page

  function initLeads() {
    const select = $("#run-select");
    if (select) select.addEventListener("change", () => { location.href = `/leads?run=${select.value}`; });
    const dataEl = $("#leads-data");
    if (dataEl) setupLeadsTable(JSON.parse(dataEl.textContent));
  }

  // Filterable, sortable leads table (templates/_leads_table.html). Used by both sites.
  function setupLeadsTable(leads) {
    const tbody = $("#leads-table tbody");
    const citySelect = $("#f-city");
    [...new Set(leads.map((l) => l.city).filter(Boolean))].sort().forEach((c) => citySelect.add(new Option(c, c)));
    let sortKey = "score";
    let sortDir = -1;
    const open = new Set();

    const f = { text: $("#f-text"), city: $("#f-city"), score: $("#f-score"), email: $("#f-email"), excluded: $("#f-excluded") };
    Object.values(f).forEach((el) => el.addEventListener("input", render));

    document.querySelectorAll("th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        sortDir = key === sortKey ? -sortDir : (["name", "head_dentist"].includes(key) ? 1 : -1);
        sortKey = key;
        render();
      });
    });

    function emailBadge(l) {
      if (!l.email) return '<span class="pill neutral">none found</span>';
      if (l.email_source === "website") return '<span class="pill good">verified on site</span>';
      if (l.email_source === "Hunter") return '<span class="pill info">found by Hunter</span>';
      return '<span class="pill warn">pattern guess</span>';
    }

    function matches(l) {
      if (!f.excluded.checked && l.exclude_reason) return false;
      if (f.city.value && l.city !== f.city.value) return false;
      if ((l.score || 0) < Number(f.score.value || 0)) return false;
      const em = f.email.value;
      if (em === "has" && !l.email) return false;
      if (em === "none" && l.email) return false;
      if (em === "website" && l.email_source !== "website") return false;
      if (em === "confirmed" && !["website", "Hunter"].includes(l.email_source)) return false;
      const q = f.text.value.trim().toLowerCase();
      if (q) {
        const hay = [l.name, l.head_dentist, l.email, l.address, l.city, l.website, l.why].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }

    function cmp(a, b) {
      let x = a[sortKey], y = b[sortKey];
      const missX = x === null || x === undefined || x === "", missY = y === null || y === undefined || y === "";
      if (missX || missY) return missX - missY;  // blanks always last
      if (typeof x === "string") return sortDir * x.localeCompare(y);
      return sortDir * (x - y);
    }

    function plural(n, word) {
      return `${n} ${word}${n === 1 ? "" : "s"}`;
    }

    function host(url) {
      try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
    }

    function detail(l) {
      const items = [
        ["Address", esc(l.address)],
        ["Website", l.website ? `<a href="${esc(l.website)}" target="_blank" rel="noopener">${esc(l.website)}</a>` : "none"],
        ["Google Maps", l.maps_url ? `<a href="${esc(l.maps_url)}" target="_blank" rel="noopener">Open listing</a>` : ""],
        ["Email status", esc(l.email_status || "no email found")],
        ["Other emails", esc(l.other_emails || "–")],
        ["Other dentists found", esc(l.other_dentists || "–")],
        ["Dentists found", esc(l.dentist_count ?? "–")],
        ["Practice type", esc(l.practice_types_display || "")],
        ["Practice age basis", esc(l.age_basis || "")],
        ["Domain registered", esc(l.domain_created || "–")],
        ["Website est. year", esc(l.established_year || "–")],
        ["Oldest review seen", esc(l.oldest_review || "–")],
        ["Review velocity", esc(l.velocity_display || "–")],
        ["Rating change", l.rating_change != null ? esc(l.rating_change) : "–"],
        ["TX license (TSBDE)", esc(l.tx_license || "–")],
        ["Status", esc(l.business_status || "")],
      ];
      if (l.exclude_reason) items.unshift(["Excluded because", esc(l.exclude_reason)]);
      return `<div class="detail-grid">${items.map(([k, v]) => `<div><b>${k}</b>${v}</div>`).join("")}</div>`;
    }

    function render() {
      document.querySelectorAll("th.sortable").forEach((th) => {
        th.querySelector(".arrow")?.remove();
        if (th.dataset.key === sortKey) th.insertAdjacentHTML("beforeend", `<span class="arrow">${sortDir > 0 ? "▲" : "▼"}</span>`);
      });
      const rows = leads.filter(matches).sort(cmp);
      const kept = leads.filter((l) => !l.exclude_reason);
      $("#summary").innerHTML = [
        `<span class="pill neutral">Showing ${rows.length} of ${f.excluded.checked ? leads.length : kept.length}</span>`,
        `<span class="pill good">${plural(kept.filter((l) => l.email_source === "website").length, "email")} verified on site</span>`,
        `<span class="pill info">${kept.filter((l) => l.score >= 70).length} scoring 70+</span>`,
        `<span class="pill neutral">${leads.length - kept.length} excluded</span>`,
      ].join("");

      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="10" class="empty">No practices match these filters.</td></tr>`;
        return;
      }
      tbody.innerHTML = rows.map((l) => {
        const score = Math.round(l.score || 0);
        const main = `<tr class="lead ${l.exclude_reason ? "excluded" : ""}" data-id="${esc(l.place_id)}" tabindex="0" aria-expanded="${open.has(l.place_id)}">
          <td><div class="score">${l.exclude_reason ? "–" : score}<div class="bar"><div style="width:${score}%"></div></div></div></td>
          <td class="col-practice"><div class="name">${esc(l.name)}</div><div class="sub">${esc(l.city)}${l.website ? ` · <a href="${esc(l.website)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(host(l.website))}</a>` : ""}</div></td>
          <td class="col-dentist">${esc(l.head_dentist || "–")}</td>
          <td class="col-email">${l.email ? `<div class="email"><a href="mailto:${esc(l.email)}" onclick="event.stopPropagation()">${esc(l.email)}</a></div>` : ""}${emailBadge(l)}</td>
          <td style="white-space:nowrap">${l.phone ? `<a href="tel:${esc(l.phone.replace(/[^0-9+]/g, ""))}" onclick="event.stopPropagation()">${esc(l.phone)}</a>` : "–"}</td>
          <td class="num">${l.rating != null ? `${l.rating}★` : "–"}</td>
          <td class="num">${l.review_count ?? "–"}</td>
          <td class="num">${l.age_years != null ? `~${l.age_years} yrs` : "?"}</td>
          <td class="num">${l.velocity != null ? `${l.velocity_source === "tracked" ? "" : "~"}${Number(l.velocity).toFixed(1)}` : "–"}</td>
          <td class="why">${esc(l.why)}</td>
        </tr>`;
        return main + (open.has(l.place_id) ? `<tr class="detail"><td colspan="10">${detail(l)}</td></tr>` : "");
      }).join("");
    }

    function toggle(row) {
      const id = row.dataset.id;
      open.has(id) ? open.delete(id) : open.add(id);
      render();
    }
    tbody.addEventListener("click", (e) => {
      const row = e.target.closest("tr.lead");
      if (row) toggle(row);
    });
    tbody.addEventListener("keydown", (e) => {
      const row = e.target.closest("tr.lead");
      if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggle(row); }
    });
    render();
  }

  // ------------------------------------------------------------------ Published site (GitHub Pages)

  const b64bytes = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

  async function decryptBundle(bundle, password) {
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
    const key = await crypto.subtle.deriveKey(
      { name: "PBKDF2", hash: "SHA-256", salt: b64bytes(bundle.salt), iterations: bundle.iter },
      base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64bytes(bundle.iv) }, key, b64bytes(bundle.data));
    return JSON.parse(new TextDecoder().decode(plain));
  }

  function storage(action, value) {
    try {
      if (action === "get") return localStorage.getItem("dental-leads-pw");
      if (action === "set") localStorage.setItem("dental-leads-pw", value);
      if (action === "clear") localStorage.removeItem("dental-leads-pw");
    } catch { /* private mode or blocked storage: just don't remember */ }
    return null;
  }

  async function initPublished() {
    const form = $("#unlock-form");
    const msg = $("#unlock-msg");
    let bundle;
    try {
      const res = await fetch(`data.enc.json?t=${Date.now()}`);
      if (!res.ok) throw new Error();
      bundle = await res.json();
    } catch {
      $("#unlock").hidden = true;
      $("#no-data").hidden = false;
      return;
    }

    async function unlock(password, remember) {
      msg.textContent = "Unlocking…";
      let data;
      try {
        data = await decryptBundle(bundle, password);
      } catch {
        storage("clear");
        msg.textContent = "That password didn't work.";
        return;
      }
      if (remember) storage("set", password);
      msg.textContent = "";
      $("#unlock").hidden = true;
      $("#content").hidden = false;
      $("#updated").textContent = `Updated ${new Date(data.generated_at * 1000).toLocaleString()} · ${data.run.cities.replaceAll(",", ", ")}`;
      for (const [id, field, type, name] of [
        ["#dl-xlsx", "xlsx_b64", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "dental-leads.xlsx"],
        ["#dl-csv", "csv_b64", "text/csv", "dental-leads.csv"],
      ]) {
        const link = $(id);
        link.href = URL.createObjectURL(new Blob([b64bytes(data[field])], { type }));
        link.download = name;
      }
      if (data.leads.length) {
        setupLeadsTable(data.leads);
      } else {
        $("#leads-area").innerHTML = '<div class="card empty"><h2>No practices found</h2><p>The last search found no practices.</p></div>';
      }
    }

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      unlock($("#password").value, $("#remember").checked);
    });
    $("#lock").addEventListener("click", () => { storage("clear"); location.reload(); });
    const saved = storage("get");
    if (saved) unlock(saved, true);
  }

  const page = document.body.dataset.page;
  if (page === "home") initHome();
  if (page === "leads") initLeads();
  if (page === "published") initPublished();
})();
