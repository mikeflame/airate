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
        self.assertEqual(up["u"], 1.0, "a 5× cheaper solve is full marks")

    def test_uplift_scale(self):
        self.assertEqual(scoring.uplift_score(1.0), 0.5)
        self.assertEqual(scoring.uplift_score(2.0), 1.0)
        self.assertEqual(scoring.uplift_score(0.5), 0.0)
        self.assertAlmostEqual(scoring.uplift_score(2 ** 0.5), 0.75)
        self.assertEqual(scoring.uplift_score(float("inf")), 1.0)
        self.assertEqual(scoring.uplift_score(0.0), 0.0)

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
        self.assertEqual(scoring.bench_uplift({"arms": {"with": {"trials": [a]}, "baseline": {"trials": [b]}}})["u"], 1.0)
        c = {"success": True, "duration_ms": 2000}
        self.assertEqual(scoring.pick_unit([a], [c]), "duration_ms")


def _comp(p, n):
    return scoring.proportion_component(p * n, n)


class IndexTests(unittest.TestCase):
    def test_missing_evidence_counts_as_zero(self):
        self.assertEqual(scoring.index({"works": None, "first_try": None, "retained": None, "bench": None}), {"raw": 0.0, "lower": 0.0})

    def test_ladder(self):
        one = scoring.index({"works": _comp(1, 1), "first_try": _comp(1, 1), "retained": None, "bench": None})
        three = scoring.index({"works": _comp(1, 3), "first_try": _comp(1, 3), "retained": None, "bench": None})
        ten = scoring.index({"works": _comp(1, 10), "first_try": _comp(1, 10), "retained": None, "bench": None})
        full = scoring.index({"works": _comp(1, 10), "first_try": _comp(1, 10), "retained": _comp(1, 10), "bench": _comp(1, 10)})
        self.assertEqual([round(100 * x["lower"]) for x in (one, three, ten, full)], [5, 26, 34, 86])
        self.assertAlmostEqual(three["lower"], 0.4 * scoring.wilson_lower(1.0, 3))

    def test_thin_component_scales_by_evidence(self):
        self.assertAlmostEqual(scoring.index({"bench": _comp(1, 1)})["lower"], 0.3 * (1 / 3) * scoring.wilson_lower(1.0, 1))
        self.assertAlmostEqual(scoring.index({"bench": _comp(1, 3)})["lower"], 0.3 * scoring.wilson_lower(1.0, 3))
        self.assertAlmostEqual(scoring.index({"bench": _comp(1, 30)})["lower"], 0.3 * scoring.wilson_lower(1.0, 30))

    def test_more_positive_evidence_never_lowers(self):
        base = {"works": _comp(1, 9), "first_try": _comp(1, 9), "retained": None, "bench": None}
        self.assertGreater(scoring.index(base | {"bench": _comp(1, 1)})["lower"], scoring.index(base)["lower"])
        self.assertGreater(scoring.index(base | {"retained": _comp(1, 1)})["lower"], scoring.index(base)["lower"])
        self.assertGreater(scoring.index(base | {"works": _comp(1, 10), "first_try": _comp(1, 10)})["lower"], scoring.index(base)["lower"])

    def test_retention_cannot_stand_in_for_install_evidence(self):
        comps = {"works": _comp(1, 3), "first_try": _comp(1, 3), "retained": _comp(1, 100), "bench": None}
        idx = scoring.index(comps)
        self.assertLess(idx["lower"], 0.56, "three installs plus a hundred weeklies stay far from the top")
        self.assertAlmostEqual(idx["lower"], 0.4 * scoring.wilson_lower(1, 3) + 0.3 * scoring.wilson_lower(1, 100))


class RepeatWeightTests(unittest.TestCase):
    def test_harmonic(self):
        self.assertEqual([scoring.repeat_weight(1.0, k) for k in range(4)], [1.0, 0.5, 1 / 3, 0.25])
        self.assertEqual(scoring.repeat_weight(0.25, 1), 0.125)

    def test_one_account_stays_low(self):
        weights = [scoring.repeat_weight(1.0, k) for k in range(9)]
        install, bench, weekly = weights[:3], weights[3:6], weights[6:]
        comps = {"works": _comp(1, sum(install)), "first_try": _comp(1, sum(install)),
                 "bench": _comp(1, sum(bench)), "retained": _comp(1, sum(weekly))}
        self.assertLess(round(100 * scoring.index(comps)["lower"]), 20, "nine perfect reports from one account")


if __name__ == "__main__":
    unittest.main()
