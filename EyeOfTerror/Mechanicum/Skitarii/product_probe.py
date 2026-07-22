"""Product probe — the brigade runs the PRODUCT, not only the checks.

Acceptance checks prove commands exit green; they proved a game «works» whose
screen stayed pitch black until a tap nobody knew to make. This module collects
EVIDENCE of the running product for the quality critic:

  - cli:     run the declared commands inside the sandbox VM, capture output;
  - server:  start it in the background (VM), hit the declared endpoints, stop;
  - android: pull the built APK to the host, install it on a warm headless
             emulator, launch, inject input, take screenshots, uninstall.

The fighter cannot influence the probe: it is driven by the service (host side)
against the delivered workspace, the same trust model as the acceptor.
Every runner is bounded by timeouts and returns partial evidence on failure —
a broken probe degrades the critique, never the mission.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

ANDROID_TOOLS = os.environ.get(
    "SKITARII_ANDROID_TOOLS",
    "/media/shushunya/SHUSHUNYA/shushunya/android-tools",
)
_SDK = f"{ANDROID_TOOLS}/android-sdk"
_ADB = f"{_SDK}/platform-tools/adb"
_EMULATOR = f"{_SDK}/emulator/emulator"
_JAVA_HOME = f"{ANDROID_TOOLS}/jdk21"
_AVD_NAME = os.environ.get("SKITARII_PROBE_AVD", "skitarii-probe")
_AVD_HOME = os.environ.get(
    "SKITARII_PROBE_AVD_HOME",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "avd"),
)
_EMULATOR_LOCK = os.path.join(_AVD_HOME, "emulator.lock")
_EMULATOR_SERIAL = os.environ.get("SKITARII_PROBE_EMULATOR_SERIAL", "emulator-5606")
_EMULATOR_PORT = _EMULATOR_SERIAL.rsplit("-", 1)[-1]
_BOOT_TIMEOUT_SEC = int(os.environ.get("SKITARII_PROBE_BOOT_TIMEOUT_SEC", "240"))
_PULL_CHUNK_BYTES = 3_000_000
_MAX_TEXT_EVIDENCE = 4_000
_MAX_SCREENS = 4

_PROFILE_KINDS = ("android", "server", "cli", "web", "library", "none")


def normalize_profile(raw: Any, goal: str, deliverables: list[str]) -> dict[str, Any]:
    """Validated product profile: the spec LLM declares, mechanics double-check."""
    profile = dict(raw) if isinstance(raw, dict) else {}
    kind = str(profile.get("kind") or "").strip().lower()
    hay = (goal + "\n" + "\n".join(deliverables)).lower()
    if kind not in _PROFILE_KINDS:
        kind = ""
    if not kind:
        if any(s in hay for s in ("androidmanifest", "gradlew", "build.gradle", "apk")):
            kind = "android"
        elif any(s in hay for s in ("сервер", "server", "endpoint", "flask", "fastapi", "экспресс", "express")):
            kind = "server"
        elif deliverables:
            kind = "cli"
        else:
            kind = "none"
    run = [str(c) for c in (profile.get("run") or []) if isinstance(c, str) and c.strip()][:4]
    endpoints = [str(e) for e in (profile.get("endpoints") or []) if isinstance(e, str)][:6]
    start = str(profile.get("start") or "").strip()
    return {"kind": kind, "run": run, "endpoints": endpoints, "start": start}


def _clip(text: Any) -> str:
    return str(text or "")[:_MAX_TEXT_EVIDENCE]


# --- VM-side runners (delivered code executes only inside the sandbox) ---

def _probe_cli(ex: Any, profile: dict[str, Any]) -> dict[str, Any]:
    texts: list[str] = []
    for cmd in profile.get("run") or []:
        res = ex.bash(cmd, timeout=120)
        texts.append(
            f"$ {cmd}\n[exit {res.get('returncode')}]\n"
            f"{_clip(res.get('stdout'))}\n{_clip(res.get('stderr'))}"
        )
    return {"texts": texts, "screens": [], "facts": {"runs": len(texts)}}


def _probe_server(ex: Any, profile: dict[str, Any]) -> dict[str, Any]:
    texts: list[str] = []
    facts: dict[str, Any] = {"started": False}
    start = profile.get("start") or (profile.get("run") or [""])[0]
    if not start:
        return {"texts": ["server profile has no start command"], "screens": [], "facts": facts}
    bg = ex.bash_background(start)
    facts["started"] = bool(bg.get("started"))
    texts.append(f"start: {start} -> {bg.get('started')} {_clip(bg.get('error'))}")
    if bg.get("started"):
        time.sleep(4)
        for ep in (profile.get("endpoints") or ["http://127.0.0.1:8000/"])[:6]:
            res = ex.bash(f"curl -fsS --max-time 8 {shlex.quote(ep)} | head -c 2000", timeout=20)
            texts.append(f"GET {ep}\n[exit {res.get('returncode')}]\n{_clip(res.get('stdout'))}")
        log = str(bg.get("log") or "")
        if log:
            tail = ex.bash(f"tail -c 2000 {shlex.quote(log)}", timeout=15)
            texts.append("server log tail:\n" + _clip(tail.get("stdout")))
        pid = str(bg.get("pid") or "").strip()
        if pid:
            ex.bash(f"kill {shlex.quote(pid)} 2>/dev/null; true", timeout=15)
    return {"texts": texts, "screens": [], "facts": facts}


# --- Android runner (host side: warm headless emulator) ---

def _adb(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_ADB, "-s", _EMULATOR_SERIAL, *args],
        capture_output=True, text=False, timeout=timeout,
    )


def _emulator_booted() -> bool:
    try:
        out = _adb("shell", "getprop", "sys.boot_completed", timeout=10)
        return out.stdout.decode(errors="replace").strip() == "1"
    except Exception:
        return False


def _ensure_emulator() -> bool:
    """Boot (or reuse) the warm probe emulator; serialized by a host flock."""
    os.makedirs(_AVD_HOME, exist_ok=True)
    env = {
        **os.environ,
        "JAVA_HOME": _JAVA_HOME,
        "ANDROID_AVD_HOME": _AVD_HOME,
        "ANDROID_SDK_ROOT": _SDK,
        "PATH": f"{_JAVA_HOME}/bin:{_SDK}/platform-tools:{os.environ.get('PATH', '')}",
    }
    with open(_EMULATOR_LOCK, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if _emulator_booted():
                return True
            if not os.path.isdir(os.path.join(_AVD_HOME, f"{_AVD_NAME}.avd")):
                create = subprocess.run(
                    [f"{_SDK}/cmdline-tools/latest/bin/avdmanager", "create", "avd",
                     "-n", _AVD_NAME, "-k", "system-images;android-35;google_apis;x86_64",
                     "--force"],
                    input=b"no\n", capture_output=True, timeout=120, env=env,
                )
                if create.returncode != 0:
                    return False
            subprocess.Popen(
                [_EMULATOR, "-avd", _AVD_NAME, "-port", _EMULATOR_PORT,
                 "-no-window", "-no-audio", "-no-boot-anim",
                 "-gpu", "swiftshader_indirect", "-memory", "2048"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env, start_new_session=True,
            )
            deadline = time.monotonic() + _BOOT_TIMEOUT_SEC
            while time.monotonic() < deadline:
                if _emulator_booted():
                    return True
                time.sleep(5)
            return False
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _pull_apk(ex: Any, host_dir: str) -> str:
    """Chunked bounded pull of the freshly built APK out of the VM, sha-verified."""
    found = ex.bash(
        "find . -path '*/build/outputs/apk/*' -name '*.apk' -newer settings.gradle "
        "2>/dev/null | head -1 || find . -path '*/build/outputs/apk/*' -name '*.apk' | head -1",
        timeout=30,
    )
    rel = (found.get("stdout") or "").strip().lstrip("./")
    if not rel:
        raise RuntimeError("no built apk found in the workspace")
    sha = ex.bash(f"sha256sum {shlex.quote(rel)} | cut -d' ' -f1", timeout=30)
    expected = (sha.get("stdout") or "").strip()
    split_dir = ".skitarii-probe-apk"
    ex.bash(
        f"rm -rf {split_dir} && mkdir -p {split_dir} && "
        f"split -b {_PULL_CHUNK_BYTES} -d -- {shlex.quote(rel)} {split_dir}/part-",
        timeout=60,
    )
    listing = ex.bash(f"ls {split_dir}", timeout=15)
    parts = sorted(p for p in (listing.get("stdout") or "").split() if p.startswith("part-"))
    blob = b""
    for part in parts[:16]:
        blob += ex.fetch_artifact(f"{split_dir}/{part}", max_bytes=_PULL_CHUNK_BYTES + 1)
    ex.bash(f"rm -rf {split_dir}", timeout=30)
    if expected and hashlib.sha256(blob).hexdigest() != expected:
        raise RuntimeError("apk pull integrity check failed")
    out = os.path.join(host_dir, "probe.apk")
    with open(out, "wb") as fh:
        fh.write(blob)
    return out


def _apk_package(apk_path: str) -> str:
    bt = sorted(Path(_SDK, "build-tools").glob("*"))
    aapt = str(bt[-1] / "aapt") if bt else ""
    try:
        out = subprocess.run([aapt, "dump", "badging", apk_path],
                             capture_output=True, text=True, timeout=30)
        for token in out.stdout.split():
            if token.startswith("name='"):
                return token.split("'")[1]
    except Exception:
        pass
    return ""


def _screencap() -> bytes:
    out = _adb("exec-out", "screencap", "-p", timeout=30)
    return out.stdout if out.returncode == 0 else b""


def _screen_size() -> tuple[int, int]:
    try:
        out = _adb("shell", "wm", "size", timeout=10).stdout.decode(errors="replace")
        physical = out.split(":")[-1].strip()
        w, h = physical.split("x")
        return int(w), int(h)
    except Exception:
        return 320, 640


def _probe_android(ex: Any, profile: dict[str, Any], scratch_dir: str) -> dict[str, Any]:
    texts: list[str] = []
    screens: list[bytes] = []
    facts: dict[str, Any] = {"installed": False, "launched": False, "alive_after_input": False,
                             "screen_reacts": False}
    if not _ensure_emulator():
        return {"texts": ["probe emulator did not boot"], "screens": [], "facts": facts}
    apk = _pull_apk(ex, scratch_dir)
    package = _apk_package(apk)
    install = _adb("install", "-r", apk, timeout=180)
    install_out = (install.stdout + install.stderr).decode(errors="replace")
    facts["installed"] = "Success" in install_out
    texts.append(f"adb install -> {_clip(install_out.strip())}")
    if not facts["installed"] or not package:
        return {"texts": texts, "screens": screens, "facts": facts}
    try:
        _adb("shell", "monkey", "-p", package, "-c",
             "android.intent.category.LAUNCHER", "1", timeout=30)
        time.sleep(5)
        alive = _adb("shell", "pidof", package, timeout=10)
        facts["launched"] = bool(alive.stdout.strip())
        first = _screencap()
        if first:
            screens.append(first)
        w, h = _screen_size()
        for x, y in ((w // 2, h // 2), (w // 2, int(h * 0.8)), (w // 3, int(h * 0.8))):
            _adb("shell", "input", "tap", str(x), str(y), timeout=10)
            time.sleep(2)
        after = _screencap()
        if after:
            screens.append(after)
        time.sleep(4)
        final = _screencap()
        if final:
            screens.append(final)
        alive2 = _adb("shell", "pidof", package, timeout=10)
        facts["alive_after_input"] = bool(alive2.stdout.strip())
        facts["screen_reacts"] = bool(
            len(screens) >= 2 and screens[0] != screens[-1]
        )
        crash = _adb("logcat", "-d", "-s", "AndroidRuntime:E", timeout=20)
        crash_text = crash.stdout.decode(errors="replace").strip()
        if package in crash_text:
            texts.append("crash log:\n" + _clip(crash_text[-2000:]))
            facts["crashed"] = True
    finally:
        _adb("uninstall", package, timeout=60)
        _adb("logcat", "-c", timeout=10)
    texts.append(f"facts: {facts}")
    return {"texts": texts, "screens": screens[:_MAX_SCREENS], "facts": facts}


def collect_evidence(ex: Any, profile: dict[str, Any], scratch_dir: str) -> dict[str, Any]:
    """Bounded, best-effort evidence for the critic; failures degrade, never raise."""
    kind = str(profile.get("kind") or "none")
    try:
        if kind == "android":
            return _probe_android(ex, profile, scratch_dir)
        if kind == "server":
            return _probe_server(ex, profile)
        if kind in ("cli", "library", "web"):
            return _probe_cli(ex, profile)
    except Exception as exc:  # noqa: BLE001 - probe is advisory infrastructure
        return {"texts": [f"probe failed: {type(exc).__name__}: {exc}"],
                "screens": [], "facts": {"probe_error": True}}
    return {"texts": [], "screens": [], "facts": {}}


def screens_to_data_urls(screens: list[bytes]) -> list[str]:
    return [
        "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        for png in screens[:_MAX_SCREENS] if png
    ]
