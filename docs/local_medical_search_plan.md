# Plan: local search over trusted medical websites (no search API)

**Goal.** The research workflow's "public" queries (`medical_brain/research/sources/web.py`) today need a
paid Tavily or Brave key; without one, only PubMed, Europe PMC and MedlinePlus are searched. Replace that
with a search that runs on this Mac: no API key, no third-party search engine, works offline once built.

## What was checked (2026-10-09)

| Site | Sitemap | Plain HTTP fetch |
|---|---|---|
| nhs.uk | `sitemap.xml` → `sitemap-cms-content.xml` | ✅ 200 (`/conditions/leg-cramps/`) |
| my.clevelandclinic.org | `sitemap.xml` → `site.xml`, `page.xml` | ✅ |
| medlineplus.gov | `sitemap.xml` (+ free search API already used) | ✅ |
| cdc.gov | `wcms-auto-sitemap-index.xml` | ✅ |
| who.int | `sitemaps/sitemapindex.xml` | ✅ |
| health.harvard.edu | `sitemap.xml` → `sitemap-pages.xml` … | ✅ |
| patient.info, aafp.org | `sitemap.xml` | ✅ |
| mayoclinic.org, hopkinsmedicine.org | **403** (bot protection) | ❌ needs fallback |

## Design: sitemap catalog + on-demand pages + SQLite full-text search

```
build (weekly, ~minutes)          query (during research, ~ms)
sitemaps ──► catalog(url, title,  query ──► FTS5/BM25 over title+slug+summary
             slug, site, lastmod)            │ top N URLs not yet cached
                                             ▼
                                  fetch page (throttled, cached) ──► page_text()
                                             ▼
                                  FTS5 over page body ──► ranked sources (same dict shape as today)
```

1. **Catalog (cheap, eager).** For each site in a `SITES` registry, read the sitemap index, keep only
   health-topic paths (allow-list per site, e.g. `nhs.uk/conditions/`, `nhs.uk/medicines/`,
   `my.clevelandclinic.org/health/`, `medlineplus.gov/ency/`, `cdc.gov/<topic>/about/`,
   `who.int/news-room/fact-sheets/`). Store `url, site, title-from-slug, lastmod` in SQLite. This is
   about 20–60k URLs across all sites. No page downloads yet.
2. **Search.** SQLite **FTS5** (part of Python's `sqlite3`, no install) with BM25 ranking over the
   catalog's title/slug, plus page bodies once cached. Add a small medical synonym map
   (cramp ↔ spasm ↔ charley horse, heart attack ↔ myocardial infarction, …) applied to the query, and
   use the LLM-written `public_queries` as they are.
3. **Pages on demand.** Fetch only the top hits that aren't cached yet. Use the existing `http.get`
   (it already caches, throttles per host and retries), extract text with the existing `page_text()`,
   and index the body so later queries rank by content, not just by title. Respect robots.txt
   (`urllib.robotparser`), send an honest User-Agent, and keep ≥1 s between requests per host.
4. **Refresh.** `python3 -m medical_brain.research.local_index --build` (or automatically when the
   catalog is older than `refresh_days`, default 7). Use sitemap `lastmod` to re-fetch only changed pages.
5. **Blocked sites (Mayo, Hopkins).** Phase 2, optional: fetch through the Chrome you already have
   (headless Playwright or the Claude-in-Chrome extension) for a hand-picked list of URLs, or simply
   leave them out. NHS, Cleveland Clinic, MedlinePlus, CDC and WHO cover the same patient topics.

## Where it plugs in

- New `medical_brain/research/sources/local_index.py`: `build()`, `search(query, max_results, years)`
  returning the same source dicts as `web._source()` (`origin: "Web (local)"`, evidence level from
  `evidence.level_of`).
- `sources/__init__.py`: `PUBLIC = {"MedlinePlus": …, "Web": web.search, "Web (local)": local_index.search}`.
  `web.py` keeps Tavily/Brave as an optional extra; the local index is the default.
- Storage: `~/.cache/medical_brain/web_index.sqlite`, outside the repo and git-ignored.
- Settings: `local_web: true`, `local_web_sites: [...]`, `refresh_days: 7` in input_fields.json.
- `sources.enabled()` shows `Web (local index, N pages)` in the run banner.

## Steps (each a separate, testable task)

1. Site registry + sitemap catalog builder (gzip sitemaps, sitemap indexes, path allow-lists). Tests use
   saved sample sitemaps; no network in tests.
2. FTS5 schema + `search()` over the catalog + synonym expansion. Tests use a tiny in-memory DB.
3. On-demand fetch + body indexing + robots.txt + caching; wire into `PUBLIC`.
4. CLI `--build` / auto-refresh, banner, and a docs section in README.
5. (Optional) Blocked-site fallback through the browser.

**Cost:** zero API spend. First build is a few minutes of sitemap downloads; each research query adds
a few page fetches (cached afterwards).
**Risk:** sites change sitemap paths, so allow-lists live in one registry, and a site that fails to
build is logged and skipped, never fatal.
