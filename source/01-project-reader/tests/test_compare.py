import unittest
from unittest.mock import patch

from tecan_reader.compare import compare_archives


class TestCompareArchives(unittest.TestCase):
    @patch("tecan_reader.compare.inspect_archive")
    def test_compare_archives_logic(self, mock_inspect_archive):
        # Setup mock behavior
        def side_effect(archive_path, script_limit, object_limit):
            if str(archive_path) == "left.zip":
                return {
                    "script_names": ["A", "B"],
                    "entry_count": 10,
                    "extension_counts": {"xml": 5},
                    "family_counts": {"f1": 2},
                    "warning_counts": {"w1": 1},
                }
            if str(archive_path) == "right.zip":
                return {
                    "script_names": ["B", "C"],
                    "entry_count": 12,
                    "extension_counts": {"xml": 6},
                    "family_counts": {"f1": 3},
                    "warning_counts": {"w2": 1},
                }
            return {}

        mock_inspect_archive.side_effect = side_effect

        # Call the function
        result = compare_archives("left.zip", "right.zip")

        # Assert results
        self.assertEqual(result["kind"], "zeia_compare")
        self.assertEqual(result["left"], "left.zip")
        self.assertEqual(result["right"], "right.zip")

        self.assertEqual(result["script_names_added"], ["C"])
        self.assertEqual(result["script_names_removed"], ["A"])
        self.assertEqual(result["script_names_common_count"], 1)

        self.assertEqual(result["left_entry_count"], 10)
        self.assertEqual(result["right_entry_count"], 12)

        self.assertEqual(result["left_extension_counts"], {"xml": 5})
        self.assertEqual(result["right_extension_counts"], {"xml": 6})

        self.assertEqual(result["left_family_counts"], {"f1": 2})
        self.assertEqual(result["right_family_counts"], {"f1": 3})

        self.assertEqual(result["left_warning_counts"], {"w1": 1})
        self.assertEqual(result["right_warning_counts"], {"w2": 1})

    @patch("tecan_reader.compare.inspect_archive")
    def test_compare_archives_arguments(self, mock_inspect_archive):
        # Setup mock behavior to return dummy dicts to prevent KeyErrors
        mock_inspect_archive.return_value = {
            "script_names": [],
            "entry_count": 0,
            "extension_counts": {},
            "family_counts": {},
            "warning_counts": {},
        }

        # Call the function with script_limit
        compare_archives("left.zip", "right.zip", script_limit=5)

        # Assert arguments passed correctly
        mock_inspect_archive.assert_any_call("left.zip", script_limit=5, object_limit=0)
        mock_inspect_archive.assert_any_call("right.zip", script_limit=5, object_limit=0)

if __name__ == "__main__":
    unittest.main()
