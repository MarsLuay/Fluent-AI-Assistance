import unittest
from fluent_pipeline.api_v2.runtime import MockRuntimeController

class RuntimeLoginTests(unittest.TestCase):
    def test_validate_user_correct_password(self):
        runtime = MockRuntimeController(resolved_expressions={"user:admin": "secret123"})
        self.assertTrue(runtime.validate_user("admin", "secret123"))

    def test_validate_user_incorrect_password(self):
        runtime = MockRuntimeController(resolved_expressions={"user:admin": "secret123"})
        self.assertFalse(runtime.validate_user("admin", "wrong_password"))

    def test_validate_user_unknown_user(self):
        runtime = MockRuntimeController(resolved_expressions={"user:admin": "secret123"})
        self.assertFalse(runtime.validate_user("unknown_user", "any_password"))

if __name__ == "__main__":
    unittest.main()
