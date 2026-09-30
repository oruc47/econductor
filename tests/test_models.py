import unittest
from pathlib import Path
from unittest.mock import patch

from econductor.models import PRESETS, model_rows


class ModelFitTests(unittest.TestCase):
    def rows_for(self, ram_gb: int, free_disk_gb: int):
        with (
            patch("econductor.models.registry", return_value={}),
            patch("econductor.models.local_model", return_value=None),
        ):
            return {
                row["id"]: row
                for row in model_rows(
                    {
                        "memory_bytes": ram_gb * 1024**3,
                        "free_disk_bytes": free_disk_gb * 1000**3,
                    }
                )
            }

    def test_36_gb_mac_shows_small_models_and_large_limits(self):
        rows = self.rows_for(36, 200)
        self.assertEqual(rows["light"]["memory_fit"], "comfortable")
        self.assertEqual(rows["large"]["memory_fit"], "tight")
        self.assertEqual(rows["moe"]["memory_fit"], "tight")
        self.assertEqual(rows["frontier"]["memory_fit"], "below minimum")
        self.assertEqual(set(PRESETS), set(rows))

    def test_128_gb_mac_labels_top_option_experimental_and_disk_limit(self):
        rows = self.rows_for(128, 80)
        self.assertEqual(rows["frontier"]["memory_fit"], "comfortable")
        self.assertEqual(rows["max"]["memory_fit"], "tight/experimental")
        self.assertEqual(rows["max"]["disk_fit"], "low disk")

    def test_readme_lists_every_preset(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        for preset in PRESETS:
            self.assertIn(f"| `{preset}` |", readme)


if __name__ == "__main__":
    unittest.main()
