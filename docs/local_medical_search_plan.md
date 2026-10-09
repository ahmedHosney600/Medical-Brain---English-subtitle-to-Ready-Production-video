# Web research over trusted medical sites: how it works and what testing showed

The research workflow's "public" queries find patient-facing pages on Mayo Clinic, Johns Hopkins, NHS and
Cleveland Clinic with **no search API**. An LLM picks which results to open and which passages matter.
Google is an optional extra.

## What was tested (2026-10-09, from this Mac)

| Target | plain HTTP | headless Chrome | visible Chrome, off-screen |
|---|---|---|---|
| mayoclinic.org (Akamai Bot Manager) | ❌ 403, even robots.txt | ❌ "Access Denied" | ✅ |
| hopkinsmedicine.org (Cloudflare) | ❌ 403 | ❌ "Sorry, you have been blocked" | ✅ |
| google.com/search | — | ❌ "unusual traffic" | ✅ at first (see below) |
| nhs.uk, clevelandclinic.org, medlineplus.gov | ✅ | — | ✅ |

**Google turned out to be unreliable for automated searching:**

- `--remote-debugging-port=0` made every search a CAPTCHA, while an explicit port was fine. That's fixed.
- Opening a new tab per search got penalised, so one long-lived search tab is used. That's fixed.
- `site:` with paths is ignored, and more than 2 sites per query returns junk. Queries use at most 2 bare
  domains.
- Even so, after a few queries Google quietly answers a looser query: it drops `site:` and most words
  ("night leg cramps relief site:hopkinsmedicine.org" → "Night by Elie Wiesel"). Google's terms also forbid
  automated searching. So Google is kept as an *optional extra* that detects its own degradation (results
  off-topic or from the wrong sites), switches to a fresh profile once, then turns itself off for the run.

## The reliable path: each site's own search or catalog (respecting robots.txt)

| Site | How | Notes |
|---|---|---|
| Mayo Clinic | its own search `/search/search-results?q=` through Chrome | allowed by robots.txt |
| NHS | its own search `/search/results?q=` over HTTP | allowed; `/search/click?url=` is decoded, never followed |
| Johns Hopkins | `health-sitemap.xml` (3,861 /health/ pages) through Chrome, cached 7 days | `/search` is **disallowed** |
| Cleveland Clinic | `site.xml` (11,039 /health/ pages) over HTTP, cached 7 days | `/search` is **disallowed** |

Catalog results are ranked by matching the query's words (3+ letters) against the page address, and a
page must match at least half of them.

## Flow (`medical_brain/research/nodes.py::_web_research`)

```
section public query ─► site search (Mayo, NHS) + catalogs (Hopkins, Cleveland) [+ Google, if healthy]
                      ─► ONE LLM triage call for all sections (prompts/research/web_triage.md)
                      ─► open picked pages (Mayo/Hopkins via Chrome, others HTTP)
                      ─► LLM passage picker returns paragraph NUMBERS (prompts/research/passage_picker.md)
                      ─► verbatim source text ─► screener → extractor → verifier → coverage loop
```

Web search runs in the first source search of each cycle (start and each coverage round), not in the
screener's gap rounds. Picked web pages keep up to half of a section's candidate slots, so they aren't cut
behind higher-evidence PubMed items.

Settings (`input_fields.json` can override): `web_search` = `sites+google` (default) | `sites` | `google` |
`off`; `web_pages_per_section` 3; `web_queries_per_section` 1; `max_google_queries` 24; `web_refine` false.

## Result on the muscle-cramp topic (run 20261009_153334)

175 verified facts, 1 left out; **106 facts cite web pages**: Mayo Clinic 89 (10 pages, e.g. "Night leg
cramps – Causes" 32, "Muscle cramp – Symptoms and causes" 18, "Heat cramps: First aid" 13), Cleveland
Clinic 13, NHS 4. Johns Hopkins has no cramp page beyond heat illness.
