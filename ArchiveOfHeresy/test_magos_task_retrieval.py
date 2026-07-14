import gc
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archivist_agent import magos_agent
from archivist_agent import vector_memory as vector_memory_module


class _FakeVectorMemory:
    def __init__(self, matches):
        self.matches = list(matches)
        self.search_calls = []
        self.search_result_counts = []

    def search(self, query, **kwargs):
        self.search_calls.append({"query": query, **kwargs})
        namespace = kwargs.get("memory_namespace")
        exclude_turn_id = kwargs.get("exclude_turn_id")
        min_score = float(kwargs.get("min_score", 0.0))
        ranker = kwargs.get("ranker")
        candidates = [
            dict(match)
            for match in self.matches
            if match.get("memory_namespace") == namespace
            and match.get("turn_id") != exclude_turn_id
            and float(match.get("score") or 0.0) >= min_score
        ]
        candidates.sort(
            key=lambda match: (
                -float(ranker(match) if ranker is not None else match.get("score") or 0.0),
                match.get("created_at") or "",
            )
        )
        result = candidates[: max(1, int(kwargs.get("limit") or 1))]
        self.search_result_counts.append(len(result))
        return result

    def recent_session_chunks(self, *_args, **_kwargs):
        return []


class MagosTaskRetrievalTest(unittest.TestCase):
    def test_vector_overfetch_hybrid_rerank_recovers_distinctive_old_episode(self):
        matches = [
            {
                "score": 0.892,
                "created_at": "2026-07-08T16:23:35+09:00",
                "role": "user",
                "content": "Че там у нас по последней задаче?",
                "label": "факт",
                "memory_namespace": "shushunya",
            },
            {
                "score": 0.869,
                "created_at": "2026-07-07T11:14:06+09:00",
                "role": "user",
                "content": "Так что там с задачей по реконструкции?",
                "label": "факт",
                "memory_namespace": "shushunya",
            },
            {
                "score": 0.868,
                "created_at": "2026-07-14T16:34:11+09:00",
                "role": "user",
                "content": "Че там висят активные задачи?",
                "label": "",
                "memory_namespace": "shushunya",
            },
            {
                "score": 0.865,
                "created_at": "2026-07-07T10:37:13+09:00",
                "role": "user",
                "content": "Повтори последнюю задачу.",
                "label": "мнение",
                "memory_namespace": "shushunya",
            },
            {
                "score": 0.861,
                "created_at": "2026-07-07T01:37:45+09:00",
                "role": "user",
                "content": "Коротко: ты кто?",
                "label": "",
                "memory_namespace": "shushunya",
            },
            {
                "score": 0.860,
                "created_at": "2026-07-13T18:58:39+09:00",
                "role": "user",
                "content": (
                    "Смотри задачку. Есть 2 кнопки, синяя и красная. "
                    "Если большинство выберет красную, куда нажмешь?"
                ),
                "label": "",
                "memory_namespace": "shushunya",
            },
        ]
        vector = _FakeVectorMemory(matches)
        magos = magos_agent.Magos(
            Path("unused-focus"),
            Path("unused-wiki"),
            lambda *_args, **_kwargs: None,
            vector_memory=vector,
        )

        with patch.object(magos_agent, "MAGOS_EXTRA_NAMESPACES", set()):
            context = magos.vector_context(
                "А помнишь задачу про кнопки?",
                memory_namespace="shushunya",
                conversation_id="shushunya-main",
                turn_id="current-turn",
            )

        self.assertGreaterEqual(
            vector.search_calls[0]["limit"],
            min(
                magos_agent.VECTOR_TOP_K * magos_agent.MAGOS_VECTOR_OVERFETCH,
                magos_agent.MAGOS_VECTOR_MAX_CANDIDATES,
            ),
        )
        self.assertEqual(vector.search_calls[0]["min_score"], -1.0)
        self.assertTrue(callable(vector.search_calls[0]["ranker"]))
        self.assertIn("синяя и красная", context)
        self.assertLess(
            context.index("синяя и красная"),
            context.index("последней задаче"),
        )
        self.assertIn("rank=", context)
        self.assertIn("semantic=", context)
        self.assertIn("lexical=", context)
        self.assertNotIn(" score=", context)

    def test_hybrid_rank_is_applied_before_realistic_top_40_truncation(self):
        semantic_neighbors = [
            {
                "score": 0.950 - index * 0.001,
                "created_at": f"2026-07-14T00:{index:02d}:00+09:00",
                "role": "user",
                "content": f"generic semantically similar task memory {index}",
                "label": "",
                "memory_namespace": "shushunya",
            }
            for index in range(50)
        ]
        exact_episode = {
            "score": 0.840,
            "created_at": "2026-07-13T18:58:39+09:00",
            "role": "user",
            "content": "episode azurecrimsonbutton42 with the blue and red choice",
            "label": "",
            "memory_namespace": "shushunya",
        }
        vector = _FakeVectorMemory(semantic_neighbors + [exact_episode])
        magos = magos_agent.Magos(
            Path("unused-focus"),
            Path("unused-wiki"),
            lambda *_args, **_kwargs: None,
            vector_memory=vector,
        )

        with patch.object(magos_agent, "MAGOS_EXTRA_NAMESPACES", set()):
            context = magos.vector_context(
                "remember azurecrimsonbutton42",
                memory_namespace="shushunya",
                conversation_id="shushunya-main",
                turn_id="current-turn",
            )

        call = vector.search_calls[0]
        semantic_rows_above_exact = sum(
            1 for match in semantic_neighbors if match["score"] > exact_episode["score"]
        )
        self.assertGreater(semantic_rows_above_exact, call["limit"])
        self.assertEqual(vector.search_result_counts[0], call["limit"])
        self.assertTrue(callable(call["ranker"]))
        self.assertIn("azurecrimsonbutton42", context)
        self.assertLess(
            context.index("azurecrimsonbutton42"),
            context.index("generic semantically similar"),
        )

    def test_vector_memory_ranker_runs_before_limit_and_keeps_semantic_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = vector_memory_module.VectorMemory(Path(temp_dir) / "vectors")
            rows = []
            for index in range(50):
                score = 0.950 - index * 0.001
                rows.append(
                    (
                        f"neighbor-{index}",
                        f"turn-{index}",
                        "conversation",
                        "shushunya",
                        f"2026-07-14T00:{index:02d}:00+09:00",
                        "user",
                        0,
                        f"generic neighbor {index}",
                        "test-version",
                        json.dumps([score, 0.0]),
                        "",
                    )
                )
            rows.append(
                (
                    "exact",
                    "exact-turn",
                    "conversation",
                    "shushunya",
                    "2026-07-13T18:58:39+09:00",
                    "user",
                    0,
                    "the exact lexical anchor",
                    "test-version",
                    json.dumps([0.840, 0.0]),
                    "",
                )
            )
            with sqlite3.connect(memory.db_path) as db:
                db.executemany(
                    """
                    INSERT INTO vector_chunks (
                        id, turn_id, conversation_id, memory_namespace,
                        created_at, role, chunk_index, content,
                        embedding_version, embedding_json, label
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            db.close()

            with patch.object(
                vector_memory_module,
                "embed_text",
                return_value=([1.0, 0.0], "test-version", "test"),
            ):
                results = memory.search(
                    "query",
                    limit=40,
                    min_score=-1.0,
                    memory_namespace="shushunya",
                    ranker=lambda match: (
                        match["score"]
                        + (0.25 if "exact lexical anchor" in match["content"] else 0.0)
                    ),
                )

            self.assertEqual(len(results), 40)
            self.assertEqual(results[0]["turn_id"], "exact-turn")
            self.assertAlmostEqual(results[0]["score"], 0.840)
            del memory
            gc.collect()

    def test_low_semantic_exact_anchor_survives_vector_cutoff_and_is_scored_honestly(self):
        vector = _FakeVectorMemory(
            [
                {
                    "score": 0.330,
                    "created_at": "2026-07-14T00:00:00+09:00",
                    "role": "user",
                    "content": "совсем другой общий эпизод",
                    "label": "",
                    "memory_namespace": "shushunya",
                },
                {
                    "score": 0.200,
                    "created_at": "2026-07-13T00:00:00+09:00",
                    "role": "user",
                    "content": "эпизод красносиняякнопка с точным якорем",
                    "label": "",
                    "memory_namespace": "shushunya",
                },
            ]
        )
        magos = magos_agent.Magos(
            Path("unused-focus"),
            Path("unused-wiki"),
            lambda *_args, **_kwargs: None,
            vector_memory=vector,
        )

        with patch.object(magos_agent, "MAGOS_EXTRA_NAMESPACES", set()):
            context = magos.vector_context(
                "красносиняякнопка",
                memory_namespace="shushunya",
                conversation_id="shushunya-main",
                turn_id="current-turn",
            )

        self.assertEqual(vector.search_calls[0]["min_score"], -1.0)
        self.assertLess(
            context.index("красносиняякнопка"),
            context.index("совсем другой"),
        )
        self.assertIn("rank=0.450 semantic=0.200 lexical=1.000", context)

    def test_discourse_only_task_references_have_no_lexical_evidence(self):
        examples = [
            (
                "remember the task we discussed before",
                "we discussed before: old Galaga controls",
            ),
            (
                "помнишь задачу которую мы обсуждали раньше",
                "мы обсуждали раньше старую задачу про управление Galaga",
            ),
            (
                "do you remember that project we talked about earlier",
                "that project we talked about earlier was Galaga",
            ),
            (
                "помнишь ту работу которую мы с тобой обсуждали ранее",
                "ту работу мы с тобой обсуждали ранее: это была Galaga",
            ),
            (
                "remember the task we spoke about earlier",
                "we spoke about the old Galaga task earlier",
            ),
            (
                "remember the work which we screamed about yesterday",
                "we screamed about yesterday's Galaga work",
            ),
            (
                "помнишь задачу о которой мы болтали",
                "мы болтали о старой задаче Galaga",
            ),
            (
                "помнишь работу которую мы вчера препарировали",
                "вчера мы препарировали старую работу по Galaga",
            ),
            (
                "what was the task we spoke about earlier?",
                "the task we spoke about earlier was Galaga",
            ),
            (
                "какая была задача, о которой мы болтали?",
                "мы болтали о старой задаче Galaga",
            ),
            (
                "remember the task spoken about earlier",
                "the task spoken about earlier was Galaga",
            ),
            (
                "remember the assignment we discussed",
                "the assignment we discussed was Galaga",
            ),
            (
                "помнишь то дело, которое обсуждали",
                "то дело, которое обсуждали, было про Galaga",
            ),
            (
                "remember task mentioned earlier",
                "the task mentioned earlier was Galaga",
            ),
            (
                "помнишь задачу препарированную ранее",
                "задача, препарированная ранее, была Galaga",
            ),
        ]
        for query, stale_episode in examples:
            with self.subTest(query=query):
                self.assertEqual(magos_agent.query_lexical_anchors(query), set())
                self.assertEqual(
                    magos_agent.lexical_anchor_overlap(query, stale_episode),
                    0.0,
                )
                self.assertEqual(
                    magos_agent.hybrid_retrieval_score(query, stale_episode, 0.80),
                    (0.80, 0.0),
                )

    def test_distinctive_topic_id_and_file_anchors_survive_recall_grammar(self):
        russian = magos_agent.query_lexical_anchors(
            "А помнишь задачу про красную и синюю кнопки?"
        )
        self.assertIn("красную", russian)
        self.assertIn("синюю", russian)
        self.assertIn("кнопки", russian)
        self.assertGreater(
            magos_agent.lexical_anchor_overlap(
                "А помнишь задачу про красную и синюю кнопки?",
                "Есть две кнопки: синяя и красная.",
            ),
            0.45,
        )

        english = magos_agent.query_lexical_anchors(
            "remember task about red blue button wm-player player_controller.gd"
        )
        for anchor in ("red", "blue", "button", "wm", "player", "player_controller", "gd"):
            self.assertIn(anchor, english)

        machine = magos_agent.query_lexical_anchors(
            "remember the work task worker_plan.py task-run-42"
        )
        for anchor in ("worker_plan", "py", "task", "run", "42"):
            self.assertIn(anchor, machine)

        without_recall_cue = magos_agent.query_lexical_anchors(
            "the task about red blue buttons"
        )
        self.assertEqual(without_recall_cue, {"red", "blue", "buttons"})
        self.assertEqual(
            magos_agent.query_lexical_anchors("workflow graph"),
            {"workflow", "graph"},
        )
        for terse_query in (
            "fix task red blue buttons",
            "remember task red blue buttons",
            "помнишь задачу красная синяя кнопки",
        ):
            with self.subTest(terse_query=terse_query):
                anchors = magos_agent.query_lexical_anchors(terse_query)
                if "кнопки" in terse_query:
                    self.assertTrue({"красная", "синяя", "кнопки"} <= anchors)
                else:
                    self.assertTrue({"red", "blue", "buttons"} <= anchors)

    def test_vector_query_features_are_built_once_for_the_whole_exact_scan(self):
        matches = [
            {
                "score": 0.80 + index / 1000,
                "created_at": f"2026-07-14T00:{index:02d}:00+09:00",
                "role": "user",
                "content": f"memory {index} about red and blue buttons",
                "label": "",
                "memory_namespace": "shushunya",
            }
            for index in range(30)
        ]
        vector = _FakeVectorMemory(matches)
        magos = magos_agent.Magos(
            Path("unused-focus"),
            Path("unused-wiki"),
            lambda *_args, **_kwargs: None,
            vector_memory=vector,
        )

        original = magos_agent.build_query_lexical_features
        with (
            patch.object(magos_agent, "MAGOS_EXTRA_NAMESPACES", set()),
            patch.object(
                magos_agent,
                "build_query_lexical_features",
                wraps=original,
            ) as feature_builder,
        ):
            context = magos.vector_context(
                "remember the task about red blue buttons",
                memory_namespace="shushunya",
                conversation_id="shushunya-main",
                turn_id="current-turn",
            )

        self.assertIn("red and blue buttons", context)
        self.assertEqual(feature_builder.call_count, 1)

    def test_ambiguous_task_wiki_pages_are_bounded_explicit_reference_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki_root = Path(temp_dir) / "wiki"
            pages = wiki_root / "pages"
            pages.mkdir(parents=True)
            definitions = [
                ("note", "note", "NOTE_PAGE"),
                ("wm-player", "task", "PLAYER_PAGE player_controller.gd fire button"),
                ("wm-joystick", "task", "JOYSTICK_PAGE virtual joystick touch controls"),
                ("wm-touch", "task", "TOUCH_PAGE gesture controls and touch zones"),
            ]
            index = {"version": 1, "pages": []}
            for page_id, kind, body in definitions:
                relative = f"pages/{page_id}.md"
                (wiki_root / relative).write_text(body, encoding="utf-8")
                index["pages"].append(
                    {
                        "id": page_id,
                        "title": f"Задача {page_id}" if kind == "task" else "Conversation note",
                        "kind": kind,
                        "path": relative,
                        "updated_at": "2026-07-14T00:00:00+09:00",
                    }
                )
            (wiki_root / "index.json").write_text(
                json.dumps(index, ensure_ascii=False),
                encoding="utf-8",
            )
            magos = magos_agent.Magos(
                Path(temp_dir) / "focus",
                wiki_root,
                lambda *_args, **_kwargs: None,
            )
            semantic = {"0": 0.847, "1": 0.829, "2": 0.813, "3": 0.812}

            with patch.object(magos_agent, "semantic_scores", return_value=semantic):
                ambiguous = magos.wiki_context(
                    "Помнишь задачу которую мы обсуждали раньше?",
                    limit=4,
                )
                exact = magos.wiki_context("wm-player player_controller.gd", limit=4)
            with patch.object(magos_agent, "semantic_scores", return_value=None):
                fallback_ambiguous = magos.wiki_context(
                    "Помнишь задачу которую мы обсуждали раньше?",
                    limit=4,
                )
                fallback_work = magos.wiki_context(
                    "Помнишь работу о которой мы болтали?",
                    limit=4,
                )
                fallback_work_en = magos.wiki_context(
                    "Remember the work we spoke about earlier?",
                    limit=4,
                )
                fallback_assignment = magos.wiki_context(
                    "Remember the assignment we discussed?",
                    limit=4,
                )
                fallback_delo = magos.wiki_context(
                    "Помнишь то дело, которое обсуждали?",
                    limit=4,
                )
            with patch.object(
                magos_agent,
                "semantic_scores",
                return_value={"0": 0.100, "1": 0.400, "2": 0.300},
            ):
                low_semantic_exact = magos.wiki_context(
                    "wm-player player_controller.gd",
                    limit=4,
                )

            self.assertIn("NOTE_PAGE", ambiguous)
            self.assertIn("PLAYER_PAGE", ambiguous)
            self.assertIn("JOYSTICK_PAGE", ambiguous)
            self.assertNotIn("TOUCH_PAGE", ambiguous)
            self.assertEqual(ambiguous.count("task_reference=ambiguous_candidate"), 2)
            self.assertEqual(ambiguous.count("authority=reference_only"), 2)
            self.assertIn("candidate_id=wm-player", ambiguous)
            self.assertIn("candidate_id=wm-joystick", ambiguous)
            self.assertIn("do_not_assume_execution_binding=true", ambiguous)
            self.assertEqual(
                fallback_ambiguous.count("task_reference=ambiguous_candidate"),
                2,
            )
            self.assertEqual(fallback_ambiguous.count("authority=reference_only"), 2)
            self.assertNotIn("TOUCH_PAGE", fallback_ambiguous)
            self.assertEqual(fallback_work.count("task_reference=ambiguous_candidate"), 2)
            self.assertEqual(fallback_work_en.count("task_reference=ambiguous_candidate"), 2)
            self.assertEqual(fallback_assignment.count("task_reference=ambiguous_candidate"), 2)
            self.assertEqual(fallback_delo.count("task_reference=ambiguous_candidate"), 2)
            self.assertIn("PLAYER_PAGE", exact)
            self.assertNotIn("JOYSTICK_PAGE", exact)
            self.assertIn("task_reference=identified", exact)
            self.assertIn("authority=reference_only", exact)
            self.assertIn("PLAYER_PAGE", low_semantic_exact)
            self.assertIn("rank=", low_semantic_exact)
            self.assertIn("semantic=0.400", low_semantic_exact)
            self.assertIn("lexical=", low_semantic_exact)
            self.assertNotIn(" score=", low_semantic_exact)

    def test_lone_vague_task_page_is_surfaced_as_weak_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki_root = Path(temp_dir) / "wiki"
            pages = wiki_root / "pages"
            pages.mkdir(parents=True)
            relative = "pages/lone-task.md"
            (wiki_root / relative).write_text(
                "LONE_TASK_PAGE internal implementation details",
                encoding="utf-8",
            )
            (wiki_root / "index.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "pages": [
                            {
                                "id": "lone-task",
                                "title": "Задача lone-task",
                                "kind": "task",
                                "path": relative,
                                "updated_at": "2026-07-14T00:00:00+09:00",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            magos = magos_agent.Magos(
                Path(temp_dir) / "focus",
                wiki_root,
                lambda *_args, **_kwargs: None,
            )

            with patch.object(
                magos_agent,
                "semantic_scores",
                return_value={"0": 0.820},
            ):
                context = magos.wiki_context("Помнишь задачу?", limit=4)
            with patch.object(magos_agent, "semantic_scores", return_value=None):
                fallback_context = magos.wiki_context("Помнишь задачу?", limit=4)

            self.assertIn("LONE_TASK_PAGE", context)
            self.assertIn("task_reference=weak_candidate", context)
            self.assertIn("authority=reference_only", context)
            self.assertIn("candidate_id=lone-task", context)
            self.assertIn("do_not_assume_execution_binding=true", context)
            self.assertIn("LONE_TASK_PAGE", fallback_context)
            self.assertIn("task_reference=weak_candidate", fallback_context)
            self.assertIn("authority=reference_only", fallback_context)


if __name__ == "__main__":
    unittest.main()
