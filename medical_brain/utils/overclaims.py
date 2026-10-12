"""Time-to-effect and certainty wording that health scripts slip in ("stops it in seconds",
"guaranteed", "a final cure") and that evidence rarely supports. The model checks missed
"تفك الانقباض في ثواني" in a real run, so code finds these phrases and hands them to the
medical truth verifier and to the "claims to check before filming" list."""
import re

_AR = [
    r"(?:في|خلال|بعد)\s+(?:\d+|[٠-٩]+|عشر|عشرة|خمس|خمسة|كام|ثلاث|تلات)?\s*ثوان[يى]?(?:\s+معدودة)?",
    r"في\s+ثواني\s+معدودة", r"في\s+لحظتها", r"في\s+ساعتها", r"على\s+طول", r"فورا|فوراً|فوري(?:ة|اً)?",
    r"نهائيا|نهائياً|للأبد", r"مضمون(?:ة)?", r"(?:علاج|حل)\s+(?:نهائي|سحري|جذري)", r"سحري(?:ة)?",
    r"100\s*[%٪]", r"١٠٠\s*[%٪]", r"مية\s+في\s+المية", r"بيعالج\s+تماما|بيعالج\s+تماماً", r"مثبت(?:ة)?\s+علمياً?\s+100",
]
_EN = [r"\bwithin (?:a few )?seconds\b", r"\bin (?:\d+ )?seconds\b", r"\binstant(?:ly)?\b", r"\bimmediately\b",
       r"\bguaranteed?\b", r"\bcures?\b", r"\bpermanent(?:ly)? (?:fix|cure)\b", r"\bmiracle\b", r"\b100 ?%"]
OVERCLAIM = re.compile("|".join(_AR + _EN), re.IGNORECASE)

_SENTENCE = re.compile(r"[^.!?؟\n]+[.!?؟]?")

# The "100%" certainty forms. A 100% that ends a numeric range is a range endpoint, not a
# claim ("75 to 100% of arm circumference"), so it is judged by what precedes it.
_HUNDRED = re.compile(r"100\s*[%٪]|١٠٠\s*[%٪]|مية\s+في\s+المية", re.IGNORECASE)
# The text just before a 100% must END with a range word (or a dash) to be an endpoint.
_RANGE_BEFORE = re.compile(
    r"(?:^|\s)(?:لحد|لغاية|إلى|الى|ل)\s*$"
    r"|(?:^|\s)to\s*$"
    r"|[-–—]\s*$",
    re.IGNORECASE)


_SECONDS = re.compile(_AR[0] + r"|في\s+ثواني\s+معدودة|\bwithin (?:a few )?seconds\b|\bin (?:\d+ )?seconds\b", re.IGNORECASE)
_IMMEDIATE = re.compile(r"فورا|فوراً|فوري(?:ة|اً)?|في\s+لحظتها|في\s+ساعتها|على\s+طول|\binstant(?:ly)?\b|\bimmediately\b",
                        re.IGNORECASE)
# An effect stem must be a whole word, not a substring ("فك" inside "فكرة"). An Arabic
# word here is: an optional subject/tense prefix (ب/ت/ي/بي/بت), an effect stem, an
# optional pronoun suffix, with no Arabic letter (U+0621–U+064A) touching either side.
_EFFECT = re.compile(r"(?<![\u0621-\u064a])(?:بت|بي|ب|ت|ي)?(?:فك|وقف|خف|هدي|عالج|ختفي|روح|زيل)"
                     r"(?:ها|هم|ه|وا|ك|و|ي)?(?![\u0621-\u064a])"
                     r"|\b(?:stops?|relieves?|ends?|cures?|fixes|goes away)\b", re.IGNORECASE)
_URGENT = re.compile(r"مستشفى|المستشفى|طوارئ|الطوارئ|إسعاف|الإسعاف|دكتور|الدكتور|طبيب|الطبيب|اتصل|\bemergency\b|\bhospital\b|\bdoctor\b|\b911\b",
                     re.IGNORECASE)
# Symptom onset ("weakness that came on suddenly, within seconds — go to the ER") is a red
# flag, not a promise about a treatment.
_ONSET = re.compile(r"فجأة|فجأه|فجائي|مفاجئ|\bsudden(?:ly)?\b|\babrupt(?:ly)?\b", re.IGNORECASE)
_NEGATED = re.compile(r"(?:^|\s)(?:مش|مفيش|لا|ولا|ليس|مافيش|\bnot\b|\bno\b|\bisn't\b)(?:\s|$)", re.IGNORECASE)


def overclaim_in(sentence: str):
    """The overclaim phrase in one sentence, or None. "In seconds" counts unless it describes a
    sudden symptom that needs urgent care; "immediately"
    counts only for a treatment effect (not for "go to hospital immediately"); "magic",
    "guaranteed", "final cure" count unless the sentence denies them (myth-busting)."""
    m = _SECONDS.search(sentence)
    if m and not (_ONSET.search(sentence) and _URGENT.search(sentence)):
        return m.group(0)
    m = _IMMEDIATE.search(sentence)
    if m and _EFFECT.search(sentence) and not _URGENT.search(sentence) and not _NEGATED.search(sentence):
        return m.group(0)
    if _NEGATED.search(sentence):
        return None
    for m in OVERCLAIM.finditer(sentence):         # seconds / immediately were judged above
        phrase = m.group(0)
        if _IMMEDIATE.fullmatch(phrase) or _SECONDS.fullmatch(phrase):
            continue
        # "100%" ending a numeric range ("75 to 100%", "75-100%", "٧٥ لحد ١٠٠٪") is an
        # endpoint, not a certainty claim.
        if _HUNDRED.search(phrase) and _RANGE_BEFORE.search(sentence[:m.start()]):
            continue
        return phrase
    return None


def find_overclaims(text: str, limit: int = 20) -> list:
    """[(sentence, phrase)] for spoken sentences that contain overclaim wording, in order.
    Stage directions in [brackets] are ignored."""
    clean = re.sub(r"\[[^\]]*\]", " ", text or "")
    out = []
    for m in _SENTENCE.finditer(clean):
        sentence = m.group(0).strip()
        hit = overclaim_in(sentence)
        if hit and len(sentence.split()) >= 3:
            out.append((sentence[:240], hit))
            if len(out) >= limit:
                break
    return out
