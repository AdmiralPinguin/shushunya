from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_fastapi_notes_feature(project_name: str, files: list[Any]) -> list[Any]:
    main = (
        "try:\n"
        "    from fastapi import FastAPI, HTTPException\n"
        "    from pydantic import BaseModel\n"
        "except ModuleNotFoundError:\n"
        "    FastAPI = None\n"
        "    HTTPException = None\n"
        "    BaseModel = object\n\n\n"
        "class NoteCreate(BaseModel):\n"
        "    title: str\n"
        "    body: str = ''\n\n\n"
        "_notes: dict[int, dict[str, object]] = {}\n"
        "_next_id = 1\n\n\n"
        "def reset_notes() -> None:\n"
        "    global _notes, _next_id\n"
        "    _notes = {}\n"
        "    _next_id = 1\n\n\n"
        "def create_note(title: str, body: str = '') -> dict[str, object]:\n"
        "    global _next_id\n"
        "    clean_title = title.strip()\n"
        "    if not clean_title:\n"
        "        raise ValueError('note title is required')\n"
        "    note = {'id': _next_id, 'title': clean_title, 'body': body.strip()}\n"
        "    _notes[_next_id] = note\n"
        "    _next_id += 1\n"
        "    return dict(note)\n\n\n"
        "def list_notes() -> list[dict[str, object]]:\n"
        "    return [dict(note) for note in _notes.values()]\n\n\n"
        "def get_note(note_id: int) -> dict[str, object]:\n"
        "    if note_id not in _notes:\n"
        "        raise KeyError(note_id)\n"
        "    return dict(_notes[note_id])\n\n\n"
        "def delete_note(note_id: int) -> dict[str, object]:\n"
        "    if note_id not in _notes:\n"
        "        raise KeyError(note_id)\n"
        "    return dict(_notes.pop(note_id))\n\n\n"
        "def health() -> dict[str, bool]:\n"
        "    return {'ok': True}\n\n\n"
        "if FastAPI is not None:\n"
        f"    app = FastAPI(title='{project_name} Notes API')\n\n"
        "    @app.get('/health')\n"
        "    def health_endpoint() -> dict[str, bool]:\n"
        "        return health()\n\n"
        "    @app.post('/notes')\n"
        "    def create_note_endpoint(payload: NoteCreate) -> dict[str, object]:\n"
        "        try:\n"
        "            return create_note(payload.title, payload.body)\n"
        "        except ValueError as exc:\n"
        "            raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n"
        "    @app.get('/notes')\n"
        "    def list_notes_endpoint() -> list[dict[str, object]]:\n"
        "        return list_notes()\n\n"
        "    @app.get('/notes/{note_id}')\n"
        "    def get_note_endpoint(note_id: int) -> dict[str, object]:\n"
        "        try:\n"
        "            return get_note(note_id)\n"
        "        except KeyError as exc:\n"
        "            raise HTTPException(status_code=404, detail='note not found') from exc\n\n"
        "    @app.delete('/notes/{note_id}')\n"
        "    def delete_note_endpoint(note_id: int) -> dict[str, object]:\n"
        "        try:\n"
        "            return delete_note(note_id)\n"
        "        except KeyError as exc:\n"
        "            raise HTTPException(status_code=404, detail='note not found') from exc\n"
        "else:\n"
        "    app = None\n"
    )
    tests = (
        "import unittest\n\n"
        "from app.main import create_note, delete_note, get_note, health, list_notes, reset_notes\n\n\n"
        "class NotesApiTests(unittest.TestCase):\n"
        "    def setUp(self):\n"
        "        reset_notes()\n\n"
        "    def test_health(self):\n"
        "        self.assertEqual(health(), {'ok': True})\n\n"
        "    def test_create_list_and_get_note(self):\n"
        "        note = create_note(' First note ', ' body ')\n"
        "        self.assertEqual(note['id'], 1)\n"
        "        self.assertEqual(note['title'], 'First note')\n"
        "        self.assertEqual(note['body'], 'body')\n"
        "        self.assertEqual(list_notes(), [note])\n"
        "        self.assertEqual(get_note(1), note)\n\n"
        "    def test_rejects_empty_title(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            create_note('   ')\n\n"
        "    def test_delete_note(self):\n"
        "        note = create_note('remove me')\n"
        "        self.assertEqual(delete_note(note['id']), note)\n"
        "        self.assertEqual(list_notes(), [])\n"
        "        with self.assertRaises(KeyError):\n"
        "            get_note(note['id'])\n"
    )
    readme = (
        f"# {project_name}\n\nA FastAPI-compatible notes service with pure note logic tested without a live server.\n\n"
        "## Run\n\n```bash\nuvicorn app.main:app --reload\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n\n"
        "```bash\npython -m py_compile app/main.py\n```\n"
    )
    rows = replace_project_file(files, "app/main.py", main)
    rows = replace_project_file(rows, "tests/test_health.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def fastapi_notes_module_contracts() -> list[dict[str, Any]]:
    return [
        {
            "module": "app.main",
            "path": "app/main.py",
            "responsibility": "notes service domain logic and optional FastAPI routes",
            "requirements": ["health returns ok true", "create notes", "list notes", "get notes by id", "delete notes", "reject empty note titles"],
        },
        {
            "module": "tests.test_health",
            "path": "tests/test_health.py",
            "responsibility": "notes service behavior verification",
            "requirements": ["prove note lifecycle", "prove invalid title rejection", "prove deletion behavior without requiring live server"],
        },
    ]
