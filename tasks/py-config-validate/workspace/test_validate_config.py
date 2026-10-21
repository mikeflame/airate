import unittest

from validate_config import validate_config

VALID_FAST = {"name": "api", "port": 8080, "mode": "fast"}
VALID_SAFE = {"name": "api", "port": 8080, "mode": "safe", "db": {"host": "db.internal", "port": 5432}, "tags": ["blue", "green"], "retries": 3}


class ValidateConfigTests(unittest.TestCase):
    def assert_error_mentions(self, config, path):
        errors = validate_config(config)
        self.assertTrue(errors, f"expected an error mentioning {path!r}")
        self.assertTrue(any(path in e for e in errors), f"no error mentions {path!r}: {errors}")

    def test_valid_configurations(self):
        self.assertEqual(validate_config(VALID_FAST), [])
        self.assertEqual(validate_config(VALID_SAFE), [])
        self.assertEqual(validate_config({**VALID_FAST, "db": {"host": "x", "port": 1}, "tags": [], "retries": 0}), [])

    def test_not_an_object(self):
        self.assert_error_mentions([], "$")
        self.assert_error_mentions("api", "$")

    def test_name(self):
        self.assert_error_mentions({"port": 80, "mode": "fast"}, "name")
        self.assert_error_mentions({**VALID_FAST, "name": ""}, "name")
        self.assert_error_mentions({**VALID_FAST, "name": 7}, "name")

    def test_port(self):
        self.assert_error_mentions({"name": "api", "mode": "fast"}, "port")
        self.assert_error_mentions({**VALID_FAST, "port": 0}, "port")
        self.assert_error_mentions({**VALID_FAST, "port": 65536}, "port")
        self.assert_error_mentions({**VALID_FAST, "port": "8080"}, "port")
        self.assert_error_mentions({**VALID_FAST, "port": True}, "port")

    def test_mode(self):
        self.assert_error_mentions({"name": "api", "port": 80}, "mode")
        self.assert_error_mentions({**VALID_FAST, "mode": "turbo"}, "mode")

    def test_tags(self):
        self.assert_error_mentions({**VALID_FAST, "tags": "blue"}, "tags")
        self.assert_error_mentions({**VALID_FAST, "tags": ["blue", 2]}, "tags[1]")
        self.assert_error_mentions({**VALID_FAST, "tags": ["blue", ""]}, "tags[1]")
        self.assert_error_mentions({**VALID_FAST, "tags": ["blue", "blue"]}, "tags")

    def test_retries(self):
        self.assert_error_mentions({**VALID_FAST, "retries": -1}, "retries")
        self.assert_error_mentions({**VALID_FAST, "retries": 11}, "retries")
        self.assert_error_mentions({**VALID_FAST, "retries": 2.5}, "retries")

    def test_db(self):
        self.assert_error_mentions({"name": "api", "port": 80, "mode": "safe"}, "db")
        self.assert_error_mentions({**VALID_SAFE, "db": {"port": 5432}}, "db.host")
        self.assert_error_mentions({**VALID_SAFE, "db": {"host": "", "port": 5432}}, "db.host")
        self.assert_error_mentions({**VALID_SAFE, "db": {"host": "h", "port": 70000}}, "db.port")
        self.assert_error_mentions({**VALID_SAFE, "db": "db.internal:5432"}, "db")
        self.assert_error_mentions({**VALID_FAST, "db": {"host": "h"}}, "db.port")

    def test_unknown_keys(self):
        self.assert_error_mentions({**VALID_FAST, "colour": "blue"}, "colour")

    def test_invalid_types_never_raise(self):
        cases = {
            "name": [None, [], {}, 7, True],
            "port": [None, [], {}, "8080", 8.0],
            "mode": [None, [], {}, 1, True, "FAST"],
            "tags": [None, {}, "blue", 1, [None], [["blue"]], [{"x": 1}]],
            "retries": [None, [], {}, "3", True, 2.5],
            "db": [None, [], "db.internal:5432", 1, {"host": None, "port": None}, {"host": [], "port": {}}],
        }
        for field, values in cases.items():
            for value in values:
                config = {**VALID_SAFE, field: value}
                with self.subTest(field=field, value=value):
                    try:
                        errors = validate_config(config)
                    except Exception as exc:  # noqa: BLE001
                        self.fail(f"validate_config raised {type(exc).__name__} for {field}={value!r}")
                    self.assertIsInstance(errors, list)
                    self.assertTrue(errors and all(isinstance(e, str) for e in errors), f"no error for {field}={value!r}")
                    self.assertTrue(any(field in e for e in errors), f"no error mentions {field!r}: {errors}")

    def test_mode_type_decides_db_requirement_without_raising(self):
        self.assert_error_mentions({"name": "api", "port": 80, "mode": []}, "mode")
        self.assert_error_mentions({"name": "api", "port": 80, "mode": {}}, "mode")


if __name__ == "__main__":
    unittest.main()
