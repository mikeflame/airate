import math
import unittest

from helpers import fixture  # noqa: F401  (sets sys.path)
import scoring


class WilsonTests(unittest.TestCase):
    def test_single_perfect_report_is_far_from_100(self):
        self.assertAlmostEqual(scoring.wilson_lower(1.0, 1), 0.379, places=3)

    def test_grows_with_evidence(self):
        self.assertAlmostEqual(scoring.wilson_lower(1.0, 10), 0.859, places=3)
        self.assertAlmostEqual(scoring.wilson_lower(1.0, 30), 0.948, places=3)
        self.assertLess(scoring.wilson_lower(1.0, 3), scoring.wilson_lower(1.0, 10))

    def test_no_evidence_is_zero(self):
        self.assertEqual(scoring.wilson_lower(0.5, 0), 0.0)

    def test_half_half(self):
        self.assertLess(scoring.wilson_lower(0.5, 10), 0.5)


class BenchUpliftTests(unittest.TestCase):
    def test_fixture_is_an_uplift_in_usd(self):
        up = scoring.bench_uplift(fixture("bench.json")["bench"])
        self.assertEqual(up["unit"], "usd")
        # with: median 0.13 / 100% ; baseline: median 0.33 / 50% → 0.66 ; ratio ≈ 5.08
        self.assertAlmostEqual(up["ratio"], 0.33 / 0.5 / 0.13, places=6)
        self.assertGreater(up["u"], 0.8)

    def test_equal_arms_are_neutral(self):
        t = {"success": True, "duration_ms": 1000, "cost_usd": 1.0}
        up = scoring.bench_uplift({"arms": {"with": {"trials": [t]}, "baseline": {"trials": [t]}}})
        self.assertEqual(up["u"], 0.5)

    def test_unsolvable_with_subject_is_zero(self):
        bad = {"success": False, "duration_ms": 1000, "cost_usd": 1.0}
        good = {"success": True, "duration_ms": 1000, "cost_usd": 1.0}
        up = scoring.bench_uplift({"arms": {"with": {"trials": [bad]}, "baseline": {"trials": [good]}}})
        self.assertEqual(up["u"], 0.0)

    def test_unsolvable_baseline_is_one(self):
        bad = {"success": False, "duration_ms": 1000, "cost_usd": 1.0}
        good = {"success": True, "duration_ms": 1000, "cost_usd": 1.0}
        up = scoring.bench_uplift({"arms": {"with": {"trials": [good]}, "baseline": {"trials": [bad]}}})
        self.assertEqual(up["u"], 1.0)
        self.assertTrue(math.isinf(up["ratio"]))

    def test_falls_back_to_tokens_then_duration(self):
        a = {"success": True, "duration_ms": 1000, "input_tokens": 10, "output_tokens": 10}
        b = {"success": True, "duration_ms": 4000, "input_tokens": 30, "output_tokens": 30, "cost_usd": 0.5}
        self.assertEqual(scoring.pick_unit([a], [b]), "tokens")
        self.assertEqual(scoring.bench_uplift({"arms": {"with": {"trials": [a]}, "baseline": {"trials": [b]}}})["ratio"], 3.0)
        c = {"success": True, "duration_ms": 2000}
        self.assertEqual(scoring.pick_unit([a], [c]), "duration_ms")


class CompositeTests(unittest.TestCase):
    def test_renormalises_over_present_components(self):
        only = scoring.composite({"first_use": {"p": 0.8, "n": 10, "lower": 0.6}, "keep": None, "bench": None}, 10)
        self.assertAlmostEqual(only["raw"], 0.8)
        self.assertAlmostEqual(only["lower"], scoring.wilson_lower(0.8, 10))

    def test_weights(self):
        comp = scoring.composite({"first_use": {"p": 1.0, "n": 3, "lower": 1.0}, "keep": {"p": 0.0, "n": 3, "lower": 0.0}, "bench": None}, 3)
        self.assertAlmostEqual(comp["raw"], 0.4 / 0.7)

    def test_thin_component_has_reduced_weight(self):
        thick = {"first_use": {"p": 1.0, "n": 9, "lower": 0.9}, "keep": {"p": 1.0, "n": 9, "lower": 0.9}, "bench": None}
        base = scoring.composite(thick, 9)
        one_bench = scoring.composite(thick | {"bench": {"p": 0.75, "n": 1, "lower": 0.2}}, 10)
        three_bench = scoring.composite(thick | {"bench": {"p": 0.75, "n": 3, "lower": 0.4}}, 12)
        self.assertAlmostEqual(one_bench["raw"], (0.7 + 0.1 * 0.75) / 0.8)
        self.assertAlmostEqual(three_bench["raw"], 0.7 + 0.3 * 0.75)
        self.assertGreater(one_bench["lower"], base["lower"], "a first positive bench report must not lower the rate")

    def test_empty(self):
        self.assertEqual(scoring.composite({"first_use": None, "keep": None, "bench": None}, 0), {"raw": 0.0, "lower": 0.0, "n": 0})


if __name__ == "__main__":
    unittest.main()
