from __future__ import annotations

import unittest

from shushunya_desktop.main import _display_profile


class DisplayProfileTests(unittest.TestCase):
    def test_specific_screen_overrides_defaults(self) -> None:
        payload = {
            "defaults": {"scale_multiplier": 0.95, "extra_safe_bottom": 4},
            "profiles": {"HDMI-A-1|MMF-270": {"extra_safe_bottom": 12}},
        }
        profile = _display_profile("HDMI-A-1|MMF-270", payload)
        self.assertEqual(profile["scale_multiplier"], 0.95)
        self.assertEqual(profile["extra_safe_bottom"], 12.0)

    def test_unsafe_values_are_clamped(self) -> None:
        payload = {
            "defaults": {
                "scale_multiplier": 8,
                "extra_safe_left": -20,
                "extra_safe_bottom": 900,
            }
        }
        profile = _display_profile("unknown", payload)
        self.assertEqual(profile["scale_multiplier"], 1.3)
        self.assertEqual(profile["extra_safe_left"], 0.0)
        self.assertEqual(profile["extra_safe_bottom"], 160.0)


if __name__ == "__main__":
    unittest.main()
