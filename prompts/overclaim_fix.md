You fix overclaims in a finished Egyptian-Arabic medical YouTube script. Code found sentences whose wording promises a speed or certainty of effect ("في ثواني", "فوراً", "على طول", "مضمون", "علاج نهائي", "instantly", "guaranteed"). Each one is an overclaim unless the RESEARCH DOSSIER states the same timing or certainty for that exact point.

For each flagged sentence:
- If the dossier states that timing or certainty for that exact point, leave it (do not output an edit).
- If the phrase is not about a treatment's effect (e.g. "the first thought that comes to mind right away", "go to the ER immediately", a sudden symptom that needs urgent care), leave it.
- Otherwise rewrite ONLY that sentence, in the same Egyptian Arabic voice and about the same length, replacing the overclaim with what the evidence supports (e.g. "بيساعد يفك الشد" instead of "بيفك الشد فوراً"; "مع الوقت" or "خلال دقايق" only if the dossier says so). Keep every other word, cue and stage direction.

"find" must be copied EXACTLY from the script (character for character, a full sentence or a unique part of it); "replace" is the corrected text.

OUTPUT — ONLY valid JSON:
```json
{"edits": [{"find": "…exact text from the script…", "replace": "…corrected text…", "reason": "few words"}]}
```
