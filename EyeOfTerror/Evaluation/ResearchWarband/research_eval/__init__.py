"""External, answer-key-isolated evaluation for the Research Warband."""

from .fixtures import FixtureError, LoadedFixture, load_fixture
from .manifest import ManifestError, LoadedSuite, load_suite
from .runner import run_suite
from .subjects import FakeSubjectAdapter, SubjectAdapter, SubjectExecution

__all__ = [
    "FakeSubjectAdapter",
    "FixtureError",
    "LoadedFixture",
    "LoadedSuite",
    "ManifestError",
    "SubjectAdapter",
    "SubjectExecution",
    "load_fixture",
    "load_suite",
    "run_suite",
]

__version__ = "0.1.0"
