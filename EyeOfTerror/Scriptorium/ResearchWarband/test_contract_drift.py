from __future__ import annotations

from pathlib import Path
import sys
import unittest


# In the isolated build tree the native boundary is staged separately.  In the
# deployed repository EyeOfTerror is already importable from the repository
# root.  Keep this test importing the real production module by package name.
_NATIVE_ROOT = Path(__file__).resolve().parents[1] / "native_boundary"
if str(_NATIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(_NATIVE_ROOT))

from EyeOfTerror.common_protocol import iskandar_directive as native_directive

from ResearchWarband import execution_policy as engine_policy


class DirectiveContractDriftTests(unittest.TestCase):
    def test_native_and_engine_directive_contracts_are_exactly_equal(self) -> None:
        self.assertEqual(
            set(native_directive.SOURCE_CLASSES),
            set(engine_policy.SOURCE_CLASSES),
        )
        self.assertEqual(
            set(native_directive.RESEARCH_DEPTHS),
            set(engine_policy.RESEARCH_DEPTHS),
        )
        self.assertEqual(
            set(native_directive.SOURCE_POLICIES),
            set(engine_policy.SOURCE_POLICIES),
        )
        self.assertEqual(
            set(native_directive.ERROR_TOLERANCES),
            set(engine_policy.ERROR_TOLERANCES),
        )
        self.assertEqual(
            set(native_directive.ANSWER_MODES),
            set(engine_policy.ANSWER_MODES),
        )
        self.assertEqual(
            set(native_directive.DIRECTIVE_FIELDS),
            set(engine_policy.DIRECTIVE_FIELDS),
        )
        self.assertEqual(
            native_directive.DIRECTIVE_KIND,
            engine_policy.DIRECTIVE_KIND,
        )
        self.assertEqual(
            native_directive.DIRECTIVE_VERSION,
            engine_policy.DIRECTIVE_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
