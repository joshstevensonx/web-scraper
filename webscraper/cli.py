"""Command-line interface.

    webscraper ui                     open the web app in your browser
    webscraper new                    interactive job wizard (creates a job JSON file)
    webscraper run job.json           run a job with a live terminal dashboard
    webscraper jobs                   list previous jobs
    webscraper show <job-id>          show a job's results
    webscraper export <job-id> -f csv export a finished job again in another format
    webscraper validate job.json      check a job file without running it
    webscraper example [entity]       print an example job file
    webscraper doctor                 check that everything is installed correctly
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from . import config as app_config
from .job.models import JobConfigError, OUTPUT_FORMATS, load_job_file, parse_job
from .target.entities import ENTITIES, get_entity


def _print_err(msg: str) -> None:
    print(msg, file=sys.stderr)


# ------------------------------------------------------------------------------------------------
# Examples
# ------------------------------------------------------------------------------------------------

def example_job(entity: str = "business") -> Dict[str, Any]:
    e = get_entity(entity)
    if e.record_mode == "site":
        return {
            "name": "Cape Town software companies",
            "entity": e.key,
            "targeting": {
                "description": "Software companies in Cape Town",
                "required": {"location": {"country": "South Africa", "province": "Western Cape", "city": "Cape Town"},
                             "industry": ["Software"]},
                "preferred": {"keywords": ["SaaS", "cloud"]},
                "excluded": {"business_types": ["Government", "Non-profit"]},
            },
            "fields": e.default_fields,
            "sources": {"mode": "auto", "urls": []},
            "records": {"minimum": 50, "maximum": 100, "stop_at_minimum": True},
            "output": {"format": "xlsx", "filename": "cape-town-software-companies", "include_source": True,
                       "include_timestamp": True, "include_quality_score": True, "include_provenance": True},
        }
    return {
        "name": "Books to scrape",
        "entity": e.key,
        "sources": {"mode": "manual", "urls": [{"url": "https://books.toscrape.com/", "type": "listing"}]},
        "fields": e.default_fields,
        "records": {"minimum": 20, "maximum": 50},
        "output": {"format": "csv", "filename": "books"},
    }


# ------------------------------------------------------------------------------------------------
# Interactive wizard (terminal version of the UI wizard, section 28)
# ------------------------------------------------------------------------------------------------

def _ask(prompt: str, default: str = "") -> str:
    suffix = " [%s]" % default if default else ""
    try:
        value = input("%s%s: " % (prompt, suffix)).strip()
    except EOFError:
        value = ""
    return value or default


def _ask_bool(prompt: str, default: bool = True) -> bool:
    v = _ask(prompt + (" (Y/n)" if default else " (y/N)"), "").lower()
    return default if not v else v.startswith("y")


def _ask_int(prompt: str, default: int) -> int:
    while True:
        v = _ask(prompt, str(default)).replace(",", "")
        if v.isdigit() and int(v) > 0:
            return int(v)
        print("  Please enter a positive whole number.")


def _split(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def wizard() -> Dict[str, Any]:
    print("\n\033[1mNew scraping job\033[0m — press Enter to accept the [default].\n")
    print("Step 1 — Target")
    print("  Entity types: " + ", ".join(sorted(k for k in ENTITIES)))
    entity = get_entity(_ask("  Entity type", "business"))
    description = _ask("  Describe the target (e.g. 'Software companies in Cape Town')")

    print("\nStep 2 — Targeting")
    country = _ask("  Country")
    province = _ask("  Province / state")
    city = _ask("  City")
    industry = _split(_ask("  Industry (comma separated)"))
    keywords = _split(_ask("  Preferred keywords (comma separated)"))
    exclude = _split(_ask("  Exclude records mentioning (comma separated)"))
    required: Dict[str, Any] = {}
    location = {k: v for k, v in (("country", country), ("province", province), ("city", city)) if v}
    if location:
        required["location"] = location
    if industry:
        required["industry"] = industry

    print("\nStep 3 — Fields")
    defaults = [f["name"] for f in entity.default_fields]
    print("  Default fields: " + ", ".join(defaults))
    names = _split(_ask("  Fields to extract", ", ".join(defaults)))
    custom = _split(_ask("  Extra custom fields (comma separated, optional)"))
    fields: List[Dict[str, Any]] = []
    for n in names + custom:
        base = next((dict(f) for f in entity.default_fields if f["name"] == n), {"name": n})
        fields.append(base)

    print("\nStep 4 — Sources")
    urls = _split(_ask("  Source URLs (comma separated; leave empty to find sources automatically)"))
    mode = "manual" if urls else "auto"
    if urls and _ask_bool("  Also discover more sources automatically?", False):
        mode = "hybrid"

    print("\nStep 5 — Record count")
    minimum = _ask_int("  Minimum records", 50)
    maximum = _ask_int("  Maximum records", max(minimum, 100))
    while maximum < minimum:
        print("  Maximum must be at least the minimum.")
        maximum = _ask_int("  Maximum records", minimum)
    stop = _ask_bool("  Stop as soon as the minimum is reached?", True)

    print("\nStep 6 — Output")
    fmt = _ask("  Output format (%s)" % ", ".join(OUTPUT_FORMATS), "xlsx").lower()
    filename = _ask("  File name", "")
    provenance = _ask_bool("  Include provenance (where each value came from)?", True)

    job: Dict[str, Any] = {
        "entity": entity.key,
        "targeting": {"description": description, "required": required,
                      "preferred": {"keywords": keywords} if keywords else {},
                      "excluded": {"terms": exclude} if exclude else {}},
        "fields": fields,
        "sources": {"mode": mode, "urls": urls},
        "records": {"minimum": minimum, "maximum": maximum, "stop_at_minimum": stop},
        "output": {"format": fmt, "include_provenance": provenance},
    }
    if description:
        job["name"] = description
    if filename:
        job["output"]["filename"] = filename
    return job


# ------------------------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    from .job.runner import JobControl, JobRunner
    from .monitoring.console import ConsoleDashboard
    from .storage.database import Database

    try:
        with open(os.path.expanduser(args.job), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        _print_err("Could not read %s: %s" % (args.job, exc))
        return 2
    if args.format:
        raw.setdefault("output", {})
        if isinstance(raw["output"], str):
            raw["output"] = {"format": raw["output"]}
        raw["output"]["format"] = [f.strip() for f in args.format.split(",") if f.strip()]
    if args.minimum or args.maximum:
        raw.setdefault("records", {})
        if args.minimum:
            raw["records"]["minimum"] = args.minimum
        if args.maximum:
            raw["records"]["maximum"] = args.maximum
    if args.max_pages:
        raw.setdefault("crawl", {})["max_pages"] = args.max_pages
    try:
        job = parse_job(raw, base_dir=os.path.dirname(os.path.abspath(args.job)))
    except JobConfigError as exc:
        _print_err(str(exc))
        return 2

    db = None if args.no_db else Database()
    dash = ConsoleDashboard(job.name, quiet=args.quiet)
    control = JobControl()
    runner = JobRunner(job, db=db, control=control, on_event=dash.event, on_progress=dash.progress,
                       output_dir=args.output_dir, raw_config=raw)

    def on_sigint(signum, frame):  # first Ctrl+C: stop gracefully and export what we have
        if control.cancelled:
            raise KeyboardInterrupt
        control.cancel()
        dash.event("warning", "Stopping… (press Ctrl+C again to abort immediately)")

    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        outcome = runner.run_sync()
    finally:
        signal.signal(signal.SIGINT, previous)
        dash.finish()

    m = outcome.metrics
    print()
    print("\033[1m%s\033[0m — %s" % (job.name, outcome.state.label))
    print("  Records: %d valid (requested %d–%d), %d duplicates merged, %d rejected" %
          (sum(1 for r in outcome.records if r["valid"]), job.records.minimum, job.records.maximum,
           m.duplicates_removed, m.records_rejected))
    print("  Pages:   %d fetched (%.1f%% ok) from %d source(s) in %ds" %
          (m.pages_fetched, m.success_rate, m.sources_discovered, m.elapsed))
    if outcome.outputs:
        print("  Output:")
        for p in outcome.outputs:
            print("    " + p)
    if db is not None:
        print("  Job ID:  %s   (webscraper show %s)" % (job.id, job.id))
    if outcome.error and outcome.error != "cancelled":
        _print_err("Error: " + outcome.error)
        return 1
    if args.open and outcome.outputs and sys.platform == "darwin":
        import subprocess

        subprocess.run(["open", outcome.outputs[0]], check=False)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    raw = wizard()
    try:
        job = parse_job(raw)
    except JobConfigError as exc:
        _print_err(str(exc))
        return 2
    path = args.output or os.path.join(app_config.jobs_dir(), "%s.json" % job.output.filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2)
    print("\nSaved job to %s" % path)
    print("Review:")
    for k, v in job.summary().items():
        if k != "id":
            print("  %-8s %s" % (k.title(), v if not isinstance(v, list) else ", ".join(v)))
    if args.run or _ask_bool("\nStart scraping now?", True):
        ns = argparse.Namespace(job=path, format=None, minimum=None, maximum=None, max_pages=None, no_db=False,
                                quiet=False, output_dir=None, open=False)
        return cmd_run(ns)
    print("Run it later with:  webscraper run %s" % json.dumps(path))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        job = load_job_file(args.job)
    except JobConfigError as exc:
        _print_err(str(exc))
        return 2
    except OSError as exc:
        _print_err("Could not read %s: %s" % (args.job, exc))
        return 2
    print("✓ %s is valid" % args.job)
    for k, v in job.summary().items():
        print("  %-8s %s" % (k.title(), v if not isinstance(v, list) else ", ".join(v)))
    print("  Mode     %s (one record per %s)" % (job.record_mode, "website" if job.record_mode == "site" else "page"))
    print("  Budget   up to %d pages" % job.page_budget)
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    from .storage.database import Database

    db = Database()
    jobs = db.list_jobs(args.limit)
    if not jobs:
        print("No jobs yet. Create one with  webscraper new  or  webscraper ui")
        return 0
    print("%-13s %-20s %-8s %-9s %s" % ("ID", "STATE", "RECORDS", "PAGES", "NAME"))
    for j in jobs:
        m = j.get("metrics") or {}
        print("%-13s %-20s %-8s %-9s %s" % (j["id"], j["state"], m.get("final_unique", m.get("records_valid", "")),
                                             m.get("pages_fetched", ""), j["name"][:60]))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from .job.manager import JobManager

    mgr = JobManager()
    st = mgr.status(args.job_id)
    if st is None:
        _print_err("No job with id %s" % args.job_id)
        return 2
    print("\033[1m%s\033[0m (%s) — %s" % (st["name"], st["id"], st["state"]))
    for k, v in (st.get("summary") or {}).items():
        if k != "id":
            print("  %-8s %s" % (k.title(), v if not isinstance(v, list) else ", ".join(v)))
    for p in st.get("outputs") or []:
        print("  Output   %s" % p)
    records = mgr.records(args.job_id, include_rejected=False)
    fields = [f["name"] for f in st.get("fields", [])][:6]
    print("\n%d valid record(s). First %d:" % (len(records), min(args.records, len(records))))
    for r in records[: args.records]:
        vals = [str(r["data"].get(f, "") if not isinstance(r["data"].get(f), list) else
                    "; ".join(map(str, r["data"].get(f))))[:40] for f in fields]
        print("  %5.1f  %s" % (r["quality"] or 0, " | ".join(vals)))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .job.manager import JobManager

    mgr = JobManager()
    try:
        path = mgr.export(args.job_id, args.format, args.output_dir, args.include_rejected)
    except KeyError:
        _print_err("No job with id %s" % args.job_id)
        return 2
    print(path)
    return 0


def cmd_example(args: argparse.Namespace) -> int:
    print(json.dumps(example_job(args.entity), indent=2))
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .web.server import serve

    serve(args.host, args.port, open_browser=not args.no_browser)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import platform

    ok = True
    print("Web Scraper %s" % __version__)
    print("  Python        %s (%s)" % (platform.python_version(), sys.executable))
    if sys.version_info < (3, 9):
        print("  ✗ Python 3.9 or newer is required")
        ok = False
    for mod, why in (("httpx", "HTTP fetching"), ("bs4", "HTML parsing"), ("lxml", "fast parsing / XPath"),
                     ("openpyxl", "XLSX output"), ("phonenumbers", "phone normalization")):
        try:
            __import__(mod)
            print("  ✓ %-13s %s" % (mod, why))
        except ImportError:
            print("  ✗ %-13s missing (%s) — run ./install.sh" % (mod, why))
            ok = False
    from .util import module_available

    for mod, why, hint in (("pyarrow", "Parquet output", "pip install pyarrow"),
                           ("playwright", "JavaScript rendering",
                            "pip install playwright && python -m playwright install chromium")):
        if module_available(mod):
            print("  ✓ %-13s %s" % (mod, why))
        else:
            print("  - %-13s optional, for %s (%s)" % (mod, why, hint))
    if os.environ.get("BRAVE_API_KEY"):
        provider = "Brave Search API"
    elif os.environ.get("SEARXNG_URL"):
        provider = "SearXNG (%s)" % os.environ["SEARXNG_URL"]
    else:
        provider = "DuckDuckGo (no key; set BRAVE_API_KEY for heavier use)"
    print("  Search        %s" % provider)
    print("  Data folder   %s" % app_config.home_dir())
    if args.network:
        import asyncio
        from .crawling.fetcher import Fetcher

        async def probe() -> None:
            f = Fetcher(retries=0, timeout=10)
            try:
                res = await f.fetch("https://books.toscrape.com/")
                print("  %s Network       %s" % ("✓" if res.ok else "✗", "OK" if res.ok else res.error or res.status))
            finally:
                await f.close()

        asyncio.run(probe())
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="webscraper", description="Target-driven web scraping for macOS.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="run a job file")
    r.add_argument("job", help="path to a job JSON file")
    r.add_argument("-f", "--format", help="override output format(s), e.g. csv,xlsx")
    r.add_argument("-o", "--output-dir", help="where to save results (default ~/WebScraper/output)")
    r.add_argument("--min", dest="minimum", type=int, help="override minimum records")
    r.add_argument("--max", dest="maximum", type=int, help="override maximum records")
    r.add_argument("--max-pages", type=int, help="override the page budget")
    r.add_argument("--no-db", action="store_true", help="do not record the job in the local database")
    r.add_argument("-q", "--quiet", action="store_true", help="only print warnings and the summary")
    r.add_argument("--open", action="store_true", help="open the first output file when done (macOS)")
    r.set_defaults(func=cmd_run)

    n = sub.add_parser("new", help="create a job with an interactive wizard")
    n.add_argument("-o", "--output", help="where to save the job file")
    n.add_argument("--run", action="store_true", help="start the job immediately")
    n.set_defaults(func=cmd_new)

    u = sub.add_parser("ui", help="open the web interface")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    u.set_defaults(func=cmd_ui)

    j = sub.add_parser("jobs", help="list jobs")
    j.add_argument("--limit", type=int, default=30)
    j.set_defaults(func=cmd_jobs)

    s = sub.add_parser("show", help="show a job and its records")
    s.add_argument("job_id")
    s.add_argument("--records", type=int, default=15)
    s.set_defaults(func=cmd_show)

    e = sub.add_parser("export", help="export a job's records again")
    e.add_argument("job_id")
    e.add_argument("-f", "--format", default="csv", choices=[f for f in OUTPUT_FORMATS if f != "ndjson"])
    e.add_argument("-o", "--output-dir")
    e.add_argument("--include-rejected", action="store_true")
    e.set_defaults(func=cmd_export)

    v = sub.add_parser("validate", help="validate a job file")
    v.add_argument("job")
    v.set_defaults(func=cmd_validate)

    x = sub.add_parser("example", help="print an example job file")
    x.add_argument("entity", nargs="?", default="business")
    x.set_defaults(func=cmd_example)

    d = sub.add_parser("doctor", help="check the installation")
    d.add_argument("--network", action="store_true", help="also test internet access")
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _print_err("\nAborted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
