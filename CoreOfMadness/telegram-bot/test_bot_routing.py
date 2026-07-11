import unittest

import bot


class SharedChatModelRoutingTest(unittest.TestCase):
    def setUp(self):
        self.original_selections = dict(bot.CHAT_MODEL_SELECTIONS)
        self.original_request_json = bot.request_json
        self.original_archive_get = bot.archive_get
        self.original_running = bot.RUNNING
        self.original_job_timeout = bot.TELEGRAM_ARCHIVE_JOB_TIMEOUT_SEC
        self.original_poll_interval = bot.TELEGRAM_ARCHIVE_JOB_POLL_INTERVAL_SEC
        self.calls = []

        def fake_request_json(url, payload=None, timeout=60):
            self.calls.append((url, payload, timeout))
            return {"job_id": "job-1"}

        bot.request_json = fake_request_json
        bot.archive_get = lambda path, timeout=30: {
            "status": "done",
            "response": {"message": "routed answer"},
        }
        bot.RUNNING = True

    def tearDown(self):
        bot.CHAT_MODEL_SELECTIONS.clear()
        bot.CHAT_MODEL_SELECTIONS.update(self.original_selections)
        bot.request_json = self.original_request_json
        bot.archive_get = self.original_archive_get
        bot.RUNNING = self.original_running
        bot.TELEGRAM_ARCHIVE_JOB_TIMEOUT_SEC = self.original_job_timeout
        bot.TELEGRAM_ARCHIVE_JOB_POLL_INTERVAL_SEC = self.original_poll_interval

    def test_selected_model_is_sent_through_archive(self):
        model_key = "gemma"
        bot.CHAT_MODEL_SELECTIONS["42"] = model_key

        answer = bot.shared_chat_answer(42, "hello")

        self.assertEqual(answer, "routed answer")
        url, payload, timeout = self.calls[0]
        self.assertEqual(url, f"{bot.LLM_BASE_URL}/archive/client/chat/start")
        self.assertEqual(payload["model_route"], model_key)
        self.assertEqual(payload["model"], bot.DIRECT_MODELS[model_key]["model"])
        self.assertEqual(timeout, 30)

    def test_empty_selected_model_falls_back_to_legacy_model(self):
        original_model = bot.DIRECT_MODELS["gemma"]["model"]
        try:
            bot.DIRECT_MODELS["gemma"]["model"] = ""
            bot.CHAT_MODEL_SELECTIONS["42"] = "gemma"

            bot.shared_chat_answer(42, "hello")

            self.assertEqual(self.calls[0][1]["model"], bot.LLM_MODEL)
            self.assertEqual(self.calls[0][1]["model_route"], "gemma")
        finally:
            bot.DIRECT_MODELS["gemma"]["model"] = original_model

    def test_stale_qwen_selection_is_forced_back_to_interactive_gemma(self):
        bot.CHAT_MODEL_SELECTIONS["42"] = "qwen"

        bot.shared_chat_answer(42, "hello")

        payload = self.calls[0][1]
        self.assertEqual(payload["model_route"], "gemma")
        self.assertEqual(payload["model"], bot.DIRECT_MODELS["gemma"]["model"])

    def test_pending_job_can_reach_done_before_deadline(self):
        snapshots = iter(
            [
                {"ok": True, "status": "running"},
                {"ok": True, "status": "done", "response": {"message": "eventual answer"}},
            ]
        )
        bot.archive_get = lambda path, timeout=30: next(snapshots)
        bot.TELEGRAM_ARCHIVE_JOB_POLL_INTERVAL_SEC = 0

        self.assertEqual(bot.shared_chat_answer(42, "hello"), "eventual answer")

    def test_missing_job_fails_instead_of_polling_forever(self):
        bot.archive_get = lambda path, timeout=30: {
            "ok": False,
            "error": "mobile job not found",
        }

        with self.assertRaisesRegex(RuntimeError, "mobile job not found"):
            bot.shared_chat_answer(42, "hello")

    def test_job_polling_has_an_overall_deadline(self):
        bot.TELEGRAM_ARCHIVE_JOB_TIMEOUT_SEC = 0

        with self.assertRaisesRegex(TimeoutError, r"job-1.*may be orphaned"):
            bot.shared_chat_answer(42, "hello")


if __name__ == "__main__":
    unittest.main()
