import unittest

from medical_brain.utils.overclaims import OVERCLAIM, _EFFECT, find_overclaims, overclaim_in


class OverclaimTest(unittest.TestCase):
    def test_arabic_and_english_time_to_effect_and_certainty(self):
        for s in ["الحركة دي بتخليها تفك الانقباض في ثواني.", "وسيب جسمك يفك الأزمة في ثواني معدودة!",
                  "حركة في 10 ثواني", "ده علاج نهائي للمشكلة", "النتيجة مضمونة", "stops the cramp instantly",
                  "relief within seconds", "a guaranteed fix"]:
            self.assertTrue(OVERCLAIM.search(s), s)

    def test_plain_sentences_pass(self):
        for s in ["الإطالة ممكن تساعد في تخفيف الشد.", "Stretching may help some people.", "استشير الدكتور لو الشد اتكرر."]:
            self.assertFalse(OVERCLAIM.search(s), s)

    def test_find_overclaims_returns_sentences_and_ignores_stage_directions(self):
        text = "[لقطة قريبة في ثواني] افرد رجلك. الحركة دي بتفك الشد في ثواني! اسأل دكتورك."
        found = find_overclaims(text)
        self.assertEqual(len(found), 1)
        self.assertIn("بتفك الشد", found[0][0])


class OverclaimContextTest(unittest.TestCase):
    def test_urgent_care_and_myth_busting_are_not_flagged(self):
        from medical_brain.utils.overclaims import overclaim_in
        for s in ["ولازم تتوجه للمستشفى فوراً عشان تعمل الفحص.", "دي حالة طوارئ محتاجة إسعاف فوراً.",
                  "طب لو الحكاية مش نقص سحري في الفيتامينات.", "Go to the hospital immediately if this happens."]:
            self.assertIsNone(overclaim_in(s), s)

    def test_treatment_effect_claims_are_flagged(self):
        from medical_brain.utils.overclaims import overclaim_in
        for s in ["الضغط ده بيبعت إشارة فورية بتوقف الانقباض في لحظتها!", "وسيب جسمك يفك الأزمة في ثواني.",
                  "This stretch stops the cramp instantly."]:
            self.assertIsNotNone(overclaim_in(s), s)


class RangeEndpointHundredTest(unittest.TestCase):
    """A "100%" that ends a numeric range is an endpoint, not a certainty claim."""

    def test_hundred_at_the_end_of_a_range_is_not_an_overclaim(self):
        for s in ["من خمسة وسبعين لحد مية في المية من محيط الذراع",
                  "75-100%", "75 to 100%", "٧٥ لحد ١٠٠٪"]:
            self.assertIsNone(overclaim_in(s), s)

    def test_other_hundred_uses_are_still_flagged(self):
        for s in ["النتيجة مضمونة 100%", "بيعالج مية في المية"]:
            self.assertIsNotNone(overclaim_in(s), s)


class RedFlagOnsetTest(unittest.TestCase):
    def test_sudden_symptom_with_emergency_is_not_an_overclaim(self):
        s = "لو حسيت بضعف ظهر فجأة في ثواني في جنب واحد، دي حالة طارئة وروح الطوارئ فوراً."
        self.assertIsNone(overclaim_in(s))

    def test_treatment_in_seconds_still_flagged(self):
        self.assertEqual(overclaim_in("الحباية دي بتفك الصداع في ثواني."), "في ثواني")

    def test_denied_immediate_effect_is_not_flagged(self):
        s = "المسكنات بتفيد بعد النوبة، لكن مش هي أبداً اللي بتفك الشد في لحظتها."
        self.assertIsNone(overclaim_in(s))
        self.assertTrue(overclaim_in("حركة واحدة تفكها فوراً"))


class EffectWordBoundaryTest(unittest.TestCase):
    """The effect-verb regex must match whole words, not stems inside unrelated words
    (the real false positive: "فكرة" contains "فك")."""

    MATCH = ["تفكها", "بيفك", "يفك", "بتوقف", "يوقف", "بتخف", "بيخف", "تهدي", "بيهدي",
             "بتعالج", "يختفي", "بيروح"]
    NO_MATCH = ["فكرة", "تفكير", "خفيف", "وقفة", "الفكرة"]

    def test_effect_stems_match_only_as_words(self):
        for word in self.MATCH:
            self.assertTrue(_EFFECT.search(word), word)
        for word in self.NO_MATCH:
            self.assertIsNone(_EFFECT.search(word), word)

    def test_real_false_positive_is_not_an_overclaim(self):
        self.assertIsNone(overclaim_in("أول فكرة بتيجي في بالك على طول"))

