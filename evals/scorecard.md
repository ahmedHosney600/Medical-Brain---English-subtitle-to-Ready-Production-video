# Research eval scorecard

One row per scored session. Baseline is the `v1-baseline-research-web` tag; rows are comparable only when the topic bank stayed frozen (same `questions`).

| date | label | slug | commit | minutes | cost | facts | left out | web % | core answered | off-topic % | low-value % | safety gaps | answers | actionability | safety | focus | honesty | mean |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-09 17:43 | v1-baseline-deepseek | muscle-cramps | e4fd55a | 30.4 | — | 175 | 1 | 60.6 | 8/9 | 10.3 | 10.3 | 8 | 8 | 8 | 7 | 6 | 7 | 7.20 |

_`cost` is approximate: DeepSeek balance delta (before − after). Concurrent DeepSeek usage would inflate it; `—` means the balance was unavailable._

## Latest per topic

### `muscle-cramps` — v1-baseline-deepseek · 2026-10-09 17:43 (commit e4fd55a)

The dossier is strong on immediate cramp relief, heat-cramp first aid, common triggers, prevention basics, and serious red flags such as DVT/PE, PAD, weakness, swelling, toxin exposure, and heat illness. The main patient-facing gaps are the doctor work-up, children, pregnancy-specific safety, vitamin D, and popular remedies such as bananas, dates, creams, olive oil, and black seed oil. Many facts are off-topic or low-value for a general audience, especially dialysis, restless-legs/venous, Ramadan, athlete-incidence, and trial-design details. Evidence is often honestly labelled, but hydration/electrolyte prevention is stated too confidently and the conflicting magnesium-in-pregnancy results are not reconciled. Safety would improve with explicit warnings about rhabdomyolysis, heat stroke, avoiding massage of a suspected DVT, quinine, and electrolyte supplements in kidney or heart disease.

**Core questions not fully answered:**
- Q7 (partly): Do magnesium, potassium, calcium, or vitamin D supplements actually work for cramps, and what does the evidence say?
