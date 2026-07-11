import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import archive_httpio


class LlmRouteHeaderTest(unittest.TestCase):
    def test_allowlisted_route_is_added_to_upstream_headers(self):
        priority_token = archive_httpio.LLM_PRIORITY.set("chat")
        route_token = archive_httpio.LLM_ROUTE.set("")
        try:
            self.assertEqual(archive_httpio.set_llm_route(" QWEN "), "qwen")
            headers = archive_httpio._with_priority({"Content-Type": "application/json"})
            self.assertEqual(headers["X-LLM-Priority"], "chat")
            self.assertEqual(headers["X-LLM-Route"], "qwen")
        finally:
            archive_httpio.LLM_ROUTE.reset(route_token)
            archive_httpio.LLM_PRIORITY.reset(priority_token)

    def test_unknown_route_is_cleared(self):
        route_token = archive_httpio.LLM_ROUTE.set("qwen")
        try:
            self.assertEqual(archive_httpio.set_llm_route("http://attacker.invalid"), "")
            self.assertNotIn("X-LLM-Route", archive_httpio._with_priority({}))
        finally:
            archive_httpio.LLM_ROUTE.reset(route_token)


if __name__ == "__main__":
    unittest.main()
