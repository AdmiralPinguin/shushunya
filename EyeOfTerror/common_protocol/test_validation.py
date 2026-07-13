from __future__ import annotations

import unittest

from EyeOfTerror.common_protocol.protocol import review_finding
from EyeOfTerror.common_protocol.validation import (
    ProtocolValidationError,
    validate_review_findings,
)


def _finding() -> dict[str, object]:
    return {
        "code": "candidate_failure",
        "entity_kind": "behavioural_check",
        "entity_id": "public-1",
        "what_failed": "The candidate returned the wrong value.",
        "evidence": "Expected 2, observed 1.",
        "expected": "The executable check returns 2.",
        "remediation": "Repair the calculation and rerun the check.",
        "revision_owner": "fighter",
        "retryable": True,
    }


class ReviewFindingValidationTests(unittest.TestCase):
    def test_strict_review_finding_boundary(self) -> None:
        finding = _finding()
        self.assertEqual(
            [finding],
            validate_review_findings([finding], require_nonempty=True),
        )

        invalid_values = [
            [{}],
            [{**finding, "unknown": "field"}],
            [{**finding, "revision_owner": "nobody"}],
            [{**finding, "retryable": 1}],
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ProtocolValidationError):
                    validate_review_findings(value, require_nonempty=True)

    def test_constructor_bounds_multibyte_text_to_validator_limit(self) -> None:
        finding = review_finding(
            "candidate_failure",
            "ошибка" * 1_000,
            "доказательство",
            "ожидаемое поведение",
            "исправить и повторить проверку",
            "fighter",
            True,
            entity_kind="behavioural_check",
            entity_id="public-1",
        )
        self.assertLessEqual(len(finding["what_failed"].encode("utf-8")), 2_000)
        self.assertEqual(
            [finding], validate_review_findings([finding], require_nonempty=True)
        )


if __name__ == "__main__":
    unittest.main()
