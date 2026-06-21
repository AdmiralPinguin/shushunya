#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen


HOST = os.environ.get("TRANSLATOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("TRANSLATOR_PORT", "8091"))
LLM_BASE_URL = os.environ.get("TRANSLATOR_LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
LLM_MODEL = os.environ.get("TRANSLATOR_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf")

LANGUAGES = {
    "ru": "Russian",
    "ko": "Korean",
    "ar_dz": "Algerian Arabic (Darija)",
    "tr": "Turkish",
}


def normalize_language(value):
    return str(value or "").strip().lower().replace("-", "_")


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def write_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def translate(source, target, text):
    source_name = LANGUAGES[source]
    target_name = LANGUAGES[target]
    dialect_instruction = ""
    if target == "ar_dz":
        dialect_instruction = (
            "The target is Algerian Arabic (Darija): use natural Algerian Arabic phrasing where possible, "
            "not Modern Standard Arabic, while keeping the meaning clear. "
        )
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a meaning-preserving rewrite translator, not a word-for-word dictionary. "
                    f"Read the {source_name} text, understand the speaker's intent, tone, register, and implied meaning, "
                    f"then express the same meaning naturally in {target_name}. "
                    f"{dialect_instruction}"
                    "Output only the final translated text. "
                    "Do not explain, do not add notes, do not mention that this is a translation, "
                    "do not quote the source, do not provide alternatives, and do not add content that was not implied. "
                    "Keep names, URLs, code, numbers, and paragraph structure intact when appropriate. "
                    "Prefer natural target-language phrasing over literal source-language structure."
                ),
            },
            {
                "role": "user",
                "content": text,
            },
        ],
        "temperature": 0.0,
        "top_p": 0.1,
        "max_tokens": 2048,
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{LLM_BASE_URL}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        result = json.loads(response.read().decode("utf-8"))
    return (result.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "ShushunyaTranslator/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self):
        if self.path == "/health":
            write_json(self, 200, {"status": "ok", "service": "ShushunyaTranslator", "llm_base_url": LLM_BASE_URL})
            return
        write_json(self, 404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/translate":
            write_json(self, 404, {"error": "not found"})
            return

        try:
            payload = read_json(self)
            source = normalize_language(payload.get("source"))
            target = normalize_language(payload.get("target"))
            text = str(payload.get("text") or "").strip()
            if source not in LANGUAGES or target not in LANGUAGES or source == target:
                write_json(
                    self,
                    400,
                    {
                        "error": "source/target language is not supported or identical",
                        "source": source,
                        "target": target,
                        "supported": sorted(LANGUAGES.keys()),
                    },
                )
                return
            if not text:
                write_json(self, 400, {"error": "text is required"})
                return
            write_json(self, 200, {"translation": translate(source, target, text)})
        except Exception as exc:
            write_json(self, 500, {"error": str(exc)})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Shushunya translator started: http://{HOST}:{PORT}", flush=True)
    print(f"Upstream LLM: {LLM_BASE_URL}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
