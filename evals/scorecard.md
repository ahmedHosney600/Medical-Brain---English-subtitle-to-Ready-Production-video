# Research eval scorecard

One row per scored session. Baseline is the `v1-baseline-research-web` tag; rows are comparable only when the topic bank stayed frozen (same `questions`).

| date | label | slug | commit | minutes | cost | facts | left out | web % | core answered | off-topic % | low-value % | safety gaps | answers | actionability | safety | focus | honesty | mean | guide core | guide mean |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-09 17:43 | v1-baseline-deepseek | muscle-cramps | e4fd55a | 30.4 | — | 175 | 1 | 60.6 | 8/9 | 10.3 | 10.3 | 8 | 8 | 8 | 7 | 6 | 7 | 7.20 | — | — |
| 2026-10-10 22:25 | v1-baseline-gw | muscle-cramps | 15ae3ec | 7.0 | $0.0000 | 40 | 0 | 55.0 | 3/9 | 5.0 | 10.0 | 3 | 5 | 6 | 3 | 6 | 7 | 5.40 | — | — |
| 2026-10-11 01:48 | v3-research2-agy | muscle-cramps | 03a171f | 15.5 | $0.0000 | 82 | 2 | 56.1 | 5/9 | 6.1 | 7.3 | 4 | 7 | 7 | 7 | 6 | 8 | 7.00 | 7/9 | 7.20 |
| 2026-10-11 02:05 | v3-research2-agy | migraine | 03a171f | 14.9 | $0.0000 | 99 | 0 | 50.5 | 4/8 | 0.0 | 8.1 | 3 | 7 | 7 | 8 | 6 | 7 | 7.00 | 4/8 | 6.60 |
| 2026-10-11 02:22 | v3-research2-agy | statins | 03a171f | 16.2 | $0.0000 | 87 | 2 | 37.9 | 8/9 | 0.0 | 5.7 | 3 | 8 | 8 | 9 | 8 | 8 | 8.20 | 6/9 | 6.80 |

_`cost` is approximate: DeepSeek balance delta (before − after). Concurrent DeepSeek usage would inflate it; `—` means the balance was unavailable._

## Latest per topic

### `muscle-cramps` — v3-research2-agy · 2026-10-11 01:48 (commit 03a171f)

The dossier provides clear, practical advice for immediate cramp relief (stretching, weight-bearing, heat/ice) and offers strong evidence regarding exercise fatigue and the lack of magnesium efficacy in older adults. However, it suffers from heavy redundancy across acute relief recommendations, includes off-topic academic tangents (ALS/MND pharmacological studies and chamomile chemical profiles), and completely misses clinical workup (Q6). Crucially for patient safety, it lacks an explicit warning against vigorously massaging a swollen leg that could be a deep vein thrombosis rather than a simple cramp.

**Core questions not fully answered:**
- Q2 (partly): Is a sudden muscle cramp dangerous, or could it be a sign of something serious like a blood clot or nerve problem?
- Q3 (partly): Why do cramps often happen at night or wake me up from sleep?
- Q7 (partly): Do magnesium, potassium, calcium, or vitamin D supplements actually work for cramps, and what does the evidence say?
- Q10 (partly): Which medicines or health conditions — like diuretics, statins, diabetes, kidney disease, or thyroid problems — can cause muscle cramps?

**Guide judge** — 7/9 core answered · mean 7.20 · 2 unsafe / misleading items.

The guide provides strong, practical physical advice for aborting acute calf cramps and distinguishes benign cramps from emergencies like DVT effectively. However, it completely omits the doctor's diagnostic evaluation process (Q6) and fails to address children at all (Q12). Furthermore, it overstates certainty by labeling Coenzyme Q10 as 'proven' for statin cramps and elevates niche interventions like Botox injections onto the general action card.

**Unsafe or misleading:**
- Coenzyme Q10 supplementation (proven to reduce statin-associated muscle cramps in clinical trials). — Labels CoQ10 for statin cramps as 'proven' on the action card. Major clinical trials and meta-analyses show mixed or insufficient evidence for CoQ10 relieving statin myalgia/cramps, so presenting this as definitively proven can mislead patients into buying supplements instead of discussing statin-associated muscle symptoms with their physician.
- Acupuncture or botulinum toxin-A injections (shown to provide relief for neuropathic cramps in diabetes). — Listing botulinum toxin-A injections and acupuncture under general action card 'works' for diabetic cramps is misleading and clinically premature as routine first-line advice for the general public, lacking nuance on indication, accessibility, and standard medical management.


### `migraine` — v3-research2-agy · 2026-10-11 02:05 (commit 03a171f)

The dossier provides robust clinical coverage of acute rescue options, medication-overuse headache thresholds, red-flag emergency symptoms, and pregnancy/breastfeeding considerations. However, it completely fails to cover everyday lifestyle triggers (fasting, sleep, weather, foods) relevant to an Egyptian and Arab audience, and leaves out child-specific migraine manifestations and non-drug immediate actions. Additionally, the dossier includes excessive academic trial-design trivia, uncritical reporting on blood-letting/cupping efficacy, and duplicate red-flag listings.

**Core questions not fully answered:**
- Q1 (partly): How can I tell if my headache is an actual migraine or just a regular tension or sinus headache?
- Q3 (partly): What should I do immediately in the first hour of an attack to stop the migraine before it becomes unbearable?
- Q7 (no): What everyday triggers—like Ramadan fasting, sleep changes, weather, stress, or certain foods—are secretly bringing on my attacks?
- Q9 (partly): What are the proven options for long-term migraine prevention, and how long do they take to show results?

**Guide judge** — 4/8 core answered · mean 6.60 · 2 unsafe / misleading items.

The guide covers acute treatment, medication overuse headache, and emergency red flags reasonably well, but it completely ignores triggers (Ramadan fasting, weather, stress, foods) and pediatric migraines. Several questions are only partially addressed, including sinus headache differentiation, specific aura symptoms (like dizziness/numbness), and expected timelines for preventive medications to work. The action card also misses key practical thresholds, such as the exact frequency indicating a need for daily preventives versus managing acute attacks.

**Unsafe or misleading:**
- Acupuncture and daily magnesium supplementation to reduce attack frequency or severity (proven/probable benefit). — Labels acupuncture as 'proven' on the Action Card despite the Complementary section noting evidence is weak/disputed and driven by non-specific placebo effects.
- Stopping overused medicines initially causes withdrawal headaches lasting 2 to 10 days (and sometimes weeks); abrupt stopping alone has shown no significant benefit. — A patient reading that abrupt cessation has 'no significant benefit' may be confused or discouraged from attempting structured medication withdrawal.


### `statins` — v3-research2-agy · 2026-10-11 02:22 (commit 03a171f)

The dossier provides robust clinical and safety data addressing core patient concerns, notably rhabdomyolysis warning signs, nocebo effects, drug-food interactions, and pregnancy nuances. However, it lacks practical clinical guidance for patients to differentiate statin myalgia (typically bilateral, large muscle groups) from common joint aches. Additionally, coverage of culturally prominent Arab herbal remedies is incomplete, leaving out widely used items like olive oil and artichoke.

**Core questions not fully answered:**
- Q3 (partly): Is statin-related muscle pain common and dangerous, and how do I tell it apart from everyday fatigue or joint aches?

**Guide judge** — 6/9 core answered · mean 6.80 · 3 unsafe / misleading items.

The guide covers core clinical concepts, cardiovascular risk stratification, drug interactions, and the critical danger of abruptly discontinuing statins. However, it fails to explain how patients can practically distinguish statin-induced muscle aches from ordinary joint fatigue, and it overlooks non-statin medical alternatives (like ezetimibe). Most critically from a safety standpoint, placing unstandardized supplements like red yeast rice and apple cider vinegar under 'Evidence-Based Options' sends dangerous, mixed signals to a lay Arab audience. Simplifying technical jargon (CYP3A4, hazard ratios, ApoB) is necessary before adapting this into a patient-centered video.

**Unsafe or misleading:**
- COMPLEMENTARY — EVIDENCE-BASED OPTIONS: Red yeast rice (Monascus purpureus): In a network meta-analysis of 131 trials, red yeast rice significantly lowered LDL cholesterol by 36.4 mg/dL (-0.94 mmol/L); however, it acts pharmacologically like lovastatin with the same potential side effects. — Listing red yeast rice under 'Evidence-Based Options' in a patient-facing document is dangerously contradictory when both the Action Card and Myths explicitly brand it as harmful, unregulated, and carrying identical toxicity risks to prescription statins.
- COMPLEMENTARY — EVIDENCE-BASED OPTIONS: Apple cider vinegar: Consumption in clinical trials produced a modest, statistically significant reduction in total cholesterol of -6.72 mg/dL, though it does not significantly reduce LDL cholesterol. — Categorizing apple cider vinegar under 'Evidence-Based Options' risks misleading Egyptian and Arab viewers into thinking it is a recommended therapeutic option, despite negligible clinical relevance and no LDL reduction.
- COMPLEMENTARY — POPULAR BUT NOT PROVEN: Herbal liver detox tonics and 'drug holidays' — Framing 'drug holidays' under complementary options trivializes dangerous therapy interruption, especially when the guide elsewhere notes stopping therapy drastically increases death.
