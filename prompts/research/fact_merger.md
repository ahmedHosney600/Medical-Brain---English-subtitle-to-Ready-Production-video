You tidy a list of verified medical facts for one section of a patient-education dossier. Several sources often say the same thing in different words (e.g. Mayo Clinic and MedlinePlus giving the same stretching advice). Find those duplicates so they become ONE fact backed by several sources — agreement between trusted sources is valuable, repetition is not.

Two facts are duplicates only if a viewer would learn nothing new from the second one: same advice, same finding, same population, same direction. Do NOT merge facts that differ in a number, population (adults vs pregnancy vs older adults), strength of evidence, or a meaningful detail (e.g. "stretch the calf" vs "stretch the front of the thigh").

For each duplicate group, choose the one fact to keep: the clearest and most complete wording for a patient, preferring the stronger evidence when wording is equal.

OUTPUT — ONLY valid JSON (empty "groups" if there are no duplicates):
```json
{"groups": [{"keep": "F12", "merge": ["F40", "F57"], "why": "same calf-stretch advice"}]}
```
