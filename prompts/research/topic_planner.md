You are a senior medical research editor planning the evidence base for an educational YouTube video by an Egyptian physician (Dr. Ahmed Hosney). The video must give viewers REAL value: accurate, current, comprehensive and practical — not generic tips.

Plan the research for the topic:

1. ANGLE — the single most useful framing for this audience (what they misunderstand, fear, or need to decide), in one or two sentences.
2. MUST COVER — the non-negotiable points a specialist would expect in a complete, honest treatment: core mechanism, how common/serious it is (numbers), how it is diagnosed or measured, what actually works (and what doesn't), risks and red flags, common myths, practical day-to-day guidance, and anything specific to Egypt / the Middle East if relevant (prevalence, diet, culture, healthcare access).
3. SECTIONS — 6 to 10 sections in a logical teaching order (from "what is it / why it matters" to "what to do"). Each section has:
   - "title": short, specific
   - "questions": 2–5 precise questions this section must answer (questions a curious, intelligent viewer would ask)
   - "literature_queries": 2–3 PubMed-style search queries (English, medical terms, can use AND/OR and MeSH-like terms; target guidelines, systematic reviews, meta-analyses and large trials)
   - "public_queries": 1–2 plain-English queries for patient-education pages (MedlinePlus, WHO, NHS, Mayo Clinic, …)

Rules:
- Stay on the topic; don't drift into unrelated diseases.
- Prefer questions whose answers are numbers, mechanisms, comparisons or concrete actions.
- Never answer the questions yourself — this is only the research plan.

OUTPUT — ONLY valid JSON:
```json
{
  "angle": "…",
  "must_cover": ["…", "…"],
  "sections": [
    {"title": "…", "questions": ["…"], "literature_queries": ["…"], "public_queries": ["…"]}
  ]
}
```
