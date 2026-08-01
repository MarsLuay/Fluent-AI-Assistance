import unittest

from fluent_pipeline.policies.prompt_text import (
    MEDIA_PLACEHOLDER_BEGIN,
    MEDIA_PLACEHOLDER_END,
    PROMPT_PLACEHOLDER_TOKENS,
    normalize_operator_prompt_text,
    prompt_has_media_boilerplate,
    prompt_text_is_placeholder,
    strip_media_placeholder,
)


class PromptTextPolicyTests(unittest.TestCase):
    def test_strip_media_placeholder_removes_marker_and_trailing_text(self):
        prompt = f"Confirm deck state. {MEDIA_PLACEHOLDER_BEGIN} attach image here{MEDIA_PLACEHOLDER_END}"

        self.assertEqual(strip_media_placeholder(prompt), "Confirm deck state.")

    def test_normalize_operator_prompt_text_removes_media_boilerplate(self):
        prompt = (
            "Reference images and videos for this prompt will be attached later. "
            "Confirm deck state."
        )

        self.assertEqual(normalize_operator_prompt_text(prompt), "Confirm deck state.")
        self.assertTrue(prompt_has_media_boilerplate(prompt))

    def test_prompt_text_is_placeholder_uses_shared_token_list(self):
        for text in ("TODO", "n/a", "<fill in>", "..."):
            with self.subTest(text=text):
                self.assertTrue(prompt_text_is_placeholder(text))

        self.assertIn("fill me in", PROMPT_PLACEHOLDER_TOKENS)
        self.assertFalse(prompt_text_is_placeholder("Confirm deck state."))

