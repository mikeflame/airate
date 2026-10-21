import copy
import unittest

from helpers import fixture
import validate


class ValidateTests(unittest.TestCase):
    def test_fixtures_are_valid(self):
        for name in ("install.json", "weekly.json", "bench.json"):
            with self.subTest(name):
                self.assertEqual(validate.validate_report(fixture(name)), [])

    def test_attestation_must_match_exactly(self):
        r = fixture("install.json")
        r["attestation"] = r["attestation"].replace("agent", "human")
        self.assertTrue(any(e.startswith("attestation") for e in validate.validate_report(r)))
        del r["attestation"]
        self.assertTrue(any("attestation" in e for e in validate.validate_report(r)))

    def test_unknown_keys_rejected(self):
        r = fixture("install.json")
        r["stars"] = 5
        self.assertTrue(any("stars" in e for e in validate.validate_report(r)))

    def test_kind_block_required_and_exclusive(self):
        r = fixture("install.json")
        r["weekly"] = fixture("weekly.json")["weekly"]
        self.assertIn("weekly: not allowed in a install report", validate.validate_report(r))
        r = fixture("install.json")
        del r["install"]
        self.assertTrue(validate.validate_report(r))

    def test_install_consistency(self):
        r = fixture("install.json")
        r["install"]["install_ok"] = False
        self.assertIn("install/first_use_ok: cannot be true when install_ok is false", validate.validate_report(r))
        r = fixture("install.json")
        r["install"]["first_use_ok"] = False
        errors = validate.validate_report(r)
        self.assertIn("install/attempts_to_first_success: must be null when first_use_ok is false", errors)
        r["install"]["attempts_to_first_success"] = None
        r["install"]["minutes_to_first_success"] = None
        self.assertEqual(validate.validate_report(r), [])
        r = fixture("install.json")
        del r["install"]["attempts_to_first_success"]
        self.assertIn("install/attempts_to_first_success: required when first_use_ok is true", validate.validate_report(r))

    def test_weekly_rules(self):
        r = fixture("weekly.json")
        r["weekly"]["week_of"] = "2026-10-20"
        self.assertIn("weekly/week_of: must be the Monday of the week", validate.validate_report(r))
        r = fixture("weekly.json")
        r["weekly"]["status"] = "replaced"
        errors = validate.validate_report(r)
        self.assertIn("weekly/replaced_by: required when status is replaced", errors)
        self.assertIn("weekly/verdict: cannot be keep when the subject was removed or replaced", errors)

    def test_bench_rules(self):
        r = fixture("bench.json")
        r["bench"]["arms"]["with"]["trials"] = []
        self.assertTrue(validate.validate_report(r))
        r = fixture("bench.json")
        r["bench"]["arms"]["with"]["trials"][0]["agent_error"] = True
        self.assertEqual(validate.validate_report(r), [], "a solved trial whose agent then errored is still a solved trial")

    def test_intake_block_only_on_stored_reports(self):
        r = fixture("install.json")
        self.assertIn("intake: missing on a stored report", validate.validate_report(r, stored=True))
        r["intake"] = {"issue": 1, "submitter": "octocat", "accepted_at": "2026-10-20T00:00:00Z", "weight": 1.0, "flags": []}
        self.assertEqual(validate.validate_report(r, stored=True), [])
        self.assertIn("intake: is added by the intake workflow and must not be submitted", validate.validate_report(r))

    def test_non_object(self):
        self.assertEqual(validate.validate_report([1, 2]), ["$: report must be a JSON object"])

    def test_repo_pattern(self):
        r = fixture("install.json")
        r["subject"]["repo"] = "https://github.com/iamkun/dayjs"
        self.assertTrue(any(e.startswith("subject/repo") for e in validate.validate_report(r)))


if __name__ == "__main__":
    unittest.main()
