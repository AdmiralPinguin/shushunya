import unittest

from shushunya_desktop.screen_roles import ScreenDescriptor, assign_roles


class ScreenRoleTests(unittest.TestCase):
    def test_current_landscape_and_portrait_layout(self) -> None:
        screens = [
            ScreenDescriptor("HDMI-A-1|MMF-270", "HDMI-A-1", 1920, 1080, True),
            ScreenDescriptor("HDMI-A-2|BIT-B2477IPS", "HDMI-A-2", 1080, 1920),
        ]
        self.assertEqual(
            {
                "HDMI-A-1|MMF-270": "presence",
                "HDMI-A-2|BIT-B2477IPS": "mind",
            },
            assign_roles(screens),
        )

    def test_third_display_becomes_canvas(self) -> None:
        screens = [
            ScreenDescriptor("main", "HDMI-A-1", 1920, 1080, True),
            ScreenDescriptor("portrait", "HDMI-A-2", 1080, 1920),
            ScreenDescriptor("third", "DP-1", 2560, 1440),
        ]
        roles = assign_roles(screens)
        self.assertEqual("presence", roles["main"])
        self.assertEqual("mind", roles["portrait"])
        self.assertEqual("canvas", roles["third"])

    def test_persisted_roles_follow_identity_not_order(self) -> None:
        screens = [
            ScreenDescriptor("right", "DP-2", 1920, 1080),
            ScreenDescriptor("left", "DP-1", 1920, 1080, True),
        ]
        roles = assign_roles(screens, {"right": "canvas", "left": "presence"})
        self.assertEqual("canvas", roles["right"])
        self.assertEqual("presence", roles["left"])

    def test_legacy_machine_roles_are_migrated(self) -> None:
        screens = [
            ScreenDescriptor("portrait", "HDMI-A-2", 1080, 1920),
            ScreenDescriptor("third", "DP-1", 1920, 1080),
        ]
        roles = assign_roles(screens, {"portrait": "operations", "third": "archive"})
        self.assertEqual("mind", roles["portrait"])
        self.assertEqual("canvas", roles["third"])


if __name__ == "__main__":
    unittest.main()
