/* Web Scraper — single-page UI: jobs list, 7-step job wizard, live dashboard, results browser. */
"use strict";

(function () {
  const app = document.getElementById("app");
  let META = null;
  let pollTimer = null;
  let pendingPrefill = null;

  // ------------------------------------------------------------------------------------------
  // helpers
  // ------------------------------------------------------------------------------------------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style") el.setAttribute("style", v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else if (k === "checked") el.checked = !!v;
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  // Like el.replaceChildren(), but flattens arrays and skips null/false (replaceChildren would print them).
  function fill(el, ...children) {
    el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false)
      .map((c) => (c instanceof Node ? c : document.createTextNode(String(c)))));
    return el;
  }

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: { "X-Requested-With": "webscraper" } };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || ("HTTP " + res.status));
      err.data = data;
      throw err;
    }
    return data;
  }

  function toast(msg, ms = 3200) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(t._timer);
    t._timer = setTimeout(() => (t.hidden = true), ms);
  }

  function fmtNum(n) { return (n === null || n === undefined || n === "") ? "–" : Number(n).toLocaleString(); }
  function fmtTime(s) {
    s = Math.round(s || 0);
    if (s < 60) return s + "s";
    const m = Math.floor(s / 60);
    return m < 60 ? m + "m " + (s % 60) + "s" : Math.floor(m / 60) + "h " + (m % 60) + "m";
  }
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString();
  }
  function cellText(v) {
    if (v === null || v === undefined) return "";
    if (Array.isArray(v)) return v.map(cellText).join("; ");
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }
  function isUrl(v) { return typeof v === "string" && /^https?:\/\//i.test(v); }
  function linkOrText(v) {
    if (isUrl(v)) return h("a", { href: v, target: "_blank", rel: "noopener noreferrer" }, v.replace(/^https?:\/\//, ""));
    return cellText(v);
  }
  function qClass(q) { return q >= 75 ? "q hi" : q >= 50 ? "q mid" : "q lo"; }

  const FINAL = ["COMPLETED", "FAILED", "CANCELLED", "PARTIALLY_COMPLETED", "SOURCE_BLOCKED", "INTERRUPTED"];
  function stateBadge(state, running) {
    const s = state || "CREATED";
    let cls = "badge";
    if (running && !FINAL.includes(s)) cls += " running";
    else if (s === "COMPLETED") cls += " ok";
    else if (s === "PARTIALLY_COMPLETED" || s === "CANCELLED" || s === "PAUSED" || s === "INTERRUPTED") cls += " warn";
    else if (s === "FAILED" || s === "SOURCE_BLOCKED") cls += " bad";
    return h("span", { class: cls }, s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()));
  }

  function setNav(which) {
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === which));
  }

  function stopPolling() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } }

  // ------------------------------------------------------------------------------------------
  // routing
  // ------------------------------------------------------------------------------------------
  async function route() {
    stopPolling();
    closeDrawer();
    if (!META) META = await api("/api/meta");
    const hash = location.hash || "#/";
    let m;
    if (hash === "#/" || hash === "#") { setNav("jobs"); return viewJobs(); }
    if (hash === "#/new") {
      setNav("new");
      const prefill = pendingPrefill;
      pendingPrefill = null;
      return viewWizard(prefill);
    }
    if ((m = hash.match(/^#\/jobs\/([\w-]+)$/))) { setNav("jobs"); return viewJob(m[1]); }
    location.hash = "#/";
  }
  window.addEventListener("hashchange", () => route().catch(showFatal));

  function showFatal(err) {
    app.replaceChildren(h("div", { class: "card errors" }, "Something went wrong: " + (err && err.message || err)));
  }

  // ------------------------------------------------------------------------------------------
  // jobs list
  // ------------------------------------------------------------------------------------------
  async function viewJobs() {
    const { jobs } = await api("/api/jobs");
    const head = h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Scraping jobs"),
        h("div", { class: "muted" }, "Results are saved to " + META.output_dir)),
      h("a", { class: "btn btn-primary", href: "#/new" }, "+ New job"));
    if (!jobs.length) {
      app.replaceChildren(head, h("div", { class: "card empty" },
        h("h2", {}, "No jobs yet"),
        h("p", {}, "Describe the records you need — who, where, which fields and how many — and the scraper finds sources, crawls, extracts, cleans and exports them."),
        h("a", { class: "btn btn-primary", href: "#/new" }, "Create your first job")));
      return;
    }
    const rows = jobs.map((j) => {
      const m = j.metrics || {};
      const valid = m.final_unique || m.records_valid || 0;
      return h("tr", { class: "clickable", onclick: () => (location.hash = "#/jobs/" + j.id) },
        h("td", {}, h("div", { style: "font-weight:600" }, j.name), h("div", { class: "muted small" }, j.entity + " · " + j.id)),
        h("td", {}, stateBadge(j.state, j.running)),
        h("td", { class: "num" }, fmtNum(valid), m.target_maximum ? h("span", { class: "muted" }, " / " + fmtNum(m.target_maximum)) : ""),
        h("td", { class: "num" }, fmtNum(m.pages_fetched)),
        h("td", { class: "num" }, m.average_quality ? h("span", { class: qClass(m.average_quality) }, Math.round(m.average_quality)) : "–"),
        h("td", { class: "muted nowrap" }, fmtDate(j.created_at)));
    });
    app.replaceChildren(head, h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Job"), h("th", {}, "Status"), h("th", { class: "num" }, "Records"),
        h("th", { class: "num" }, "Pages"), h("th", { class: "num" }, "Avg quality"), h("th", {}, "Created"))),
      h("tbody", {}, rows))));
    if (jobs.some((j) => j.running)) pollTimer = setTimeout(() => route().catch(showFatal), 2000);
  }

  // ------------------------------------------------------------------------------------------
  // wizard
  // ------------------------------------------------------------------------------------------
  const STEPS = ["Target", "Targeting", "Fields", "Sources", "Record count", "Output", "Review"];
  let draft = null;
  let step = 0;

  function entityMeta(key) { return META.entities.find((e) => e.key === key) || META.entities[0]; }

  function newDraft(entity = "business") {
    const e = entityMeta(entity);
    return {
      name: "",
      entity: e.key,
      description: "",
      location: { country: "", province: "", city: "", suburb: "", postal_code: "" },
      industry: [], keywordsRequired: [], keywordsPreferred: [], exclude: [], filters: [], strict: true,
      fields: e.fields.map((f) => Object.assign({ enabled: true }, f)),
      sourceMode: e.mode === "site" ? "auto" : "manual",
      sources: [], discovery: { search_engines: true, directories: true, company_websites: true, industry_directories: true },
      min: 50, max: 100, stopAtMin: true, minQuality: 0,
      format: "xlsx", extraFormats: [], filename: "",
      include: { source: true, timestamp: true, quality: true, provenance: true, raw: false, rejected: false },
      crawl: { delay: 1, concurrency: 6, max_pages: 0, max_depth: 3, render_js: "auto", respect_robots: true, max_runtime_minutes: 0 },
    };
  }

  function draftToJob(d) {
    const loc = {};
    for (const [k, v] of Object.entries(d.location)) if (v && String(v).trim()) loc[k] = String(v).trim();
    const required = {}, preferred = {}, excluded = {};
    if (Object.keys(loc).length) required.location = loc;
    if (d.industry.length) required.industry = d.industry;
    if (d.keywordsRequired.length) required.keywords = d.keywordsRequired;
    if (d.keywordsPreferred.length) preferred.keywords = d.keywordsPreferred;
    if (d.exclude.length) excluded.terms = d.exclude;
    for (const f of d.filters) {
      if (!f.field) continue;
      let val;
      if (f.op === "between" || f.op === "min" || f.op === "max") {
        val = {};
        if (f.op !== "max" && f.a !== "") val.min = Number(f.a);
        if (f.op !== "min" && (f.op === "max" ? f.a : f.b) !== "") val.max = Number(f.op === "max" ? f.a : f.b);
      } else {
        val = f.a.split(",").map((s) => s.trim()).filter(Boolean);
      }
      (f.kind === "preferred" ? preferred : f.kind === "excluded" ? excluded : required)[f.field] = val;
    }
    const fields = d.fields.filter((f) => f.enabled).map((f) => {
      const o = { name: f.name, type: f.type, required: !!f.required, priority: f.priority || "medium" };
      if (f.label) o.label = f.label;
      for (const k of ["selector", "attribute", "xpath", "regex"]) if (f[k]) o[k] = f[k];
      return o;
    });
    const job = {
      entity: d.entity,
      targeting: { description: d.description, required, preferred, excluded, strict: d.strict },
      fields,
      sources: { mode: d.sourceMode, urls: d.sources.filter((s) => s.url.trim()).map((s) => {
        const o = { url: s.url.trim(), type: s.type || "auto" };
        if (s.record_selector) o.record_selector = s.record_selector;
        return o;
      }), discovery: d.discovery },
      records: { minimum: Number(d.min), maximum: Number(d.max), stop_at_minimum: d.stopAtMin, min_quality: Number(d.minQuality) || 0 },
      crawl: Object.assign({}, d.crawl),
      output: {
        format: [d.format].concat(d.extraFormats.filter((f) => f !== d.format)),
        include_source: d.include.source, include_timestamp: d.include.timestamp,
        include_quality_score: d.include.quality, include_provenance: d.include.provenance,
        include_raw_data: d.include.raw, include_rejected: d.include.rejected,
      },
    };
    if (d.name) job.name = d.name;
    if (d.filename) job.output.filename = d.filename;
    if (!job.crawl.max_pages) delete job.crawl.max_pages;
    if (job.sources.mode === "auto" && job.sources.urls.length) job.sources.mode = "hybrid";
    return job;
  }

  function jobToDraft(job) {
    const d = newDraft(job.entity || "business");
    const t = job.targeting || {};
    const req = t.required || {}, pref = t.preferred || {}, exc = t.excluded || {};
    d.name = job.name || "";
    d.description = t.description || "";
    Object.assign(d.location, req.location || {});
    d.industry = req.industry || [];
    d.keywordsRequired = req.keywords || [];
    d.keywordsPreferred = pref.keywords || [];
    d.exclude = [].concat(exc.terms || [], exc.business_types || [], exc.company_types || [], exc.keywords || []);
    d.strict = t.strict !== false;
    if (job.fields && job.fields.length) {
      const known = d.fields.map((f) => f.name);
      d.fields.forEach((f) => (f.enabled = job.fields.some((x) => (x.name || x) === f.name)));
      job.fields.forEach((x) => {
        const name = x.name || x;
        const existing = d.fields.find((f) => f.name === name);
        if (existing) Object.assign(existing, typeof x === "object" ? x : {}, { enabled: true });
        else if (!known.includes(name)) d.fields.push(Object.assign({ enabled: true, type: "text" }, typeof x === "object" ? x : { name }));
      });
    }
    const s = job.sources || {};
    d.sourceMode = s.mode || (s.urls && s.urls.length ? "manual" : "auto");
    d.sources = (s.urls || []).map((u) => (typeof u === "string" ? { url: u, type: "auto" } : Object.assign({ type: "auto" }, u)));
    if (s.discovery) Object.assign(d.discovery, s.discovery);
    const r = job.records || {};
    d.min = r.minimum || d.min; d.max = r.maximum || d.max;
    d.stopAtMin = r.stop_at_minimum !== undefined ? r.stop_at_minimum : d.stopAtMin;
    d.minQuality = r.min_quality || 0;
    const o = job.output || {};
    const fmts = [].concat(o.format || "xlsx");
    d.format = fmts[0]; d.extraFormats = fmts.slice(1).concat(o.formats || []);
    d.filename = o.filename || "";
    d.include = { source: o.include_source !== false, timestamp: o.include_timestamp !== false,
      quality: o.include_quality_score !== false, provenance: !!o.include_provenance, raw: !!o.include_raw_data,
      rejected: !!o.include_rejected };
    Object.assign(d.crawl, job.crawl || {});
    return d;
  }

  function tagInput(list, placeholder) {
    const wrap = h("div", { class: "tags" });
    const input = h("input", { type: "text", placeholder });
    function render() {
      wrap.replaceChildren(...list.map((t, i) => h("span", { class: "tag" }, t,
        h("button", { type: "button", "aria-label": "Remove " + t, onclick: () => { list.splice(i, 1); render(); } }, "×"))), input);
    }
    function commit() {
      const parts = input.value.split(",").map((s) => s.trim()).filter(Boolean);
      for (const p of parts) if (!list.includes(p)) list.push(p);
      input.value = "";
      render();
      input.focus();
    }
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); }
      else if (e.key === "Backspace" && !input.value && list.length) { list.pop(); render(); input.focus(); }
    });
    input.addEventListener("blur", () => { if (input.value.trim()) commit(); });
    render();
    return wrap;
  }

  function bind(obj, key, type = "text", attrs = {}) {
    const el = h("input", Object.assign({ type, value: obj[key] === undefined ? "" : obj[key] }, attrs));
    el.addEventListener("input", () => { obj[key] = type === "number" ? (el.value === "" ? "" : Number(el.value)) : el.value; });
    return el;
  }
  function bindCheck(obj, key, label, hint) {
    const el = h("input", { type: "checkbox", checked: !!obj[key] });
    el.addEventListener("change", () => { obj[key] = el.checked; });
    return h("label", { class: "check" }, el, h("span", {}, label, hint ? h("span", { class: "hint" }, hint) : null));
  }
  function bindSelect(obj, key, options, onchange) {
    const el = h("select", {}, options.map(([v, l]) => h("option", { value: v }, l)));
    el.value = obj[key];
    el.addEventListener("change", () => { obj[key] = el.value; if (onchange) onchange(el.value); });
    return el;
  }
  function field(label, control, hint) {
    return h("label", { class: "field" }, h("span", {}, label), control, hint ? h("small", {}, hint) : null);
  }

  function viewWizard(prefill) {
    if (prefill) { draft = jobToDraft(prefill); step = 0; }
    if (!draft) { draft = newDraft(); step = 0; }
    const nav = h("ol", { class: "steps" }, STEPS.map((s, i) =>
      h("li", {}, h("button", { type: "button", class: (i === step ? "active" : "") + (i < step ? " done" : ""),
        onclick: () => { step = i; viewWizard(); } }, h("span", { class: "n" }, i < step ? "✓" : i + 1), s))));
    const body = h("div", { class: "card" });
    const foot = h("div", { class: "wizard-foot" },
      h("div", {}, step > 0 ? h("button", { class: "btn", onclick: () => { step--; viewWizard(); } }, "← Back") : null,
        " ", h("button", { class: "btn btn-ghost", onclick: () => { if (confirm("Discard this job and start over?")) { draft = newDraft(); step = 0; viewWizard(); } } }, "Reset")),
      step < STEPS.length - 1
        ? h("button", { class: "btn btn-primary", onclick: () => { step++; viewWizard(); } }, "Next →")
        : h("span"));
    const builders = [stepTarget, stepTargeting, stepFields, stepSources, stepRecords, stepOutput, stepReview];
    body.append(h("h2", {}, (step + 1) + ". " + STEPS[step]), builders[step](), foot);
    app.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "New scraping job"),
        h("div", { class: "muted" }, "Define what records you need; the scraper works out where and how to collect them."))),
      h("div", { class: "wizard" }, h("div", {}, nav), body));
  }

  function stepTarget() {
    const grid = h("div", { class: "entity-grid", role: "radiogroup" });
    for (const e of META.entities) {
      const input = h("input", { type: "radio", name: "entity", value: e.key, checked: draft.entity === e.key });
      input.addEventListener("change", () => {
        const keep = draft;
        const fresh = newDraft(e.key);
        fresh.description = keep.description; fresh.location = keep.location; fresh.industry = keep.industry;
        fresh.keywordsRequired = keep.keywordsRequired; fresh.keywordsPreferred = keep.keywordsPreferred; fresh.exclude = keep.exclude;
        fresh.name = keep.name;
        draft = fresh;
        viewWizard();
      });
      grid.append(h("label", { class: draft.entity === e.key ? "sel" : "" }, input, e.label));
    }
    const em = entityMeta(draft.entity);
    return h("div", {},
      h("p", { class: "muted" }, "What kind of records do you want?"),
      grid,
      h("p", { class: "hint" }, em.mode === "site"
        ? "One record per organisation/website. Directory listings and company websites are combined and de-duplicated."
        : "One record per page or listing item (e.g. each product, job or event)."),
      field("Describe the target", bind(draft, "description", "text", { placeholder: "Software companies in Cape Town" }),
        "Used for search queries. With automatic discovery, text like “… in Cape Town” also sets the location and industry if you leave the targeting step empty."),
      field("Job name (optional)", bind(draft, "name", "text", { placeholder: "Cape Town software companies" })));
  }

  function stepTargeting() {
    const loc = draft.location;
    const filtersBox = h("div", {});
    function renderFilters() {
      filtersBox.replaceChildren(...draft.filters.map((f, i) => h("div", { class: "grid grid-4", style: "margin-bottom:8px" },
        bind(f, "field", "text", { placeholder: "field, e.g. rating" }),
        bindSelect(f, "op", [["min", "at least"], ["max", "at most"], ["between", "between"], ["contains", "contains"]], () => renderFilters()),
        h("div", { class: "btn-row" }, bind(f, "a", "text", { placeholder: f.op === "contains" ? "value(s)" : "number" }),
          f.op === "between" ? bind(f, "b", "text", { placeholder: "and" }) : null),
        h("div", { class: "btn-row" }, bindSelect(f, "kind", [["required", "required"], ["preferred", "preferred"], ["excluded", "exclude"]]),
          h("button", { class: "btn btn-ghost", onclick: () => { draft.filters.splice(i, 1); renderFilters(); } }, "✕")))),
        h("button", { class: "btn btn-sm", onclick: () => { draft.filters.push({ field: "", op: "min", a: "", b: "", kind: "required" }); renderFilters(); } }, "+ Add filter"));
    }
    renderFilters();
    return h("div", {},
      h("h3", {}, "Location"),
      h("div", { class: "grid grid-3" },
        field("Country", bind(loc, "country", "text", { placeholder: "South Africa" })),
        field("Province / State", bind(loc, "province", "text", { placeholder: "Western Cape" })),
        field("City", bind(loc, "city", "text", { placeholder: "Cape Town" }))),
      h("div", { class: "grid grid-3" },
        field("Suburb / District", bind(loc, "suburb")),
        field("Postal code", bind(loc, "postal_code"))),
      h("h3", {}, "Industry & keywords"),
      field("Industry (required — any of)", tagInput(draft.industry, "Software, Technology… press Enter")),
      field("Required keywords (any of)", tagInput(draft.keywordsRequired, "optional")),
      field("Preferred keywords", tagInput(draft.keywordsPreferred, "SaaS, cloud… improve the score but are not required")),
      field("Exclude records mentioning", tagInput(draft.exclude, "Government, Non-profit…")),
      h("h3", {}, "Additional filters"),
      h("p", { class: "hint" }, "Constraints on extracted fields, e.g. rating at least 4, founded_year between 2010 and 2020."),
      filtersBox,
      bindCheck(draft, "strict", "Strict matching", "Records must mention the required location and industry on their pages. Turn off to keep near-matches (they still score lower)."));
  }

  function stepFields() {
    const list = h("div", {});
    const types = META.data_types;
    function render() {
      list.replaceChildren(h("div", { class: "field-row muted small" }, h("span"), h("span", {}, "Field"), h("span", {}, "Type"), h("span", {}, "Required"), h("span")),
        ...draft.fields.map((f, i) => {
          const en = h("input", { type: "checkbox", checked: f.enabled, "aria-label": "Extract " + f.name });
          en.addEventListener("change", () => { f.enabled = en.checked; });
          const req = h("input", { type: "checkbox", checked: !!f.required, "aria-label": f.name + " required" });
          req.addEventListener("change", () => { f.required = req.checked; });
          const adv = h("div", { class: "adv" + (f._adv ? "" : " hidden") },
            bind(f, "selector", "text", { placeholder: "CSS selector (optional)" }),
            bind(f, "attribute", "text", { placeholder: "attribute, e.g. href (optional)" }),
            bind(f, "regex", "text", { placeholder: "regex (optional)" }));
          return h("div", { class: "field-row" }, en,
            h("div", {}, h("div", { style: "font-weight:550" }, f.label || f.name), h("div", { class: "muted small" }, f.name)),
            bindSelect(f, "type", types.map((t) => [t, t])),
            h("label", { class: "check" }, req, "Required"),
            h("button", { class: "btn btn-ghost btn-sm", title: "Extraction rules", onclick: () => { f._adv = !f._adv; render(); } }, "⋯"),
            adv);
        }));
    }
    render();
    const custom = { name: "", type: "text", selector: "" };
    return h("div", {},
      h("p", { class: "muted" }, "Choose what to extract for each record. Built-in fields are found automatically (structured data, contact links, page text). Use “⋯” to add a CSS selector when a site needs it."),
      list,
      h("h3", {}, "Add custom field"),
      h("div", { class: "grid grid-4" },
        bind(custom, "name", "text", { placeholder: "e.g. linkedin_url" }),
        bindSelect(custom, "type", types.map((t) => [t, t])),
        bind(custom, "selector", "text", { placeholder: "CSS selector (optional)" }),
        h("button", { class: "btn", onclick: () => {
          const name = (custom.name || "").trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_|_$/g, "");
          if (!name) return toast("Enter a field name");
          if (draft.fields.some((f) => f.name === name)) return toast("That field already exists");
          draft.fields.push({ name, type: custom.type, selector: custom.selector || undefined, enabled: true, required: false, _adv: !!custom.selector });
          viewWizard();
        } }, "+ Add field")));
  }

  function stepSources() {
    const rows = h("div", {});
    const typeOpts = [["auto", "Detect automatically"], ["directory", "Directory / listing"], ["website", "Company website"],
      ["detail", "Single record page"], ["sitemap", "Sitemap"], ["feed", "RSS / Atom feed"], ["api", "JSON API"]];
    function render() {
      rows.replaceChildren(...draft.sources.map((s, i) => h("div", { class: "source-row" },
        bind(s, "url", "url", { placeholder: "https://…" }),
        bindSelect(s, "type", typeOpts),
        h("button", { class: "btn btn-ghost", "aria-label": "Remove", onclick: () => { draft.sources.splice(i, 1); render(); } }, "✕"))));
    }
    render();
    const paste = h("textarea", { placeholder: "Paste URLs, one per line" });
    const radios = [["auto", "Automatically find sources", "Searches the web for directories, lists and company websites that match your target."],
      ["manual", "Choose sources myself", "Only crawl the URLs below."],
      ["hybrid", "Both", "Crawl the URLs below and search for more."]].map(([v, l, d]) => {
      const r = h("input", { type: "radio", name: "smode", value: v, checked: draft.sourceMode === v });
      r.addEventListener("change", () => { draft.sourceMode = v; viewWizard(); });
      return h("label", { class: "check" }, r, h("span", {}, l, h("span", { class: "hint" }, d)));
    });
    const disc = draft.discovery;
    const providerNote = META.search_provider === "duckduckgo"
      ? "Search uses DuckDuckGo (no key needed). For large jobs set BRAVE_API_KEY or SEARXNG_URL before starting the app."
      : "Search provider: " + META.search_provider + ".";
    return h("div", {}, radios,
      draft.sourceMode !== "manual" ? h("div", { class: "card", style: "margin:12px 0" }, h("strong", {}, "Source discovery"),
        bindCheck(disc, "search_engines", "Search engines"),
        bindCheck(disc, "directories", "Business directories & “top companies” lists"),
        bindCheck(disc, "company_websites", "Company websites"),
        bindCheck(disc, "industry_directories", "Industry directories"),
        h("p", { class: "hint" }, providerNote)) : null,
      draft.sourceMode !== "auto" || draft.sources.length ? h("div", {},
        h("h3", {}, "Source URLs"), rows,
        h("div", { class: "btn-row" },
          h("button", { class: "btn btn-sm", onclick: () => { draft.sources.push({ url: "", type: "auto" }); render(); } }, "+ Add source")),
        h("details", {}, h("summary", {}, "Paste a list of URLs"), paste,
          h("button", { class: "btn btn-sm", style: "margin-top:6px", onclick: () => {
            paste.value.split(/\s+/).map((s) => s.trim()).filter(Boolean).forEach((u) => draft.sources.push({ url: u, type: "auto" }));
            paste.value = ""; render();
          } }, "Add URLs"))) : null);
  }

  function stepRecords() {
    return h("div", {},
      h("div", { class: "grid grid-2" },
        field("Minimum records", bind(draft, "min", "number", { min: 1 })),
        field("Maximum records", bind(draft, "max", "number", { min: 1 }))),
      bindCheck(draft, "stopAtMin", "Stop at minimum", "Stop as soon as the minimum number of valid, unique records is reached. Otherwise keep going toward the maximum."),
      field("Minimum quality score (0–100)", bind(draft, "minQuality", "number", { min: 0, max: 100 }),
        "Records scoring below this are rejected. Score = completeness 30% · validity 25% · target match 20% · source reliability 15% · uniqueness 10%."));
  }

  function stepOutput() {
    const fmts = META.output_formats.map((f) => [f, f.toUpperCase() + (f === "parquet" && !META.parquet ? " (needs pyarrow)" : "")]);
    const extra = h("div", { class: "btn-row" }, META.output_formats.map((f) => {
      const c = h("input", { type: "checkbox", checked: draft.extraFormats.includes(f) });
      c.addEventListener("change", () => {
        draft.extraFormats = draft.extraFormats.filter((x) => x !== f);
        if (c.checked) draft.extraFormats.push(f);
      });
      return h("label", { class: "check" }, c, f.toUpperCase());
    }));
    const inc = draft.include;
    const cr = draft.crawl;
    return h("div", {},
      h("div", { class: "grid grid-2" },
        field("Output format", bindSelect(draft, "format", fmts)),
        field("File name", bind(draft, "filename", "text", { placeholder: "cape-town-software-companies" }))),
      field("Also save as", extra),
      h("h3", {}, "Include"),
      bindCheck(inc, "source", "Source URLs"),
      bindCheck(inc, "timestamp", "Extraction timestamp"),
      bindCheck(inc, "quality", "Quality score"),
      bindCheck(inc, "provenance", "Provenance", "Where each value came from and how it was extracted"),
      bindCheck(inc, "raw", "Raw data", "Structured data found on the page (larger files)"),
      bindCheck(inc, "rejected", "Rejected records", "Add records that failed validation, with the reason"),
      h("details", {}, h("summary", {}, "Advanced crawl settings"),
        h("div", { class: "grid grid-3" },
          field("Delay per site (seconds)", bind(cr, "delay", "number", { min: 0, step: "0.1" }), "Politeness delay between requests to the same host"),
          field("Concurrency", bind(cr, "concurrency", "number", { min: 1, max: 32 }), "Parallel requests (one per site at a time)"),
          field("Max pages (0 = automatic)", bind(cr, "max_pages", "number", { min: 0 })),
          field("Max link depth", bind(cr, "max_depth", "number", { min: 0, max: 10 })),
          field("JavaScript rendering", bindSelect(cr, "render_js", [["auto", "Automatic"], ["never", "Never"], ["always", "Always"]]),
            META.browser_rendering ? "Playwright is installed" : "Install Playwright to render JavaScript pages"),
          field("Max runtime (minutes, 0 = none)", bind(cr, "max_runtime_minutes", "number", { min: 0 }))),
        bindCheck(cr, "respect_robots", "Respect robots.txt", "Recommended. Only turn off for sites you own or have permission to crawl.")));
  }

  function stepReview() {
    const box = h("div", {}, h("p", { class: "muted" }, "Checking…"));
    const job = draftToJob(draft);
    api("/api/jobs/validate", { method: "POST", body: job }).then((res) => {
      if (!res.ok) {
        box.replaceChildren(h("div", { class: "errors" }, h("strong", {}, "Please fix the following:"),
          h("ul", {}, res.errors.map((e) => h("li", {}, e)))));
        return;
      }
      const s = res.summary;
      const start = h("button", { class: "btn btn-primary", onclick: async () => {
        start.disabled = true;
        try {
          const created = await api("/api/jobs", { method: "POST", body: job });
          draft = null;
          location.hash = "#/jobs/" + created.id;
        } catch (err) {
          start.disabled = false;
          toast(err.data && err.data.errors ? err.data.errors.join("; ") : err.message, 6000);
        }
      } }, "Start scraping");
      const dl = h("button", { class: "btn", onclick: () => {
        const blob = new Blob([JSON.stringify(job, null, 2)], { type: "application/json" });
        const a = h("a", { href: URL.createObjectURL(blob), download: (job.output.filename || "job") + ".json" });
        document.body.append(a); a.click(); a.remove();
      } }, "Download job file");
      fill(box, h("div", { class: "review" }, h("dl", {},
        h("dt", {}, "Target"), h("dd", {}, s.entity + " — " + (s.target || "(any)")),
        h("dt", {}, "Location"), h("dd", {}, s.location || "(any)"),
        h("dt", {}, "Fields"), h("dd", {}, s.fields.length + " fields: " + s.fields.join(", ")),
        h("dt", {}, "Sources"), h("dd", {}, s.sources),
        h("dt", {}, "Records"), h("dd", {}, s.records),
        h("dt", {}, "Output"), h("dd", {}, s.output),
        h("dt", {}, "Mode"), h("dd", {}, res.mode === "site" ? "One record per website" : "One record per page"),
        h("dt", {}, "Page budget"), h("dd", {}, "up to " + fmtNum(res.page_budget) + " pages"),
        res.queries && res.queries.length && job.sources.mode !== "manual" ? [h("dt", {}, "Search queries"),
          h("dd", {}, h("ul", { style: "margin:0;padding-left:18px" }, res.queries.map((q) => h("li", {}, q))))] : null)),
        !job.crawl.respect_robots ? h("div", { class: "notice" }, "robots.txt will be ignored. Only do this for sites you own or have permission to crawl.") : null,
        h("details", {}, h("summary", {}, "Job JSON"), h("pre", {}, JSON.stringify(job, null, 2))),
        h("div", { class: "btn-row", style: "margin-top:14px" }, start, dl));
    }).catch((err) => box.replaceChildren(h("div", { class: "errors" }, err.message)));
    return box;
  }

  // ------------------------------------------------------------------------------------------
  // job dashboard + results
  // ------------------------------------------------------------------------------------------
  let jobView = null;

  async function viewJob(id) {
    jobView = { id, tab: null, autoTab: true, events: [], lastEvent: 0, records: null, filter: "valid", search: "",
      sortKey: null, sortDir: -1, minQ: 0, hidden: new Set(), status: null };
    await refreshJob();
  }

  async function refreshJob() {
    const v = jobView;
    if (!v) return;
    let st;
    try { st = await api("/api/jobs/" + v.id); } catch (err) { return showFatal(err); }
    if (jobView !== v) return;
    v.status = st;
    try {
      const ev = await api("/api/jobs/" + v.id + "/events?after=" + v.lastEvent);
      if (ev.events.length) { v.events = v.events.concat(ev.events).slice(-500); v.lastEvent = ev.events[ev.events.length - 1].id; }
    } catch (e) { /* ignore */ }
    const final = FINAL.includes(st.state) && !st.running;
    if (v.autoTab) v.tab = final ? "results" : "activity";  // follow the job until the user picks a tab
    if (final && v.records === null) {
      try { v.records = (await api("/api/jobs/" + v.id + "/records")).records; } catch (e) { v.records = []; }
    }
    renderJob();
    if (!final) pollTimer = setTimeout(refreshJob, 1000);
  }

  function renderJob() {
    const v = jobView, st = v.status, m = st.metrics || {};
    const running = st.running;
    const actions = h("div", { class: "btn-row" });
    if (running) {
      actions.append(st.paused
        ? h("button", { class: "btn", onclick: () => control("resume") }, "Resume")
        : h("button", { class: "btn", onclick: () => control("pause") }, "Pause"),
        h("button", { class: "btn btn-danger", onclick: () => { if (confirm("Stop this job? Records found so far will be exported.")) control("cancel"); } }, "Stop"));
    } else {
      if (st.state === "CREATED" || st.state === "INTERRUPTED") actions.append(h("button", { class: "btn btn-primary", onclick: () => control("start") }, st.state === "CREATED" ? "Start" : "Run again"));
      actions.append(h("button", { class: "btn", onclick: async () => {
        const res = await api("/api/jobs/" + v.id + "/clone", { method: "POST", body: {} });
        pendingPrefill = res.config;
        location.hash = "#/new";
      } }, "Edit & re-run"));
      if (META.platform === "darwin" && (st.outputs || []).length) actions.append(h("button", { class: "btn", onclick: () => api("/api/jobs/" + v.id + "/reveal", { method: "POST", body: {} }) }, "Show in Finder"));
      actions.append(h("a", { class: "btn", href: "/api/jobs/" + v.id + "/config" }, "Job file"),
        h("button", { class: "btn btn-danger", onclick: async () => {
          if (!confirm("Delete this job and its stored records? Exported files are kept.")) return;
          await api("/api/jobs/" + v.id, { method: "DELETE" });
          location.hash = "#/";
        } }, "Delete"));
    }
    const max = m.target_maximum || 1, min = m.target_minimum || 0;
    const valid = m.final_unique || m.records_valid || 0;
    const pct = Math.min(100, (100 * valid) / max);
    const progress = h("div", { class: "progress", title: "Minimum " + min + ", maximum " + max },
      h("div", { class: "bar", style: "width:" + pct.toFixed(1) + "%" }),
      h("div", { class: "min", style: "left:" + Math.min(100, (100 * min) / max).toFixed(1) + "%" }));
    const tiles = h("div", { class: "tiles" },
      tile("Records", fmtNum(valid) + " / " + fmtNum(max)),
      tile("Valid", fmtNum(m.records_valid)),
      tile("Duplicates", fmtNum(m.duplicates_removed)),
      tile("Rejected", fmtNum(m.records_rejected)),
      tile("Sources", fmtNum(m.sources_discovered)),
      tile("Pages crawled", fmtNum(m.pages_fetched)),
      tile("Success rate", (m.success_rate || 0) + "%"),
      tile("Average quality", m.average_quality ? Math.round(m.average_quality) + "%" : "–"),
      tile("Elapsed", fmtTime(m.elapsed)),
      tile("Current source", m.current_source || "–", true));
    const pipeline = h("div", { class: "pipeline" }, [
      ["Search queries", m.queries_run], ["Sources discovered", m.sources_discovered], ["URLs discovered", m.urls_discovered],
      ["Pages scraped", m.pages_fetched], ["Records extracted", m.records_extracted], ["Records validated", m.records_valid],
      ["Duplicates removed", m.duplicates_removed], ["Final unique records", m.final_unique || (running ? "…" : 0)]
    ].map(([k, val]) => [h("span", {}, k), h("span", {}, fmtNum(val))]));
    const sum = st.summary || {};
    const head = h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, st.name), h("div", { class: "muted" }, stateBadge(st.state, running), "  ",
        (sum.entity || "") + (sum.location ? " · " + sum.location : "") + " · " + (sum.records || "") + " records · " + st.id)),
      actions);
    const tabs = h("div", { class: "tabs", role: "tablist" }, [["results", "Results"], ["activity", "Activity"], ["sources", "Sources"], ["overview", "Pipeline"]]
      .map(([k, l]) => h("button", { role: "tab", class: v.tab === k ? "active" : "", onclick: () => { v.tab = k; v.autoTab = false; renderJob(); } }, l)));
    let content;
    if (v.tab === "results") content = renderResults();
    else if (v.tab === "sources") { content = h("div", {}, h("p", { class: "muted" }, "Loading…")); loadSources(content); }
    else if (v.tab === "overview") content = h("div", { class: "card" }, pipeline,
      st.error ? h("div", { class: "errors", style: "margin-top:12px" }, st.error) : null,
      (m.warnings || []).length ? h("div", {}, h("h3", {}, "Warnings"), h("ul", {}, m.warnings.map((w) => h("li", {}, w)))) : null);
    else content = h("div", { class: "log" }, v.events.length ? v.events.map((e) =>
      h("div", { class: e.level }, new Date(e.ts).toLocaleTimeString() + "  " + e.message)) : h("div", { class: "muted" }, "Waiting for activity…"));
    const outputs = (st.outputs || []).length ? h("div", { class: "muted small" }, "Saved: ", st.outputs.join(" · ")) : null;
    fill(app, head, h("div", { class: "card" },
      h("div", { class: "btn-row", style: "justify-content:space-between" }, h("strong", {}, m.phase || st.state), h("span", { class: "muted" }, fmtNum(valid) + " of " + fmtNum(min) + "–" + fmtNum(max) + " records")),
      progress, outputs), tiles, tabs, content);
    if (v.tab === "activity") { const log = app.querySelector(".log"); if (log) log.scrollTop = log.scrollHeight; }
  }

  function tile(k, val, small) {
    return h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" + (small ? " sm" : ""), title: String(val) }, val));
  }

  async function control(action) {
    try {
      await api("/api/jobs/" + jobView.id + "/" + action, { method: "POST", body: {} });
      toast(action === "cancel" ? "Stopping… exporting records found so far" : action.charAt(0).toUpperCase() + action.slice(1) + "d");
      stopPolling();
      jobView.records = null;
      setTimeout(refreshJob, 300);
    } catch (err) { toast(err.message); }
  }

  async function loadSources(box) {
    try {
      const { sources } = await api("/api/jobs/" + jobView.id + "/sources");
      if (!sources.length) { box.replaceChildren(h("p", { class: "muted" }, "Source details are available when the job finishes.")); return; }
      box.replaceChildren(h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Source"), h("th", {}, "Type"), h("th", {}, "Found by"), h("th", { class: "num" }, "Score"),
          h("th", { class: "num" }, "Pages"), h("th", { class: "num" }, "Records"), h("th", {}, "Status"))),
        h("tbody", {}, sources.map((s) => h("tr", {}, h("td", { class: "cell" }, linkOrText(s.url)), h("td", {}, s.type),
          h("td", {}, s.origin === "user" ? "you" : "discovery"), h("td", { class: "num" }, s.score ? Number(s.score).toFixed(2) : "–"),
          h("td", { class: "num" }, fmtNum(s.pages)), h("td", { class: "num" }, fmtNum(s.records)), h("td", {}, s.status)))))));
    } catch (err) { box.replaceChildren(h("div", { class: "errors" }, err.message)); }
  }

  function renderResults() {
    const v = jobView, st = v.status;
    if (st.running) return h("div", { class: "card empty" }, h("p", {}, "Results appear here when the job finishes. Watch progress in the Activity tab."));
    if (v.records === null) return h("p", { class: "muted" }, "Loading…");
    const fields = (st.fields || []).map((f) => f.name);
    const labels = Object.fromEntries((st.fields || []).map((f) => [f.name, f.label || f.name]));
    const types = Object.fromEntries((st.fields || []).map((f) => [f.name, f.type]));
    const cols = fields.filter((f) => !v.hidden.has(f));
    function filtered() {
      let rows = v.records.filter((r) => v.filter === "all" || (v.filter === "valid" ? r.valid : !r.valid));
      if (v.minQ) rows = rows.filter((r) => (r.quality || 0) >= v.minQ);
      if (v.search) {
        const q = v.search.toLowerCase();
        rows = rows.filter((r) => Object.values(r.data).some((x) => cellText(x).toLowerCase().includes(q)));
      }
      if (v.sortKey) {
        const k = v.sortKey;
        rows = rows.slice().sort((a, b) => {
          const x = k === "_quality" ? a.quality : a.data[k], y = k === "_quality" ? b.quality : b.data[k];
          if (x === y) return 0;
          if (x === undefined || x === null || x === "") return 1;
          if (y === undefined || y === null || y === "") return -1;
          return (typeof x === "number" && typeof y === "number" ? x - y : cellText(x).localeCompare(cellText(y))) * v.sortDir;
        });
      }
      return rows;
    }
    const search = h("input", { type: "text", placeholder: "Search records…", value: v.search });
    search.addEventListener("input", () => { v.search = search.value; rerenderTable(); });
    const filter = h("select", {}, [["valid", "Valid records"], ["rejected", "Rejected"], ["all", "All"]].map(([k, l]) => h("option", { value: k }, l)));
    filter.value = v.filter;
    filter.addEventListener("change", () => { v.filter = filter.value; renderJob(); });
    const minQ = h("input", { type: "number", min: 0, max: 100, value: v.minQ || "", placeholder: "min quality", style: "width:120px" });
    minQ.addEventListener("change", () => { v.minQ = Number(minQ.value) || 0; renderJob(); });
    const colMenu = h("div", { class: "menu hidden" }, fields.map((f) => {
      const c = h("input", { type: "checkbox", checked: !v.hidden.has(f) });
      c.addEventListener("change", () => { if (c.checked) v.hidden.delete(f); else v.hidden.add(f); renderJob(); });
      return h("label", { class: "check" }, c, labels[f]);
    }));
    const colBtn = h("div", { class: "columns-menu" }, h("button", { class: "btn", onclick: () => colMenu.classList.toggle("hidden") }, "Columns"), colMenu);
    const exports = h("div", { class: "btn-row" }, h("span", { class: "muted" }, "Export:"), META.output_formats.map((f) =>
      h("a", { class: "btn btn-sm", href: "/api/jobs/" + v.id + "/export?format=" + f + (v.filter !== "valid" ? "&include_rejected=1" : "") }, f.toUpperCase())));
    function sortHead(k, label, cls) {
      const arrow = v.sortKey === k ? (v.sortDir > 0 ? " ▲" : " ▼") : "";
      return h("th", { class: "sortable " + (cls || ""), onclick: () => { if (v.sortKey === k) v.sortDir *= -1; else { v.sortKey = k; v.sortDir = k === "_quality" ? -1 : 1; } renderJob(); } }, label + arrow);
    }
    const tableBox = h("div", { class: "table-wrap results-table" });
    function rerenderTable() {
      const shown = filtered();
      const body = shown.slice(0, 2000).map((r) => h("tr", { class: "clickable", onclick: () => openRecord(r.record_id, fields, labels, types) },
        cols.map((f) => h("td", { class: "cell", title: cellText(r.data[f]) }, types[f] === "url" || isUrl(r.data[f]) ? linkOrText(r.data[f]) : cellText(r.data[f]))),
        h("td", { class: "num" }, h("span", { class: qClass(r.quality) }, Math.round(r.quality || 0))),
        v.filter !== "valid" ? h("td", { class: "cell muted", title: (r.reasons || []).join("; ") }, r.valid ? "valid" : (r.reasons || []).join("; ")) : null));
      tableBox.replaceChildren(h("table", {}, h("thead", {}, h("tr", {}, cols.map((f) => sortHead(f, labels[f])), sortHead("_quality", "Quality", "num"),
        v.filter !== "valid" ? h("th", {}, "Status") : null)), h("tbody", {}, body.length ? body :
        h("tr", {}, h("td", { colspan: cols.length + 2, class: "muted" }, v.records.length ? "No records match the filters." : "No records were collected.")))));
      count.textContent = fmtNum(shown.length) + " record" + (shown.length === 1 ? "" : "s") + (shown.length > 2000 ? " (showing 2,000)" : "");
    }
    const count = h("span", { class: "muted" });
    rerenderTable();
    return h("div", {}, h("div", { class: "toolbar" }, search, filter, minQ, colBtn, count, h("span", { style: "flex:1" }), exports), tableBox);
  }

  async function openRecord(rid, fields, labels, types) {
    const drawer = document.getElementById("drawer");
    const body = document.getElementById("drawer-body");
    body.replaceChildren(h("p", { class: "muted" }, "Loading…"));
    drawer.hidden = false;
    let r;
    try { r = await api("/api/jobs/" + jobView.id + "/records/" + rid); } catch (err) { body.replaceChildren(h("div", { class: "errors" }, err.message)); return; }
    const nameField = fields.find((f) => /name|title/.test(f)) || fields[0];
    document.getElementById("drawer-title").textContent = cellText(r.data[nameField]) || "Record";
    const comps = r.quality_components || {};
    const compLabels = { completeness: "Completeness", validity: "Field validity", target_match: "Target match", source_reliability: "Source reliability", uniqueness: "Uniqueness" };
    const provRows = [];
    for (const [f, p] of Object.entries(r.provenance || {})) {
      for (const x of [].concat(p)) provRows.push(h("tr", {}, h("td", {}, labels[f] || f), h("td", { class: "cell", title: cellText(x.value) }, cellText(x.value)),
        h("td", {}, x.method || ""), h("td", { class: "num" }, x.confidence !== undefined && x.confidence !== null ? Number(x.confidence).toFixed(2) : ""),
        h("td", { class: "cell" }, x.source ? linkOrText(x.source) : "")));
    }
    fill(body,
      h("div", {}, h("span", { class: qClass(r.quality), style: "font-size:20px" }, Math.round(r.quality || 0) + " / 100"), "  ",
        r.valid ? h("span", { class: "badge ok" }, "valid") : h("span", { class: "badge bad" }, "rejected")),
      !r.valid && r.reasons && r.reasons.length ? h("div", { class: "errors", style: "margin-top:10px" }, r.reasons.join("; ")) : null,
      h("h3", {}, "Extracted fields"),
      h("dl", { class: "kv" }, fields.map((f) => [h("dt", {}, labels[f]), h("dd", {},
        Array.isArray(r.data[f]) ? h("div", {}, r.data[f].map((x) => h("div", {}, linkOrText(x)))) : (r.data[f] === undefined || r.data[f] === null || r.data[f] === "" ? h("span", { class: "muted" }, "—") : linkOrText(r.data[f])))])),
      h("h3", {}, "Quality score"),
      Object.entries(compLabels).map(([k, l]) => h("div", { class: "meter" }, h("span", {}, l),
        h("div", { class: "track" }, h("div", { class: "fill", style: "width:" + (comps[k] || 0) + "%" })), h("span", { class: "num" }, (comps[k] || 0) + "%"))),
      h("h3", {}, "Validation"),
      h("ul", { class: "vlist" }, (r.validation || []).map((c) => h("li", { class: c.passed ? "pass" : "fail" }, (labels[c.field] || c.field) + " — " + (c.message || c.category)))),
      h("h3", {}, "Sources"),
      h("ul", {}, (r.sources || []).map((s) => h("li", {}, linkOrText(s)))),
      h("h3", {}, "Provenance"),
      provRows.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Field"), h("th", {}, "Value"), h("th", {}, "Method"), h("th", { class: "num" }, "Conf."), h("th", {}, "Source"))), h("tbody", {}, provRows))) : h("p", { class: "muted" }, "No provenance stored."),
      r.raw && r.raw.length ? [h("h3", {}, "Raw extraction data"), h("pre", {}, JSON.stringify(r.raw, null, 2))] : null);
  }

  function closeDrawer() { document.getElementById("drawer").hidden = true; }
  document.getElementById("drawer").addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  route().catch(showFatal);
})();
