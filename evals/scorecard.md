# Research eval scorecard

One row per scored session. Baseline is the `v1-baseline-research-web` tag; rows are comparable only when the topic bank stayed frozen (same `questions`).

| date | label | slug | commit | minutes | cost | facts | left out | web % | core answered | off-topic % | low-value % | safety gaps | answers | actionability | safety | focus | honesty | mean |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-09 17:43 | v1-baseline-deepseek | muscle-cramps | e4fd55a | 30.4 | — | 175 | 1 | 60.6 | 8/9 | 10.3 | 10.3 | 8 | 8 | 8 | 7 | 6 | 7 | 7.20 |
| 2026-10-10 22:25 | v1-baseline-gw | muscle-cramps | 15ae3ec | 7.0 | $0.0000 | 40 | 0 | 55.0 | 3/9 | 5.0 | 10.0 | 3 | 5 | 6 | 3 | 6 | 7 | 5.40 |

_`cost` is approximate: DeepSeek balance delta (before − after). Concurrent DeepSeek usage would inflate it; `—` means the balance was unavailable._

## Latest per topic

### `muscle-cramps` — v1-baseline-gw · 2026-10-10 22:25 (commit 15ae3ec)

The dossier provides clear, practical first-aid advice for treating sudden muscle cramps and outlines basic demographic risk groups and underlying medical links like statins and kidney failure. However, it completely misses critical safety guidance on when to seek emergency medical care and lacks coverage for key patient questions regarding diagnostic tests, electrolyte supplementation evidence details, and home remedies. Additionally, some facts focus on agricultural heat stress epidemiology which falls outside the core focus of general night and exercise cramps.

**Core questions not fully answered:**
- Q3 (partly): Why do cramps often happen at night or wake me up from sleep?
- Q4 (partly): Why do cramps happen during or after exercise, and can I prevent them?
- Q7 (partly): Do magnesium, potassium, calcium, or vitamin D supplements actually work for cramps, and what does the evidence say?
- Q10 (partly): Which medicines or health conditions — like diuretics, statins, diabetes, kidney disease, or thyroid problems — can cause muscle cramps?
- Q13 (no): When should I see a doctor or go to the emergency department for a muscle cramp?
- Q14 (partly): What can I do to prevent cramps during sleep and exercise — stretching, hydration, electrolytes, warm-up, or other steps?
