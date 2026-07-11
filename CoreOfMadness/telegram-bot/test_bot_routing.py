import unittest

import bot


class SharedChatModelRoutingTest(unittest.TestCase):
    def setUp(self):
        self.original_selections = dict(bot.CHAT_MODEL_SELECTIONS)
        self.original_request_json = bot.request_json
        self.original_archive_get = bot.archive_get
        self.original_running = bot.RUNNING
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

    def test_selected_model_is_sent_through_archive(self):
        for model_key in ("qwen", "gemma"):
            with self.subTest(model_key=model_key):
                self.calls.clear()
                bot.CHAT_MODEL_SELECTIONS["42"] = model_key

                answer = bot.shared_chat_answer(42, "hello")

                self.assertEqual(answer, "routed answer")
                url, payload, timeout = self.calls[0]
                self.assertEqual(url, f"{bot.LLM_BASE_URL}/archive/client/chat/start")
                self.assertEqual(payload["model_route"], model_key)
                self.assertEqual(payload["model"], bot.DIRECT_MODELS[model_key]["model"])
                self.assertEqual(timeout, 30)

    def test_empty_selected_model_falls_back_to_legacy_model(self):
        original_model = bot.DIRECT_MODELS["qwen"]["model"]
        try:
            bot.DIRECT_MODELS["qwen"]["model"] = ""
            bot.CHAT_MODEL_SELECTIONS["42"] = "qwen"

            bot.shared_chat_answer(42, "hello")

            self.assertEqual(self.calls[0][1]["model"], bot.LLM_MODEL)
            self.assertEqual(self.calls[0][1]["model_route"], "qwen")
        finally:
            bot.DIRECT_MODELS["qwen"]["model"] = original_model


if __name__ == "__main__":
    unittest.main()
