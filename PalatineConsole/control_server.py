#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"
RUNTIME_DIR = APP_ROOT / "runtime"
SERVER_PID = RUNTIME_DIR / "palatine-console.pid"
SERVER_LOG = RUNTIME_DIR / "palatine-console.log"
HOST = os.environ.get("PALATINE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PALATINE_PORT", "57257"))


@dataclass
class Service:
    key: str
    name: str
    group: str
    description: str
    start: list[str] | None = None
    stop: list[str] | None = None
    check: list[str] | None = None
    cwd: Path = ROOT
    pid_file: Path | None = None
    log_file: Path | None = None
    port: int | None = None
    url: str | None = None
    managed: bool = False
    command: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)


def p(*parts: str) -> Path:
    return ROOT.joinpath(*parts)


SERVICES: dict[str, Service] = {
    "llm_host": Service(
        "llm_host",
        "Gemma 12B LLM host",
        "core",
        "llama-server с основной Gemma 4 12B на GPU.",
        start=[str(p("CoreOfMadness", "llm-host", "scripts", "start-host.sh"))],
        stop=[str(p("CoreOfMadness", "llm-host", "scripts", "stop-host.sh"))],
        check=[str(p("CoreOfMadness", "llm-host", "scripts", "check-host.sh"))],
        pid_file=p("CoreOfMadness", "llm-host", "runtime", "llama-server.pid"),
        log_file=p("CoreOfMadness", "llm-host", "runtime", "llama-server.log"),
        port=8080,
        url="http://127.0.0.1:8080",
    ),
    "archive": Service(
        "archive",
        "ArchiveOfHeresy",
        "core",
        "OpenAI-compatible прослойка памяти и архива.",
        start=[str(p("ArchiveOfHeresy", "start-main.sh"))],
        stop=[str(p("ArchiveOfHeresy", "stop-main.sh"))],
        check=[str(p("ArchiveOfHeresy", "check-main.sh"))],
        cwd=p("ArchiveOfHeresy"),
        pid_file=p("ArchiveOfHeresy", "runtime", "archive-main.pid"),
        log_file=p("ArchiveOfHeresy", "runtime", "archive-main.log"),
        port=8090,
        url="http://127.0.0.1:8090",
    ),
    "telegram_bot": Service(
        "telegram_bot",
        "Telegram bot",
        "core",
        "Бот для общения с моделью через ArchiveOfHeresy.",
        start=[str(p("CoreOfMadness", "telegram-bot", "start-bot.sh"))],
        stop=[str(p("CoreOfMadness", "telegram-bot", "stop-bot.sh"))],
        cwd=p("CoreOfMadness", "telegram-bot"),
        pid_file=p("CoreOfMadness", "telegram-bot", "runtime", "telegram-bot.pid"),
        log_file=p("CoreOfMadness", "telegram-bot", "runtime", "telegram-bot.log"),
    ),
    "site": Service(
        "site",
        "Shushunya site",
        "public",
        "Локальный сайт ShushunyaSite.",
        start=[str(p("start-shushunya-site.sh"))],
        stop=[str(p("stop-shushunya-site.sh"))],
        pid_file=p("runtime", "shushunya-site", "site.pid"),
        log_file=p("runtime", "shushunya-site", "site.log"),
        port=8094,
        url="http://127.0.0.1:8094",
    ),
    "named_tunnel": Service(
        "named_tunnel",
        "Named Cloudflare tunnel",
        "public",
        "Постоянный туннель доменов chat/translator/stt/roxdub.",
        start=[str(p("start-cloudflare-tunnel.sh"))],
        stop=[str(p("stop-cloudflare-tunnel.sh"))],
        pid_file=p("runtime", "cloudflare", "shushunya-core.pid"),
        log_file=p("runtime", "cloudflare", "shushunya-core.log"),
    ),
    "translator": Service(
        "translator",
        "Translator server",
        "translation",
        "Локальный HTTP-переводчик.",
        start=[str(p("Shushunya_M", "start-translator-server.sh"))],
        stop=[str(p("Shushunya_M", "stop-translator-server.sh"))],
        cwd=p("Shushunya_M"),
        pid_file=p("Shushunya_M", "runtime", "translator-server.pid"),
        log_file=p("Shushunya_M", "runtime", "translator-server.log"),
        port=8091,
        url="http://127.0.0.1:8091",
    ),
    "translator_tunnel": Service(
        "translator_tunnel",
        "Translator temporary tunnel",
        "translation",
        "Временный trycloudflare-туннель переводчика.",
        start=[str(p("Shushunya_M", "start-translator-tunnel.sh"))],
        cwd=p("Shushunya_M"),
        pid_file=p("Shushunya_M", "runtime", "translator-cloudflared.pid"),
        log_file=p("Shushunya_M", "runtime", "translator-cloudflared.log"),
    ),
    "stt": Service(
        "stt",
        "STT server",
        "translation",
        "Локальный сервер распознавания речи.",
        start=[str(p("Shushunya_M", "start-stt-server.sh"))],
        cwd=p("Shushunya_M"),
        pid_file=p("Shushunya_M", "runtime", "stt-server.pid"),
        log_file=p("Shushunya_M", "runtime", "stt-server.log"),
        port=8093,
        url="http://127.0.0.1:8093",
    ),
    "stt_tunnel": Service(
        "stt_tunnel",
        "STT temporary tunnel",
        "translation",
        "Временный trycloudflare-туннель STT.",
        start=[str(p("Shushunya_M", "start-stt-tunnel.sh"))],
        cwd=p("Shushunya_M"),
        pid_file=p("Shushunya_M", "runtime", "stt-cloudflared.pid"),
        log_file=p("Shushunya_M", "runtime", "stt-cloudflared.log"),
    ),
    "internet_tunnel": Service(
        "internet_tunnel",
        "Archive temporary tunnel",
        "translation",
        "Временный туннель к ArchiveOfHeresy для тестов.",
        start=[str(p("Shushunya_M", "start-internet-tunnel.sh"))],
        stop=[str(p("Shushunya_M", "stop-internet-tunnel.sh"))],
        cwd=p("Shushunya_M"),
        pid_file=p("Shushunya_M", "runtime", "cloudflared.pid"),
        log_file=p("Shushunya_M", "runtime", "cloudflared.log"),
    ),
    "demons_sd35": Service(
        "demons_sd35",
        "DemonsForge SD 3.5",
        "image",
        "Gradio SD 3.5 Large, CPU by default.",
        managed=True,
        command=[str(p("DemonsForge", "start.sh"))],
        cwd=p("DemonsForge"),
        pid_file=RUNTIME_DIR / "demons-sd35.pid",
        log_file=RUNTIME_DIR / "demons-sd35.log",
        port=7860,
        url="http://127.0.0.1:7860",
    ),
    "demons_sdxl": Service(
        "demons_sdxl",
        "DemonsForge SDXL",
        "image",
        "Gradio SDXL Base, CPU by default.",
        managed=True,
        command=[str(p("DemonsForge", "start-sdxl.sh"))],
        cwd=p("DemonsForge"),
        pid_file=RUNTIME_DIR / "demons-sdxl.pid",
        log_file=RUNTIME_DIR / "demons-sdxl.log",
        port=7861,
        url="http://127.0.0.1:7861",
    ),
    "demons_flux": Service(
        "demons_flux",
        "DemonsForge FLUX",
        "image",
        "Gradio FLUX Schnell, CPU by default.",
        managed=True,
        command=[str(p("DemonsForge", "start-flux.sh"))],
        cwd=p("DemonsForge"),
        pid_file=RUNTIME_DIR / "demons-flux.pid",
        log_file=RUNTIME_DIR / "demons-flux.log",
        port=7862,
        url="http://127.0.0.1:7862",
    ),
    "roxdub": Service(
        "roxdub",
        "RoxDub server",
        "mechanicum",
        "Отдельный контроллер дубляжа и Android-клиента.",
        managed=True,
        command=[str(p("Mechanicum", "RoxDub", "RoxDub", "bin", "python")), "-m", "roxdub.server"],
        cwd=p("Mechanicum", "RoxDub"),
        pid_file=RUNTIME_DIR / "roxdub-server.pid",
        log_file=RUNTIME_DIR / "roxdub-server.log",
        port=8765,
        url="http://127.0.0.1:8765",
    ),
    "roxdub_tunnel": Service(
        "roxdub_tunnel",
        "RoxDub temporary tunnel",
        "mechanicum",
        "Временный туннель к RoxDub для Android.",
        managed=True,
        command=[
            str(p("android-tools", "cloudflared", "cloudflared")),
            "tunnel",
            "--url",
            "http://127.0.0.1:8765",
            "--protocol",
            "http2",
            "--no-autoupdate",
        ],
        cwd=p("Mechanicum", "RoxDub"),
        pid_file=RUNTIME_DIR / "roxdub-tunnel.pid",
        log_file=RUNTIME_DIR / "roxdub-tunnel.log",
    ),
}

BUNDLES = {
    "shushunya_core": ["llm_host", "archive", "telegram_bot"],
    "public_surface": ["site", "named_tunnel"],
    "translator_stack": ["translator", "stt"],
    "translator_public": ["translator", "stt", "translator_tunnel", "stt_tunnel"],
    "image_forge": ["demons_sdxl"],
    "roxdub_stack": ["llm_host", "roxdub"],
}

GROUPS = {
    "core": "Ядро",
    "public": "Публичный контур",
    "translation": "Переводчик и речь",
    "image": "DemonsForge",
    "mechanicum": "Mechanicum / RoxDub",
}


def pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid(path: Path | None) -> int | None:
    if not path or not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid_running(pid) else None


def port_open(port: int | None) -> bool:
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def service_status(service: Service) -> dict:
    pid = read_pid(service.pid_file)
    up = bool(pid) or port_open(service.port)
    return {
        "key": service.key,
        "name": service.name,
        "group": service.group,
        "groupName": GROUPS.get(service.group, service.group),
        "description": service.description,
        "running": up,
        "pid": pid,
        "port": service.port,
        "url": service.url,
        "logFile": str(service.log_file) if service.log_file else None,
        "canStop": bool(service.stop or service.pid_file),
        "canCheck": bool(service.check),
    }


def run_command(args: list[str], cwd: Path, timeout: int = 45, env: dict[str, str] | None = None) -> dict:
    merged_env = os.environ.copy()
    merged_env["PROJECT_ROOT"] = str(ROOT)
    if env:
        merged_env.update(env)
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return {"ok": result.returncode == 0, "code": result.returncode, "output": result.stdout[-12000:]}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "code": 124, "output": (exc.stdout or "") + "\nCommand timed out."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": 1, "output": str(exc)}


def start_managed(service: Service) -> dict:
    assert service.command and service.pid_file and service.log_file
    if read_pid(service.pid_file):
        return {"ok": True, "code": 0, "output": f"{service.name} already running."}
    service.log_file.parent.mkdir(parents=True, exist_ok=True)
    service.pid_file.parent.mkdir(parents=True, exist_ok=True)
    log = service.log_file.open("ab")
    env = os.environ.copy()
    env.update(service.env)
    process = subprocess.Popen(
        service.command,
        cwd=str(service.cwd),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    service.pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    return {"ok": True, "code": 0, "output": f"{service.name} started: PID {process.pid}\nLog: {service.log_file}"}


def stop_by_pid(service: Service) -> dict:
    pid = read_pid(service.pid_file)
    if not pid:
        if service.pid_file:
            service.pid_file.unlink(missing_ok=True)
        return {"ok": True, "code": 0, "output": f"{service.name} is not running."}
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not pid_running(pid):
            break
        time.sleep(0.1)
    if pid_running(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)
    if service.pid_file:
        service.pid_file.unlink(missing_ok=True)
    return {"ok": True, "code": 0, "output": f"Stopped {service.name} PID {pid}."}


def start_service(key: str) -> dict:
    service = SERVICES[key]
    if service.managed:
        return start_managed(service)
    if not service.start:
        return {"ok": False, "code": 1, "output": "No start command configured."}
    return run_command(service.start, service.cwd, timeout=60, env=service.env)


def stop_service(key: str) -> dict:
    service = SERVICES[key]
    if service.stop:
        return run_command(service.stop, service.cwd, timeout=30, env=service.env)
    return stop_by_pid(service)


def check_service(key: str) -> dict:
    service = SERVICES[key]
    if service.check:
        return run_command(service.check, service.cwd, timeout=45, env=service.env)
    status = service_status(service)
    label = "running" if status["running"] else "stopped"
    return {"ok": status["running"], "code": 0 if status["running"] else 1, "output": f"{service.name}: {label}"}


def run_bundle(name: str, action: str) -> dict:
    keys = BUNDLES[name]
    if action == "stop":
        keys = list(reversed(keys))
    results = []
    ok = True
    for key in keys:
        result = stop_service(key) if action == "stop" else start_service(key)
        ok = ok and result["ok"]
        results.append({"service": key, **result})
    return {"ok": ok, "results": results}


def tail(path: Path | None, limit: int = 16000) -> str:
    if not path or not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        return handle.read().decode("utf-8", errors="replace")


class Handler(BaseHTTPRequestHandler):
    server_version = "PalatineConsole/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self.address_string()} {fmt % args}\n"
        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SERVER_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def send_json(self, payload: object, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        content_type = "text/html; charset=utf-8"
        if path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json(
                {
                    "root": str(ROOT),
                    "groups": GROUPS,
                    "bundles": BUNDLES,
                    "services": [service_status(service) for service in SERVICES.values()],
                }
            )
            return
        if parsed.path == "/api/logs":
            key = parse_qs(parsed.query).get("service", [""])[0]
            if key == "console":
                self.send_json({"service": "console", "log": tail(SERVER_LOG)})
                return
            service = SERVICES.get(key)
            if not service:
                self.send_json({"error": "Unknown service"}, 404)
                return
            self.send_json({"service": key, "log": tail(service.log_file), "path": str(service.log_file)})
            return
        if parsed.path == "/":
            self.send_file(STATIC_DIR / "index.html")
            return
        if parsed.path.startswith("/static/"):
            self.send_file(STATIC_DIR / parsed.path.removeprefix("/static/"))
            return
        self.send_error(404)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path.startswith("/static/"):
            self.send_response(200)
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if parsed.path == "/api/service":
            key = payload.get("service")
            action = payload.get("action")
            if key not in SERVICES or action not in {"start", "stop", "check"}:
                self.send_json({"ok": False, "output": "Bad service or action."}, 400)
                return
            result = {"start": start_service, "stop": stop_service, "check": check_service}[action](key)
            self.send_json(result, 200 if result["ok"] else 500)
            return
        if parsed.path == "/api/bundle":
            name = payload.get("bundle")
            action = payload.get("action", "start")
            if name not in BUNDLES or action not in {"start", "stop"}:
                self.send_json({"ok": False, "output": "Bad bundle or action."}, 400)
                return
            result = run_bundle(name, action)
            self.send_json(result, 200 if result["ok"] else 500)
            return
        self.send_error(404)


def main() -> int:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SERVER_PID.write_text(f"{os.getpid()}\n", encoding="utf-8")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Palatine Console: {url}", flush=True)
    if "--open" in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        SERVER_PID.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
