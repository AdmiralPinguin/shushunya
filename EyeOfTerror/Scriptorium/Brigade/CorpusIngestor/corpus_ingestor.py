from __future__ import annotations

import hashlib
import html.parser
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


SUPPORTED_EXTENSIONS = {".txt", ".md", ".html", ".htm", ".xhtml", ".fb2", ".epub"}
MAX_SCAN_BYTES = 25_000_000
MAX_TEXT_CHARS = 250_000
METADATA_SUFFIXES = (".metadata.json", ".meta.json")

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "EyeOfTerror" / "Warmaster" / "MobileGateway" / "ShushunyaAgent").exists())
DEFAULT_CORPUS_ROOT = REPO_ROOT / "Corpus"
LEXMECHANIC_PLAYBOOK_DIR = REPO_ROOT / "EyeOfTerror" / "Scriptorium" / "Brigade" / "Lexmechanic" / "playbooks"
SHUSHUNYA_AGENT_DIR = REPO_ROOT / "EyeOfTerror" / "Warmaster" / "MobileGateway" / "ShushunyaAgent"
if str(SHUSHUNYA_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(SHUSHUNYA_AGENT_DIR))

try:
    from shushunya_agent.web_tools import extract_epub_text  # type: ignore
except Exception:  # pragma: no cover - fallback is only for unusual import failures.
    extract_epub_text = None


class TextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def configured_corpus_root() -> Path:
    raw = os.environ.get("SHUSHUNYA_CORPUS_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_CORPUS_ROOT.resolve()


def clean_text(value: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in value.splitlines())).strip()


def html_to_text(value: str) -> str:
    parser = TextExtractor()
    parser.feed(value)
    parser.close()
    return clean_text(parser.text())


def fb2_to_text(value: str) -> str:
    root = ElementTree.fromstring(value)
    parts: list[str] = []
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1].lower()
        if local_name in {"p", "title", "subtitle", "text-author", "epigraph"}:
            text = " ".join("".join(element.itertext()).split())
            if text:
                parts.append(text)
    return clean_text("\n".join(parts))


def read_epub(path: Path) -> str:
    if extract_epub_text is not None:
        _title, text = extract_epub_text(path.read_bytes())
        return clean_text(str(text))
    parts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name.lower().endswith((".html", ".htm", ".xhtml")):
                parts.append(html_to_text(archive.read(name).decode("utf-8", errors="replace")))
    return clean_text("\n\n".join(parts))


def read_corpus_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if path.stat().st_size > MAX_SCAN_BYTES:
        raise ValueError(f"file is too large for corpus scan: {path.stat().st_size} bytes")
    if suffix in {".txt", ".md"}:
        return clean_text(path.read_text(encoding="utf-8", errors="replace")), "local_text"
    if suffix in {".html", ".htm", ".xhtml"}:
        return html_to_text(path.read_text(encoding="utf-8", errors="replace")), "local_html"
    if suffix == ".fb2":
        return fb2_to_text(path.read_text(encoding="utf-8", errors="replace")), "local_fb2"
    if suffix == ".epub":
        return read_epub(path), "local_epub"
    raise ValueError(f"unsupported corpus extension: {suffix}")


def relevance_tokens(text: str) -> set[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "warhammer",
        "black",
        "library",
        "official",
        "source",
        "собери",
        "события",
        "информацию",
        "максимально",
        "полно",
    }
    return {token for token in re.findall(r"[a-zа-я0-9]+", text.lower()) if len(token) > 2 and token not in stopwords}


def matching_source_playbooks(goal: str) -> list[dict[str, Any]]:
    playbooks: list[dict[str, Any]] = []
    lowered = goal.lower()
    if not LEXMECHANIC_PLAYBOOK_DIR.exists():
        return playbooks
    for path in sorted(LEXMECHANIC_PLAYBOOK_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        terms = [str(term).lower() for term in payload.get("match_terms", []) if term]
        if any(term in lowered for term in terms):
            playbooks.append(payload)
    return playbooks


def source_playbook_terms(goal: str) -> set[str]:
    terms: set[str] = set()
    for playbook in matching_source_playbooks(goal):
        terms.update(relevance_tokens(str(playbook.get("topic") or "")))
        for source in playbook.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_class = str(source.get("source_class") or source.get("type") or "").lower()
            source_kind = str(source.get("type") or "").lower()
            if "primary" in source_class or source_kind in {"novel", "short_story", "book"}:
                terms.update(relevance_tokens(str(source.get("title") or "")))
    return terms


def contract_terms(contract: dict[str, Any]) -> set[str]:
    goal = str(contract.get("goal") or "")
    terms = set(relevance_tokens(goal))
    terms.update(source_playbook_terms(goal))
    lowered = goal.lower()
    expansions = {
        "скалатрак": "skalathrax",
        "skalathrax": "скалатракс",
        "кхарн": "kharn",
        "kharn": "кхарн",
    }
    for needle, expansion in expansions.items():
        if needle in lowered:
            terms.update(relevance_tokens(expansion))
    for artifact in contract.get("required_artifacts", []):
        if isinstance(artifact, str):
            terms.update(relevance_tokens(artifact))
    return terms


def source_title(path: Path, text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line and len(first_line) <= 120:
        return first_line
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name


def metadata_paths_for(path: Path) -> list[Path]:
    return [path.with_name(path.name + suffix) for suffix in METADATA_SUFFIXES] + [
        path.with_suffix(path.suffix + ".json"),
    ]


def read_source_metadata(path: Path, corpus_root: Path) -> tuple[dict[str, Any], str]:
    for metadata_path in metadata_paths_for(path):
        if not metadata_path.exists():
            continue
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {}, f"{metadata_path.relative_to(corpus_root)}: {exc}"
        if not isinstance(payload, dict):
            return {}, f"{metadata_path.relative_to(corpus_root)}: metadata must be a JSON object"
        return payload, ""
    return {}, ""


def metadata_terms(metadata: dict[str, Any]) -> set[str]:
    values: list[str] = []
    for key in ("title", "language", "source_class", "source_type", "type", "series", "author"):
        if metadata.get(key):
            values.append(str(metadata.get(key)))
    for key in ("tags", "aliases", "topics", "keywords"):
        raw_items = metadata.get(key)
        if isinstance(raw_items, list):
            values.extend(str(item) for item in raw_items if item)
    return relevance_tokens(" ".join(values))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def corpus_source(
    path: Path,
    corpus_root: Path,
    text: str,
    source_kind: str,
    score: int,
    matched_terms: set[str],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = metadata if isinstance(metadata, dict) else {}
    extension_type = {
        "local_epub": "book",
        "local_fb2": "book",
        "local_html": "document",
        "local_text": "document",
    }.get(source_kind, "document")
    title = str(metadata.get("title") or "").strip() or source_title(path, text)
    source_kind_override = str(metadata.get("type") or metadata.get("source_type") or "").strip()
    source_class = str(metadata.get("source_class") or "").strip() or "local_primary_candidate"
    source = {
        "title": title,
        "type": source_kind_override or extension_type,
        "language": str(metadata.get("language") or "unknown"),
        "local_path": str(path),
        "corpus_relative_path": str(path.relative_to(corpus_root)),
        "sha256": file_sha256(path),
        "text_chars": min(len(text), MAX_TEXT_CHARS),
        "relevance_score": score,
        "matched_terms": sorted(matched_terms),
        "reliability": str(metadata.get("reliability") or "user-provided"),
        "direct_event_detail_level": str(metadata.get("direct_event_detail_level") or "unknown"),
        "source_class": source_class,
        "expected_use": str(metadata.get("expected_use") or "user-provided local corpus text; treat as a primary candidate only after extractor evidence matches the task"),
        "discovery_method": "local_corpus",
    }
    for key in ("author", "series", "publication_year", "tags", "aliases", "topics"):
        if key in metadata:
            source[key] = metadata[key]
    if metadata:
        source["metadata_available"] = True
    return source


def scan_corpus(contract: dict[str, Any], corpus_root: Path | None = None) -> dict[str, Any]:
    root = (corpus_root or configured_corpus_root()).resolve()
    terms = contract_terms(contract)
    sources: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    non_matching: list[dict[str, Any]] = []
    metadata_errors: list[str] = []
    files_scanned = 0
    non_matching_count = 0
    metadata_count = 0
    if not root.exists():
        return {
            "topic": str(contract.get("goal") or ""),
            "corpus_root": str(root),
            "sources": [],
            "skipped": [],
            "non_matching": [],
            "summary": {
                "corpus_exists": False,
                "files_scanned": 0,
                "sources_matched": 0,
                "sources_non_matching": 0,
                "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            },
            "gaps": [f"Local corpus directory does not exist: {root}"],
        }
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        files_scanned += 1
        try:
            text, source_kind = read_corpus_text(path)
        except Exception as exc:  # noqa: BLE001 - unreadable local files are corpus diagnostics.
            skipped.append({"path": str(path), "reason": str(exc)})
            continue
        metadata, metadata_error = read_source_metadata(path, root)
        if metadata_error:
            metadata_errors.append(metadata_error)
        if metadata:
            metadata_count += 1
        haystack = " ".join(
            [
                path.name,
                str(path.relative_to(root)),
                " ".join(sorted(metadata_terms(metadata))),
                text[:5000],
            ]
        ).lower()
        haystack_tokens = relevance_tokens(haystack)
        matched_terms = terms & haystack_tokens
        score = len(matched_terms)
        if terms and score == 0:
            non_matching_count += 1
            if len(non_matching) < 30:
                non_matching.append(
                    {
                        "corpus_relative_path": str(path.relative_to(root)),
                        "text_chars": min(len(text), MAX_TEXT_CHARS),
                        "reason": "no task relevance terms matched filename, path, or text sample",
                    }
                )
            continue
        if len(text.strip()) < 100:
            skipped.append({"path": str(path), "reason": "text extraction produced too little text"})
            continue
        sources.append(corpus_source(path, root, text, source_kind, score, matched_terms, metadata))
    sources.sort(key=lambda item: (int(item.get("relevance_score") or 0), int(item.get("text_chars") or 0)), reverse=True)
    gaps: list[str] = []
    if not sources:
        gaps.append("Local corpus contains no matching supported texts for this task.")
    return {
        "topic": str(contract.get("goal") or ""),
        "corpus_root": str(root),
        "sources": sources,
        "skipped": skipped,
        "non_matching": non_matching,
        "summary": {
            "corpus_exists": True,
            "files_scanned": files_scanned,
            "sources_matched": len(sources),
            "sources_non_matching": non_matching_count,
            "non_matching_sample_count": len(non_matching),
            "metadata_files_loaded": metadata_count,
            "metadata_error_count": len(metadata_errors),
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        },
        "metadata_errors": metadata_errors,
        "gaps": gaps,
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    contract = request.get("contract")
    step = request.get("step")
    if not isinstance(contract, dict):
        return {"ok": False, "worker": "CorpusIngestor", "error": "request.contract must be an object"}
    if not isinstance(step, dict):
        return {"ok": False, "worker": "CorpusIngestor", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "CorpusIngestor", "error": "step.expected_artifacts is empty"}
    output_path = str(expected_artifacts[0])
    index = scan_corpus(contract)
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = index.get("summary") if isinstance(index.get("summary"), dict) else {}
    return {
        "ok": True,
        "worker": "CorpusIngestor",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Indexed {summary.get('sources_matched', 0)} matching local corpus sources from {summary.get('files_scanned', 0)} files.",
        "artifacts": [output_path],
        "gaps": index.get("gaps", []),
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run CorpusIngestor on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/corpus-ingestor-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
