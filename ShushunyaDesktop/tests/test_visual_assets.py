import unittest
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImageReader


ROOT = Path(__file__).resolve().parents[1]
HERESY_ASSETS = (
    "horned-skull.svg",
    "fractured-chaos-seal.svg",
    "warp-eye.svg",
    "broken-halo-rune.svg",
    "chaos-star.svg",
    "horus-eye.svg",
)


class VisualAssetTests(unittest.TestCase):
    def test_vector_heresy_pack_exists_and_renders(self) -> None:
        asset_root = ROOT / "assets" / "heresy"

        for filename in HERESY_ASSETS:
            with self.subTest(asset=filename):
                path = asset_root / filename
                self.assertTrue(path.is_file(), f"Missing vector asset: {path}")

                reader = QImageReader(str(path))
                self.assertTrue(
                    reader.canRead(),
                    f"Qt cannot decode {filename}: {reader.errorString()}",
                )
                reader.setScaledSize(QSize(256, 256))
                image = reader.read()
                self.assertFalse(
                    image.isNull(),
                    f"Qt failed to render {filename}: {reader.errorString()}",
                )
                self.assertEqual(QSize(256, 256), image.size())
                self.assertTrue(image.hasAlphaChannel())

    def test_active_qml_uses_the_sanctum_but_contains_no_cat_art(self) -> None:
        active_paths = [ROOT / "qml" / "ScreenWindow.qml"]
        active_paths.extend(sorted((ROOT / "qml" / "views").glob("*.qml")))
        active_paths.extend(sorted((ROOT / "qml" / "components").glob("*.qml")))
        active = "\n".join(path.read_text(encoding="utf-8") for path in active_paths)
        active_lower = active.lower()

        self.assertIn("assets/heresy", active_lower)
        self.assertIn("chaos-sanctum-panorama.png", active_lower)
        self.assertEqual(1, active_lower.count("chaos-sanctum-panorama.png"))
        panorama = ROOT / "assets" / "chaos-sanctum-panorama.png"
        self.assertTrue(panorama.is_file(), f"Missing live panorama: {panorama}")
        panorama_reader = QImageReader(str(panorama))
        self.assertTrue(
            panorama_reader.canRead(),
            f"Qt cannot decode live panorama: {panorama_reader.errorString()}",
        )
        for retired_cat_asset in (
            "shushunya-presence-v2",
            "shushunya-mind-v2",
            "shushunya-hero",
        ):
            with self.subTest(retired_cat_asset=retired_cat_asset):
                self.assertNotIn(retired_cat_asset, active_lower)

    def test_living_seal_restores_the_old_body_without_icon_orbit(self) -> None:
        source = (ROOT / "qml" / "components" / "LivingSeal.qml").read_text(
            encoding="utf-8"
        )

        self.assertIn("fractured-chaos-seal.svg", source)
        self.assertIn("horus-eye.svg", source)
        self.assertIn("broken-halo-rune.svg", source)
        self.assertEqual(4, source.count("Image {"))
        self.assertEqual(2, source.count("SequentialAnimation on rotation"))
        self.assertNotIn("Repeater", source)
        self.assertNotIn("horned-skull.svg", source)
        self.assertNotIn("warp-eye.svg", source)

    def test_active_views_are_scenes_not_dashboard_cards(self) -> None:
        screen = (ROOT / "qml" / "ScreenWindow.qml").read_text(encoding="utf-8")
        views = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "qml" / "views").glob("*.qml"))
        )

        self.assertIn("LivingEnvironment", screen)
        self.assertNotIn("HeresyField {", screen)
        self.assertIn("IncisedText", views)
        self.assertNotIn("RitualInscription", views)
        self.assertNotIn("QuietHeader", views)
        self.assertNotIn("ScrollBar", views)

    def test_global_virtual_desktop_coordinates_reach_the_scene(self) -> None:
        screen = (ROOT / "qml" / "ScreenWindow.qml").read_text(encoding="utf-8")
        main = (ROOT / "src" / "shushunya_desktop" / "main.py").read_text(
            encoding="utf-8"
        )

        for name in (
            "screenOriginX",
            "screenOriginY",
            "virtualOriginX",
            "virtualOriginY",
            "virtualDesktopWidth",
            "virtualDesktopHeight",
        ):
            with self.subTest(property=name):
                self.assertIn(name, screen)
                self.assertIn(f'"{name}"', main)


if __name__ == "__main__":
    unittest.main()
