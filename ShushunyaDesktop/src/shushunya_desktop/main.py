from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QScreen, QWindow
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow

from .backend import AppBackend
from .companion import CoreCompanionProvider, DemoCompanionProvider
from .demo_state import DEMO_STATES
from .screen_roles import ScreenDescriptor, assign_roles


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QML_FILE = PROJECT_ROOT / "qml" / "ScreenWindow.qml"
RUNTIME_DIR = PROJECT_ROOT / "runtime"


def _parse_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT") from exc
    if width < 640 or height < 480:
        raise argparse.ArgumentTypeError("preview size is too small")
    return width, height


def _screen_key(screen: QScreen) -> str:
    identity = screen.model().strip() or screen.serialNumber().strip() or "unknown"
    return f"{screen.name()}|{identity}"


def _descriptor(screen: QScreen, primary: QScreen | None) -> ScreenDescriptor:
    geometry = screen.geometry()
    return ScreenDescriptor(
        key=_screen_key(screen),
        name=screen.name(),
        width=geometry.width(),
        height=geometry.height(),
        primary=screen is primary,
        manufacturer=screen.manufacturer(),
        model=screen.model(),
    )


def _rect_payload(rect) -> dict[str, int]:
    return {"x": rect.x(), "y": rect.y(), "width": rect.width(), "height": rect.height()}


def _target_window_properties(rect) -> dict[str, int]:
    """Return the full virtual-desktop geometry required before native creation."""

    return _rect_payload(rect)


def _quick_window(window) -> QQuickWindow:
    pointer = shiboken6.getCppPointer(window)[0]
    return shiboken6.wrapInstance(pointer, QQuickWindow)


def _load_persisted_roles() -> dict[str, str]:
    path = RUNTIME_DIR / "screen_roles.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    roles = payload.get("roles") if isinstance(payload, dict) else None
    return roles if isinstance(roles, dict) else {}


def _load_display_profiles() -> dict[str, object]:
    for path in (
        RUNTIME_DIR / "display_profiles.json",
        PROJECT_ROOT / "config" / "display_profiles.example.json",
    ):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _display_profile(screen_key: str, payload: dict[str, object]) -> dict[str, float]:
    names = (
        "scale_multiplier",
        "extra_safe_left",
        "extra_safe_right",
        "extra_safe_top",
        "extra_safe_bottom",
    )
    result = {name: 1.0 if name == "scale_multiplier" else 0.0 for name in names}
    defaults = payload.get("defaults")
    profiles = payload.get("profiles")
    candidates = [defaults]
    if isinstance(profiles, dict):
        candidates.append(profiles.get(screen_key))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for name in names:
            value = candidate.get(name)
            if isinstance(value, (int, float)):
                result[name] = float(value)
    result["scale_multiplier"] = max(0.7, min(1.3, result["scale_multiplier"]))
    for name in names[1:]:
        result[name] = max(0.0, min(160.0, result[name]))
    return result


class WindowManager(QObject):
    def __init__(self, app: QGuiApplication, backend: AppBackend) -> None:
        super().__init__()
        self.app = app
        self.backend = backend
        self.engine = QQmlEngine(self)
        self.engine.rootContext().setContextProperty("backend", backend)
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(QML_FILE)))
        if self.component.isError():
            raise RuntimeError("\n".join(error.toString() for error in self.component.errors()))
        self.windows: dict[str, object] = {}
        self.expected_screens: dict[str, dict[str, object]] = {}
        diagnostics_value = os.environ.get("SHUSHUNYA_DIAGNOSTICS_DIR", "").strip()
        self.diagnostics_dir = Path(diagnostics_value) if diagnostics_value else None
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(300)
        self._rebuild_timer.timeout.connect(self.rebuild)
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setInterval(2400)
        self._capture_timer.timeout.connect(self.capture_live)
        backend.snapshotRequested.connect(self.capture_live)
        app.screenAdded.connect(lambda _screen: self._rebuild_timer.start())
        app.screenRemoved.connect(lambda _screen: self._rebuild_timer.start())

    def _create(self, properties: dict[str, object], screen: QScreen | None = None):
        if screen is None:
            window = self.component.createWithInitialProperties(properties)
        else:
            # QML Window.screen cannot be initialized from a Python QScreen
            # through QVariant. Split creation so both the target screen and
            # its compositor-provided global geometry are set before Qt
            # creates the native surface. QWindow::create() reselects a screen
            # from the current geometry; leaving every window at (0, 0) sends
            # every Wayland fullscreen request to the primary output.
            target = screen.geometry()
            initial_properties = dict(properties)
            initial_properties.update(_target_window_properties(target))
            window = self.component.beginCreate(self.engine.rootContext())
            if window is not None:
                try:
                    window.setScreen(screen)
                    self.component.setInitialProperties(window, initial_properties)
                    window.setGeometry(target)
                finally:
                    self.component.completeCreate()
        if window is None:
            raise RuntimeError("\n".join(error.toString() for error in self.component.errors()))
        return window

    def rebuild(self) -> None:
        for window in self.windows.values():
            window.close()
            window.deleteLater()
        self.windows.clear()
        self.expected_screens.clear()

        screens = list(self.app.screens())
        descriptors = [_descriptor(screen, self.app.primaryScreen()) for screen in screens]
        roles = assign_roles(descriptors, _load_persisted_roles())
        display_profiles = _load_display_profiles()
        for index, (screen, descriptor) in enumerate(zip(screens, descriptors, strict=True)):
            role = roles[descriptor.key]
            profile = _display_profile(descriptor.key, display_profiles)
            target = screen.geometry()
            virtual = screen.virtualGeometry()
            window = self._create(
                {
                    "width": descriptor.width,
                    "height": descriptor.height,
                    "screenRole": role,
                    "screenKey": descriptor.key,
                    "screenLabel": f"{descriptor.name} · {descriptor.width}×{descriptor.height}",
                    "screenOrdinal": index,
                    "displayCount": len(screens),
                    "previewMode": False,
                    "screenOriginX": target.x(),
                    "screenOriginY": target.y(),
                    "virtualOriginX": virtual.x(),
                    "virtualOriginY": virtual.y(),
                    "virtualDesktopWidth": virtual.width(),
                    "virtualDesktopHeight": virtual.height(),
                    "scaleMultiplier": profile["scale_multiplier"],
                    "extraSafeLeft": profile["extra_safe_left"],
                    "extraSafeRight": profile["extra_safe_right"],
                    "extraSafeTop": profile["extra_safe_top"],
                    "extraSafeBottom": profile["extra_safe_bottom"],
                },
                screen=screen,
            )
            window.setScreen(screen)
            window.setGeometry(target)
            self.expected_screens[descriptor.key] = {
                "key": descriptor.key,
                "name": descriptor.name,
                "geometry": _rect_payload(target),
            }
            print(
                f"[placement] {role} -> {descriptor.name} "
                f"{target.width()}x{target.height()}@{target.x()},{target.y()}"
            )
            window.showFullScreen()
            self.windows[descriptor.key] = window
        if self.diagnostics_dir is not None:
            self._capture_timer.start()

    def capture_live(self) -> None:
        if self.diagnostics_dir is None:
            return
        try:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"failed to create diagnostics directory: {exc}", file=sys.stderr)
            return

        payload: dict[str, object] = {
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "platform": self.app.platformName(),
            "placement_ok": True,
            "screens": [],
        }
        screen_payloads: list[dict[str, object]] = []
        for screen_key, window in self.windows.items():
            screen = window.screen()
            expected = self.expected_screens.get(screen_key, {})
            role = str(window.property("screenRole") or "unknown")
            screen_name = screen.name() if screen is not None else "unknown"
            expected_name = str(expected.get("name") or "unknown")
            expected_geometry = expected.get("geometry")
            expected_width = (
                int(expected_geometry.get("width", -1))
                if isinstance(expected_geometry, dict)
                else -1
            )
            expected_height = (
                int(expected_geometry.get("height", -1))
                if isinstance(expected_geometry, dict)
                else -1
            )
            screen_match = screen_name == expected_name
            size_match = window.width() == expected_width and window.height() == expected_height
            fullscreen = window.visibility() == QWindow.Visibility.FullScreen
            placement_ok = screen_match and size_match and fullscreen
            payload["placement_ok"] = bool(payload["placement_ok"] and placement_ok)
            safe_name = "".join(
                character if character.isalnum() or character in "-_" else "_"
                for character in f"{role}-{screen_name}"
            )
            image_path = self.diagnostics_dir / f"{safe_name}.png"
            image = _quick_window(window).grabWindow()
            image_saved = not image.isNull() and image.save(str(image_path))

            item: dict[str, object] = {
                "key": screen_key,
                "role": role,
                "expected_screen_name": expected_name,
                "screen_name": screen_name,
                "placement_ok": placement_ok,
                "screen_match": screen_match,
                "size_match": size_match,
                "fullscreen": fullscreen,
                "expected_geometry": expected_geometry,
                "window_geometry": _rect_payload(window.geometry()),
                "window_size": {"width": window.width(), "height": window.height()},
                "window_dpr": window.devicePixelRatio(),
                "visibility": str(window.visibility()).split(".")[-1],
                "capture": str(image_path) if image_saved else None,
                "layout": {
                    "safe_left": window.property("safeLeft"),
                    "safe_right": window.property("safeRight"),
                    "safe_top": window.property("safeTop"),
                    "safe_bottom": window.property("safeBottom"),
                    "design_width": window.property("designWidth"),
                    "design_height": window.property("designHeight"),
                    "content_scale": window.property("contentScale"),
                    "scale_multiplier": window.property("scaleMultiplier"),
                },
            }
            if screen is not None:
                item.update(
                    {
                        "manufacturer": screen.manufacturer(),
                        "model": screen.model(),
                        "serial": screen.serialNumber(),
                        "screen_geometry": _rect_payload(screen.geometry()),
                        "available_geometry": _rect_payload(screen.availableGeometry()),
                        "virtual_geometry": _rect_payload(screen.virtualGeometry()),
                        "screen_dpr": screen.devicePixelRatio(),
                        "orientation": str(screen.orientation()).split(".")[-1],
                    }
                )
            screen_payloads.append(item)

        payload["screens"] = screen_payloads
        temp_path = self.diagnostics_dir / "layout.json.tmp"
        target_path = self.diagnostics_dir / "layout.json"
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(target_path)
            status = "OK" if payload["placement_ok"] else "FAILED"
            stream = sys.stdout if payload["placement_ok"] else sys.stderr
            print(f"[placement] {status}: {target_path}", file=stream)
        except OSError as exc:
            print(f"failed to save diagnostics: {exc}", file=sys.stderr)

    def shutdown(self) -> None:
        for window in self.windows.values():
            window.close()
            window.deleteLater()
        self.windows.clear()
        self.expected_screens.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.engine.clearComponentCache()

    def preview(self, role: str, size: tuple[int, int], capture: Path | None) -> None:
        width, height = size
        window = self._create(
            {
                "width": width,
                "height": height,
                "screenRole": role,
                "screenKey": f"preview|{role}",
                "screenLabel": f"PREVIEW · {width}×{height}",
                "screenOrdinal": 0,
                "previewMode": True,
                "screenOriginX": 0,
                "screenOriginY": 0,
                "virtualOriginX": 0,
                "virtualOriginY": 0,
                "virtualDesktopWidth": width,
                "virtualDesktopHeight": height,
            }
        )
        window.setWidth(width)
        window.setHeight(height)
        window.show()
        self.windows[role] = window
        if capture is not None:
            capture.parent.mkdir(parents=True, exist_ok=True)

            def save() -> None:
                image = _quick_window(window).grabWindow()
                if image.isNull() or not image.save(str(capture)):
                    print(f"failed to capture {capture}", file=sys.stderr)
                    self.app.exit(2)
                    return
                print(capture)
                self.app.quit()

            QTimer.singleShot(1800, save)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shushunya fullscreen visual shell")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--demo",
        action="store_true",
        help="run the disconnected visual prototype (the default)",
    )
    mode.add_argument(
        "--core",
        action="store_true",
        help="read live state from Shushunya Core instead of the visual demo",
    )
    parser.add_argument(
        "--preview-role",
        choices=("presence", "mind", "canvas", "ambient"),
        help="open one role as a normal preview window",
    )
    parser.add_argument("--preview-size", type=_parse_size, default=(1920, 1080))
    parser.add_argument("--capture", type=Path, help="save preview screenshot and exit")
    parser.add_argument(
        "--demo-state",
        choices=DEMO_STATES,
        help="start or capture one exact visual state",
    )
    parser.add_argument(
        "--no-demo-cycle",
        action="store_true",
        help="keep the selected demo state instead of advancing automatically",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    os.environ.setdefault("QSG_RHI_BACKEND", "opengl")
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Shushunya Desktop")
    app.setOrganizationName("Shushunya")

    preview_scenario = os.environ.get("SHUSHUNYA_PREVIEW_SCENARIO", "demo")
    if args.preview_role:
        demo_mode = args.demo_state is not None
        provider = DemoCompanionProvider(preview_scenario)
    else:
        demo_mode = not args.core
        provider = (
            DemoCompanionProvider("demo")
            if demo_mode
            else CoreCompanionProvider(
                os.environ.get("SHUSHUNYA_CORE_URL", "http://127.0.0.1:7600")
            )
        )
    initial_demo_state = (
        args.demo_state
        or os.environ.get("SHUSHUNYA_DEMO_STATE", "attention")
    )
    if initial_demo_state not in DEMO_STATES:
        initial_demo_state = "attention"
    backend = AppBackend(
        provider,
        demo_mode=demo_mode,
        initial_demo_state=initial_demo_state,
        demo_cycle=demo_mode and not args.preview_role and not args.no_demo_cycle,
    )
    backend.quitRequested.connect(app.quit)
    manager = WindowManager(app, backend)
    if args.preview_role:
        manager.preview(args.preview_role, args.preview_size, args.capture)
    else:
        manager.rebuild()
    exit_code = app.exec()
    manager.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
