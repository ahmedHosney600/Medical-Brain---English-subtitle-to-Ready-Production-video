"""Time-to-effect and certainty wording that health scripts slip in ("stops it in seconds",
"guaranteed", "a final cure") and that evidence rarely supports. The model checks missed
"تفك الانقباض في ثواني" in a real run, so code finds these phrases and hands them to the
medical truth verifier and to the "claims to check before filming" list."""
import re

_AR = [
    r"(?:في|خلال|بعد)\s+(?:\d+|[٠-٩]+|عشر|عشرة|خمس|خمسة|كام|ثلاث|تلات)?\s*ثوان[يى]?(?:\s+معدودة)?",
    r"في\s+ثواني\s+معدودة", r"في\s+لحظتها", r"في\s+ساعتها", r"على\s+طول", r"فورا|فوراً|فوري(?:ة|اً)?",
    r"نهائيا|نهائياً|للأبد", r"مضمون(?:ة)?", r"(?:علاج|حل)\s+(?:نهائي|سحري|جذري)", r"سحري(?:ة)?",
    r"100\s*[%٪]", r"مية\s+في\s+المية", r"بيعالج\s+تماما|بيعالج\s+تماماً", r"مثبت(?:ة)?\s+علمياً?\s+100",
]
_EN = [r"\bwithin (?:a few )?seconds\b", r"\bin (?:\d+ )?seconds\b", r"\binstant(?:ly)?\b", r"\bimmediately\b",
       r"\bguaranteed?\b", r"\bcures?\b", r"\bpermanent(?:ly)? (?:fix|cure)\b", r"\bmiracle\b", r"\b100 ?%"]
OVERCLAIM = re.compile("|".join(_AR + _EN), re.IGNORECASE)

_SENTENCE = re.compile(r"[^.!?؟\n]+[.!?؟]?")


_SECONDS = re.compile(_AR[0] + r"|في\s+ثواني\s+معدودة|\bwithin (?:a few )?seconds\b|\bin (?:\d+ )?seconds\b", re.IGNORECASE)
_IMMEDIATE = re.compile(r"فورا|فوراً|فوري(?:ة|اً)?|في\s+لحظتها|في\s+ساعتها|على\s+طول|\binstant(?:ly)?\b|\bimmediately\b",
                        re.IGNORECASE)
_EFFECT = re.compile(r"(?:ب?[تي]?(?:فك|وقف|خف|هدي|عالج|ختفي|روح|زيل))|توقف|يوقف|بتوقف|تفك|يفك|بيفك|"
                     r"\b(?:stops?|relieves?|ends?|cures?|fixes|goes away)\b", re.IGNORECASE)
_URGENT = re.compile(r"مستشفى|المستشفى|طوارئ|الطوارئ|إسعاف|الإسعاف|دكتور|الدكتور|طبيب|الطبيب|اتصل|\bemergency\b|\bhospital\b|\bdoctor\b|\b911\b",
                     re.IGNORECASE)
_NEGATED = re.compile(r"(?:^|\s)(?:مش|مفيش|لا|ولا|ليس|مافيش|\bnot\b|\bno\b|\bisn't\b)(?:\s|$)", re.IGNORECASE)


def overclaim_in(sentence: str):
    """The overclaim phrase in one sentence, or None. "In seconds" always counts; "immediately"
    counts only for a treatment effect (not for "go to hospital immediately"); "magic",
    "guaranteed", "final cure" count unless the sentence denies them (myth-busting)."""
    m = _SECONDS.search(sentence)
    if m:
        return m.group(0)
    m = _IMMEDIATE.search(sentence)
    if m and _EFFECT.search(sentence) and not _URGENT.search(sentence):
        return m.group(0)
    m = OVERCLAIM.search(sentence)
    if m and not _IMMEDIATE.fullmatch(m.group(0)) and not _NEGATED.search(sentence):
        return m.group(0)
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
