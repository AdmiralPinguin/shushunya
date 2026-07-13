import unittest

from PySide6.QtCore import QRect

from shushunya_desktop.main import _target_window_properties


class WindowPlacementTests(unittest.TestCase):
    def test_target_geometry_keeps_virtual_desktop_offset(self) -> None:
        self.assertEqual(
            _target_window_properties(QRect(1920, 0, 1080, 1920)),
            {"x": 1920, "y": 0, "width": 1080, "height": 1920},
        )

    def test_target_geometry_supports_screens_above_and_left(self) -> None:
        self.assertEqual(
            _target_window_properties(QRect(-2560, -1440, 2560, 1440)),
            {"x": -2560, "y": -1440, "width": 2560, "height": 1440},
        )


if __name__ == "__main__":
    unittest.main()
