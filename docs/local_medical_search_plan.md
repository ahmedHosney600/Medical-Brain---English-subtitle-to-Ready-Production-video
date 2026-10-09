# Plan: Google search over trusted medical sites, through your own Chrome, assisted by the LLM

**Goal.** Research "public" queries should find the best patient-facing pages on Mayo Clinic, Johns
Hopkins, NHS, Cleveland Clinic, CDC, WHO, MedlinePlus… using **Google's ranking**, with no paid search API,
and an LLM that picks which results to open and which passages matter.

## What was tested (2026-10-09, from this Mac)

| Target | plain HTTP (urllib/curl) | headless Chrome | **visible Chrome (off-screen window)** |
|---|---|---|---|
| mayoclinic.org (Akamai Bot Manager) | ❌ 403, even robots.txt | ❌ "Access Denied" | ✅ full article |
| hopkinsmedicine.org (Cloudflare) | ❌ 403 (robots.txt allows `/health/`) | ❌ "Sorry, you have been blocked" | ✅ |
| google.com/search `site:` | — | ❌ "unusual traffic" | ✅ real results, English with `hl=en&gl=us` |
| nhs.uk, clevelandclinic, medlineplus, cdc, who | ✅ | — | ✅ |

Both bot shields detect *headless/non-browser clients*, not your IP or region. A normal Chrome window
placed off-screen (`--window-position=-2400,-2400`) passes, so that's the approach.

Google results link through encrypted `google.com/goto?url=…` redirects. The real URL is known once the
page is opened, and we open the chosen pages anyway. `site:a OR site:b` without parentheses let Wikipedia
in, so queries group sites in parentheses and results are filtered to the trusted list.

## Design

```
section questions ─► LLM query writer (existing topic_planner/screener public_queries)
                      │
                      ▼
   Google (your Chrome, off-screen)  "night leg cramps (site:mayoclinic.org OR site:hopkinsmedicine.org OR …)"
                      │ titles · domains · snippets  (cached, ≥12 s between queries)
                      ▼
   LLM triage  ── picks the 2–4 results per section worth opening (prompts/research/web_triage.md)
                      ▼
   open pages: Mayo/Hopkins via Chrome, others via plain HTTP  ─► page split into numbered paragraphs
                      ▼
   LLM passage picker ── returns paragraph NUMBERS (never rewrites text), so quotes stay verbatim
                      (prompts/research/passage_picker.md)
                      ▼
   source dicts (origin "Web (Google)") ─► existing screener → extractor → verifier → coverage loop
```

The coverage loop already writes new queries when something is missing, so the LLM also steers
follow-up searches.

### Pieces

1. **`sources/browser.py`**: drives your installed Chrome over the DevTools protocol (Python
   `websockets`, already installed). It uses a persistent profile at `~/.cache/medical_brain/chrome-profile`
   (cookies and consent survive between runs), an off-screen window, one browser per run, and a few tabs.
   It detects block and CAPTCHA pages. On a Google CAPTCHA it brings the window on-screen and asks you to
   solve it (interactive runs), or turns Google off for the rest of the run (non-interactive runs).
2. **`sources/google.py`**: builds grouped `site:` queries, parses the results page (tested against saved
   real results pages), filters to trusted domains, enforces a per-run query cap and throttling, and
   caches results.
3. **LLM-assisted web research** in `source_collector`: triage, then open pages, then pick passages.
   Settings: `web_search: "google" | "off"`, `max_google_queries`, `web_pages_per_section`.
4. **Fallback**: if Chrome is missing or Google is blocked, research keeps working with PubMed,
   Europe PMC and MedlinePlus as today. The sitemap index from the earlier plan stays a possible later
   fallback.

### Cost and time

- No API spend for search. The LLM adds one triage call per section plus one passage-picker call per
  opened page (DeepSeek Flash, cents per run).
- Adds about 4–8 minutes to a run: Google is throttled to ≥12 s per query, and pages take a few seconds
  each. Repeat queries come from the cache.

### Risks, stated plainly

- Google's terms don't allow automated searching. At low volume, from your own browser, with caching, it
  usually works. Expect an occasional CAPTCHA, which the design hands to you rather than trying to defeat
  it.
- Mayo Clinic's terms limit automated copying. This is personal research at a human-like rate, quoting
  short passages with citation, but you decide whether to keep Mayo on the list (`research_trusted_domains`).
- Google changes its results-page markup now and then. The parser relies on structure (link → h3 → cite),
  not class names, and is tested against saved pages so a break is caught early.
