# Architecture and editorial controls

## Problems found in the original MVP

1. Raw Markdown was served directly, so there was no usable reading, filtering, archive, freshness, or source-health experience.
2. Search-feed headlines could be published without enough underlying evidence. This produced false positives and shallow summaries.
3. Multiple articles about one underlying Bill could become separate developments.
4. Generic HTML extraction could mix navigation and boilerplate into factual summaries.
5. Undated index links risked being interpreted as current.
6. Legal-status precedence could label a draft that invited comments only as a consultation.
7. Feed identifiers could be overwritten by a loose body-text regex, breaking update history.
8. A second same-day run could suppress duplicates and then overwrite the existing edition with an empty report.
9. Source failures could be attributed to the wrong source because error state leaked between collection iterations.
10. Several endpoints were obsolete or inefficient: SEBI had a working RSS feed, TRAI's configured route was a 404, and an official Akashvani feed was missing.
11. Local cron plus SQLite could not provide durable hosted automation. Vercel functions have an ephemeral filesystem.
12. There was no machine-readable publication layer, automated CI, scheduled refresh, or transparent source ledger.

## Corrected design

```text
Official feeds/APIs first + HTML fallback + trusted news discovery
                    │
                    ▼
       freshness and topic/action filtering
                    │
                    ▼
      source-grounded detail / official PDF extraction
                    │
                    ▼
 signal type + legal status, dates, entities and identifiers
                    │
                    ▼
      hard publication-quality evidence gates
                    │
                    ▼
 event-level deduplication + SQLite history comparison
                    │
                    ▼
 Markdown archive + static JSON + editorial website
```

GitHub Actions makes idempotent hourly attempts from 02:00 through 08:00 IST to compensate for its best-effort scheduler. A local gate skips attempts before 07:00 and every later attempt after that date's report exists; manual dispatch remains available. It commits the SQLite history, dated Markdown report and `web/data/latest.json`. The Vercel-hosted client checks both the deployed JSON and the GitHub publication every minute and selects the newer verified timestamp, covering the interval between a bot commit and deployment promotion. Vercel does not own research state and cannot erase it during a deployment.

## Evidence policy

- Signal type answers what it is: Regulation, Consultation, Data, Programme, Institutional or Legislative.
- Evidence level answers how it is verified: Primary is the source document, Official is an official release/feed, and Reported is reputable attributed coverage.
- Primary sources establish the regulatory fact. Official releases can establish an announcement but never silently convert it into a notification or in-force rule. Reported coverage is discovery/context and must remain visibly labeled.
- Every published event requires a publication date, precise status, valid source URL, sufficient factual detail, practical analysis and no impossible effective/publication date sequence.
- Canonical identity is publisher host + document ID + publication date when available; otherwise it is the canonical URL. Fuzzy titles are never used to merge identified instruments. A changed status, deadline or effective date becomes a linked update.
- No numerical impact score is stored or shown.

No automated research system can promise zero factual errors. The correct engineering response is conservative exclusion, exact provenance, visible source health, reproducible state, and a reviewable methodology—not invented certainty.

## Coverage and selection algorithm

The registry contains 52 independently monitored endpoints. RSS, Atom and public JSON APIs are preferred; semantic HTML link extraction is the fallback. Up to eight sources are fetched concurrently with a 15-second timeout and one retry, so one slow portal cannot consume the whole 08:00 run. Every source records its own last check, last success, consecutive failure count and error. Three consecutive failures generate a GitHub Actions warning.

Collection is intentionally broader than publication. Candidates must pass all of these gates:

1. A dated item must fall inside the five-day safety window.
2. Its headline must independently establish a covered topic and a material action or official data release.
3. The detail or PDF must contain enough source-grounded factual text.
4. Title and evidence must overlap; boilerplate, recruitment, tenders and unrelated PDFs are rejected.
5. Effective dates cannot be inferred from historical dates quoted inside an amendment.
6. A primary or official URL and exact provenance must be retained.

Passing candidates are ranked for the daily edition using a deterministic selection-priority function:

`priority = authority + signal + recency + evidence + actionability`

- authority: Primary 30, Official 20, Reported 10
- signal: Regulation 18, Legislative 17, Consultation 15, Data 13, Programme 10, Institutional 8
- recency: 25 points today, declining by 5 per day
- evidence: 5 for a publisher identifier and 4 for at least 100 words of evidence
- actionability: 6 for a recorded effective date or deadline

This score chooses and orders up to 30 developments for the daily Briefing; it is not an impact claim and is neither stored nor displayed. The true daily diff is selected first. If that diff has fewer than eight developments, a labelled five-day context section supplements it from the complete verified record; the UI never presents those context records as newly published today. Every candidate that clears the evidence gate is stored in the complete Tracker, including candidates outside that daily selection. Canonical identity uses publisher host + document ID + publication date, falling back to canonical URL only when an ID is unavailable.

## Credentials and known access constraints

No paid API is required. A free `DATA_GOV_IN_API_KEY` is injected into the scheduled backend job for curated record-level resources, beginning with the official Wholesale Price Index dataset. Authenticated request URLs are redacted from errors and never become citations; the browser and exported publication contain only the public data.gov.in provenance page. The system never disables TLS validation, bypasses CAPTCHAs or disguises a failed endpoint as healthy. MCA and MoEFCC currently expose obsolete TLS from this runtime, while NPCI rejects automated access with HTTP 403; their failures stay visible and other official feeds provide corroborating discovery until those publishers expose a usable endpoint.
