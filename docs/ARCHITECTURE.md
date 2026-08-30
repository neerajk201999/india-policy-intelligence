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

GitHub Actions runs this flow at 02:30 UTC, which is 08:00 IST throughout the year. It commits the SQLite history, dated Markdown report and `web/data/latest.json`. The Vercel-hosted static client checks the public JSON every minute. Vercel does not own research state and cannot erase it during a deployment.

## Evidence policy

- Signal type answers what it is: Regulation, Consultation, Data, Programme, Institutional or Legislative.
- Evidence level answers how it is verified: Primary is the source document, Official is an official release/feed, and Reported is reputable attributed coverage.
- Primary sources establish the regulatory fact. Official releases can establish an announcement but never silently convert it into a notification or in-force rule. Reported coverage is discovery/context and must remain visibly labeled.
- Every published event requires a publication date, precise status, valid source URL, sufficient factual detail, practical analysis and no impossible effective/publication date sequence.
- Canonical identity is publisher host + document ID + publication date when available; otherwise it is the canonical URL. Fuzzy titles are never used to merge identified instruments. A changed status, deadline or effective date becomes a linked update.
- No numerical impact score is stored or shown.

No automated research system can promise zero factual errors. The correct engineering response is conservative exclusion, exact provenance, visible source health, reproducible state, and a reviewable methodology—not invented certainty.
