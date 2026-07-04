from __future__ import annotations

from typing import Any

from .common import replace_project_file


def apply_python_text_utils_library_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    init = (
        "from .core import normalize_text, slugify, summarize_text, word_count\n\n"
        "__all__ = ['normalize_text', 'slugify', 'summarize_text', 'word_count']\n"
    )
    core = (
        "import re\n"
        "import unicodedata\n\n\n"
        "def normalize_text(value: str) -> str:\n"
        "    return ' '.join(value.strip().split())\n\n\n"
        "def slugify(value: str) -> str:\n"
        "    normalized = unicodedata.normalize('NFKD', normalize_text(value))\n"
        "    ascii_text = normalized.encode('ascii', 'ignore').decode('ascii').lower()\n"
        "    slug = re.sub(r'[^a-z0-9]+', '-', ascii_text).strip('-')\n"
        "    return slug or 'text'\n\n\n"
        "def word_count(value: str) -> int:\n"
        "    return len(re.findall(r'\\b\\w+\\b', normalize_text(value), flags=re.UNICODE))\n\n\n"
        "def summarize_text(value: str, max_words: int = 12) -> str:\n"
        "    if max_words < 1:\n"
        "        raise ValueError('max_words must be positive')\n"
        "    words = normalize_text(value).split()\n"
        "    if len(words) <= max_words:\n"
        "        return ' '.join(words)\n"
        "    return ' '.join(words[:max_words]) + '...'\n"
    )
    tests = (
        f"import unittest\n\nfrom {package} import normalize_text, slugify, summarize_text, word_count\n\n\n"
        "class TextUtilsLibraryTests(unittest.TestCase):\n"
        "    def test_normalize_text_collapses_spacing(self):\n"
        "        self.assertEqual(normalize_text('  alpha\\n\\tbeta   gamma  '), 'alpha beta gamma')\n\n"
        "    def test_slugify_generates_ascii_slug(self):\n"
        "        self.assertEqual(slugify('Hello, World! 2026'), 'hello-world-2026')\n"
        "        self.assertEqual(slugify('   '), 'text')\n\n"
        "    def test_word_count(self):\n"
        "        self.assertEqual(word_count('one two, three'), 3)\n"
        "        self.assertEqual(word_count('   '), 0)\n\n"
        "    def test_summarize_text(self):\n"
        "        self.assertEqual(summarize_text('one two three four', max_words=3), 'one two three...')\n"
        "        self.assertEqual(summarize_text('one two', max_words=3), 'one two')\n"
        "        with self.assertRaises(ValueError):\n"
        "            summarize_text('one two', max_words=0)\n"
    )
    readme = (
        f"# {project_name}\n\nA Python text utilities library with normalization, slug generation, word counting, and short summaries.\n\n"
        "## Use\n\n```python\nfrom "
        f"{package} import normalize_text, slugify, summarize_text, word_count\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, f"{package}/__init__.py", init)
    rows = replace_project_file(rows, f"{package}/core.py", core)
    rows = replace_project_file(rows, "tests/test_library.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def python_text_utils_library_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": package,
            "path": f"{package}/__init__.py",
            "responsibility": "public text utilities package exports",
            "requirements": ["export normalize_text", "export slugify", "export word_count", "export summarize_text"],
        },
        {
            "module": f"{package}.core",
            "path": f"{package}/core.py",
            "responsibility": "text utility domain behavior",
            "requirements": ["normalize whitespace", "generate ascii slugs", "count words", "summarize text with explicit word limits", "reject invalid summary limits"],
        },
        {
            "module": "tests.test_library",
            "path": "tests/test_library.py",
            "responsibility": "text utility library verification",
            "requirements": ["prove normalization", "prove slug generation", "prove word counting", "prove summary limits"],
        },
    ]
