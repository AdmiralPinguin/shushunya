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

    def test_active_qml_is_vector_only_and_contains_no_cat_art(self) -> None:
        active_paths = [ROOT / "qml" / "ScreenWindow.qml"]
        active_paths.extend(sorted((ROOT / "qml" / "views").glob("*.qml")))
        active_paths.extend(sorted((ROOT / "qml" / "components").glob("*.qml")))
        active = "\n".join(path.read_text(encoding="utf-8") for path in active_paths)
        active_lower = active.lower()

        self.assertIn("assets/heresy", active_lower)
        self.assertNotIn(".png", active_lower)
        for retired_asset in (
            "shushunya-presence-v2",
            "shushunya-mind-v2",
            "shushunya-hero",
            "chaos-sanctum-panorama",
        ):
            with self.subTest(retired_asset=retired_asset):
                self.assertNotIn(retired_asset, active_lower)

    def test_living_seal_is_the_canonical_two_layer_mark(self) -> None:
        source = (ROOT / "qml" / "components" / "LivingSeal.qml").read_text(
            encoding="utf-8"
        )

        self.assertIn("chaos-star.svg", source)
        self.assertIn("horus-eye.svg", source)
        self.assertEqual(2, source.count("Image {"))
        self.assertEqual(1, source.count("RotationAnimator"))
        self.assertNotIn("Repeater", source)
        self.assertNotIn("horned-skull.svg", source)
        self.assertNotIn("broken-halo-rune.svg", source)
        self.assertNotIn("fractured-chaos-seal.svg", source)
        self.assertNotIn("warp-eye.svg", source)


if __name__ == "__main__":
    unittest.main()
