# India Policy & Regulatory Intelligence

A zero-cost, local-first Python pipeline that discovers recent Indian policy and regulatory material, filters routine noise, records the underlying event in SQLite, suppresses unchanged repeats, maintains an open watchlist, and writes a source-linked daily Markdown brief.

It also ships an editorial web interface, a static JSON publication layer, GitHub Actions automation at 08:00 IST, and Vercel hosting. The design rationale and the full issue audit are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

The system is deliberately conservative. An index-page link without a verifiable publication date is not presented as current. A source failure is recorded and the remaining registry continues. Secondary discovery can surface candidates, but reports label entries where a primary source could not be confirmed.

## Quick start

Python 3.9 or later is required. Install the small optional dependency set before a live run; it supplies robust public-certificate handling and PDF text extraction.

```bash
python3 -m pip install -r requirements.txt
python3 -m app.run
python3 scripts/export_web.py
cd web && python3 -m http.server 8000 --bind 127.0.0.1
```

Useful options:

```bash
python3 -m app.run --verbose
python3 -m app.run --lookback-days 3 --max-items 8
python3 -m app.run --offline
```

The live command initializes `data/intelligence.db`, fetches enabled sources, creates or updates structured events, reviews the watchlist, and writes `reports/daily/YYYY-MM-DD.md`. `--offline` performs no HTTP requests and generates a transparent report from the existing watchlist/history. If `pypdf` is already available, the pipeline also extracts linked official PDFs; it remains fully functional and falls back to official page metadata when that optional package is absent.

Open `http://127.0.0.1:8000` after starting the local website. The client checks `web/data/latest.json` every minute, exposes evidence status and source health, and never invents a live update when the dataset cannot be reached.

## Project structure

```text
app/
  collector.py    resilient feed/page and search-feed collection
  classifier.py   topic/status/date/entity extraction and editorial filtering
  database.py     SQLite schema and historical state
  parsing.py      dependency-free RSS/Atom/HTML parsing
  pipeline.py     collection-to-report orchestration
  reporting.py    exact daily Markdown structure and source links
  run.py          command-line entry point
config/
  sources.yaml    independently editable source registry
  topics.yaml     topic synonyms, search terms and implication language
data/
  intelligence.db
reports/daily/
tests/
scripts/export_web.py  SQLite-to-JSON publication export
web/                   responsive static intelligence website
.github/workflows/     daily 08:00 IST refresh and pull-request CI
```

The `.yaml` files use JSON syntax, which is valid YAML. This keeps configuration portable without requiring PyYAML.

## Persistence and change detection

SQLite stores events, event-source provenance, source health, run history, and report history. Events are fingerprinted using authoritative identifiers or canonical URLs together with status and critical dates. Exact fingerprints are touched but not reported again. A matching identifier or highly similar historical title with a changed fingerprint becomes an update linked through `previous_event_id`. Drafts, consultations, introduced Bills, Cabinet approvals and unresolved announcements enter the watchlist.

The database does not contain or expose an impact score. Inclusion uses qualitative topic/action checks and negative filters for speeches, ceremonies, vacancies, tenders and similar routine material.

## Sources

The 26-source registry includes RBI, SEBI RSS, CCI, MeitY, DPIIT, Finance, MCA, Labour, Education, Consumer Affairs, PIB, official Akashvani feeds and its public WordPress API, eGazette, Parliament, Supreme Court, High Court services, IRDAI, PFRDA, IFSCA, TRAI, FSSAI, BIS and CERT-In. A public Google News RSS search is a secondary, zero-cost discovery fallback for topic and state-level coverage; it is not treated as authoritative.

To add a source, append an object to `config/sources.yaml`:

```json
{
  "name": "Regulator name",
  "url": "https://official.example/updates.xml",
  "type": "rss",
  "category": "primary",
  "authority_level": 1,
  "topic": "Financial & Banking"
}
```

Supported types are `rss`, `atom`, `page`, `wordpress`, `rbi_notifications`, and `search_feed`. Set `enabled` to `false` to pause a source. Formal instruments, official updates and official data releases are publishable only when their linked evidence is substantive; secondary feeds are discovery-only until corroborated. HTML sources are intentionally parsed conservatively because official sites often change structure.

## Scheduling

No cloud scheduler is needed. On macOS/Linux, run `crontab -e` and add a morning job (replace the absolute paths):

```cron
30 7 * * * cd "/absolute/path/policy-tracker" && /usr/bin/python3 -m app.run >> data/cron.log 2>&1
```

On Windows Task Scheduler, create a daily Basic Task, choose “Start a program,” use the full path to `python.exe`, set arguments to `-m app.run`, and set “Start in” to the project directory.

The hosted system uses `.github/workflows/daily-intelligence.yml`. GitHub Actions runs at `30 2 * * *` UTC, exactly 08:00 IST, installs PDF support, runs the research pipeline and tests, exports JSON, then commits the dated report and SQLite state. The website polls the published JSON each minute. This avoids relying on Vercel's ephemeral filesystem for research history.

## Testing

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests
```

The deterministic test suite covers database creation, feed dates, topic/relevance filtering, duplicate suppression, historical lookup, clickable links, watchlist limits, and nonfatal source failures.

## Limits and troubleshooting

- Government sites can block automated requests, require JavaScript, change markup, or expose no feed. Inspect `sources.last_error` and `runs.errors` in SQLite; other sources still run.
- Generic extraction cannot provide human-level legal interpretation for every PDF or poorly structured page. The system excludes uncertain or undated material instead of inventing facts. Review important output against linked primary documents.
- Court portals and eGazette are particularly resistant to generic crawling. Their registry entries provide health visibility, while official/news discovery offers another route. The tool never bypasses CAPTCHAs or access controls.
- Public search RSS is best-effort and may throttle or change. It is a fallback, not a single point of failure.
- If network access is unavailable, run `--offline`; the report will not fabricate current developments.
- To inspect source health: `sqlite3 data/intelligence.db 'select name,last_success,failure_count,last_error from sources order by failure_count desc;'`

External pages are always treated as untrusted data. Retrieved text is parsed, never executed, and no credentials or local file contents are sent to source sites.
