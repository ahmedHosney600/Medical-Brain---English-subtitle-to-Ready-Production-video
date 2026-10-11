import unittest

from medical_brain.utils.overclaims import OVERCLAIM, find_overclaims, overclaim_in


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


class RedFlagOnsetTest(unittest.TestCase):
    def test_sudden_symptom_with_emergency_is_not_an_overclaim(self):
        s = "لو حسيت بضعف ظهر فجأة في ثواني في جنب واحد، دي حالة طارئة وروح الطوارئ فوراً."
        self.assertIsNone(overclaim_in(s))

    def test_treatment_in_seconds_still_flagged(self):
        self.assertEqual(overclaim_in("الحباية دي بتفك الصداع في ثواني."), "في ثواني")

