"""错题复盘调度回归测试；全部使用临时数据目录。"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from backend.app import prompts
from backend.app.services import quiz as quiz_service
from backend.app.services import review as review_service
from backend.app.services import expression as expression_service


class ReviewScheduleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        quiz_dir = self.data_dir / "quiz"
        self.review_file = quiz_dir / "review_schedule.json"
        self._originals = {
            "review": (
                review_service.REVIEW_FILE,
                review_service.SYNC_QUEUE_FILE,
                review_service.REVIEW_META_FILE,
            ),
            "quiz": (
                quiz_service.QUIZ_DIR,
                quiz_service.PAPERS_DIR,
                quiz_service.ATTEMPTS_DIR,
                quiz_service.MISTAKES_FILE,
                quiz_service.MASTERY_FILE,
                quiz_service.MASTERY_META_FILE,
                quiz_service.TOPICS_FILE,
            ),
        }
        review_service.REVIEW_FILE = self.review_file
        review_service.SYNC_QUEUE_FILE = quiz_dir / "review_sync_queue.json"
        review_service.REVIEW_META_FILE = quiz_dir / "review_meta.json"
        quiz_service.QUIZ_DIR = quiz_dir
        quiz_service.PAPERS_DIR = quiz_dir / "papers"
        quiz_service.ATTEMPTS_DIR = quiz_dir / "attempts"
        quiz_service.MISTAKES_FILE = quiz_dir / "mistakes.json"
        quiz_service.MASTERY_FILE = quiz_dir / "mastery.json"
        quiz_service.MASTERY_META_FILE = quiz_dir / "mastery_meta.json"
        quiz_service.TOPICS_FILE = quiz_dir / "topics.json"
        quiz_service.ensure_dirs()

    def tearDown(self):
        (
            review_service.REVIEW_FILE,
            review_service.SYNC_QUEUE_FILE,
            review_service.REVIEW_META_FILE,
        ) = self._originals["review"]
        (
            quiz_service.QUIZ_DIR,
            quiz_service.PAPERS_DIR,
            quiz_service.ATTEMPTS_DIR,
            quiz_service.MISTAKES_FILE,
            quiz_service.MASTERY_FILE,
            quiz_service.MASTERY_META_FILE,
            quiz_service.TOPICS_FILE,
        ) = self._originals["quiz"]
        self._tmp.cleanup()

    @staticmethod
    def _question(topic="RAG 分块", stem="Chunk Overlap 的作用是什么？", correct="B"):
        return {
            "topic": topic,
            "type": "single",
            "stem": stem,
            "options": [
                {"key": "A", "text": "加快检索"},
                {"key": "B", "text": "避免上下文被边界切断"},
            ],
            "correct_answer": [correct],
            "correct_answer_text": correct,
            "user_answer": ["A"],
            "user_answer_text": "A",
            "is_correct": False,
            "explanation": "Overlap 让相邻块保留重叠内容。",
            "source_title": "RAG 讲义",
            "source_doc_id": "doc_rag",
            "source_docs": [{"doc_id": "doc_rag", "title": "RAG 讲义"}],
        }

    def _paper(self, questions, context=None):
        return {
            "paper_id": "paper_test",
            "title": "测试卷",
            "doc_ids": ["doc_rag"],
            "doc_titles": ["RAG 讲义"],
            "source_docs": [{"doc_id": "doc_rag", "title": "RAG 讲义"}],
            "questions": questions,
            "generation_context": context or {},
        }

    def test_legacy_mistakes_are_migrated_without_loss(self):
        mistake = {
            **self._question(),
            "key": self._question()["stem"][:120],
            "wrong_count": 2,
            "created_at": int(time.time()) - 86400,
            "last_seen_at": int(time.time()) - 86400,
        }
        quiz_service.MISTAKES_FILE.write_text(
            json.dumps([mistake], ensure_ascii=False), encoding="utf-8"
        )

        created = review_service.migrate_legacy_mistakes()
        records = review_service.list_records()
        remaining = quiz_service.list_mistakes()

        self.assertEqual(1, created)
        self.assertEqual(1, len(records))
        self.assertEqual(mistake["key"], records[0]["question_key"])
        self.assertEqual("doc_rag", records[0]["source_lineage"]["source_doc_id"])
        self.assertEqual(0, records[0]["next_review_at"])
        self.assertEqual(1, len(remaining))

    def test_wrong_answer_schedules_and_correct_answer_keeps_schedule(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        record = review_service.list_records()[0]

        self.assertEqual("active", record["status"])
        self.assertEqual(0, record["review_stage"])
        self.assertGreater(record["next_review_at"], int(time.time()))
        self.assertEqual(1, record["wrong_count"])

        correct = dict(question)
        correct.update({"is_correct": True, "user_answer": ["B"], "user_answer_text": "B"})
        review_service.sync_wrong_answers([correct], self._paper([correct]))
        unchanged = review_service.list_records()[0]

        self.assertEqual(record["review_stage"], unchanged["review_stage"])
        self.assertEqual(record["next_review_at"], unchanged["next_review_at"])

    def test_review_practice_pass_advances_and_failure_resets(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        review_id = review_service.list_records()[0]["review_id"]
        review_service.REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = json.loads(review_service.REVIEW_FILE.read_text(encoding="utf-8"))
        state["records"][0]["next_review_at"] = 0
        review_service.REVIEW_FILE.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

        correct = dict(question)
        correct.update({"is_correct": True, "user_answer": ["B"], "user_answer_text": "B"})
        context = {
            "kind": "review_practice",
            "review_ids": [review_id],
        }
        result = review_service.sync_grade([correct], self._paper([correct], context))
        advanced = review_service.list_records()[0]

        self.assertEqual({"passed": True, "review_count": 1}, result)
        self.assertEqual(1, advanced["review_stage"])
        self.assertEqual(1, advanced["review_pass_count"])
        self.assertGreater(advanced["next_review_at"], int(time.time()))

        wrong = dict(question)
        wrong.update({"user_answer": ["A"], "user_answer_text": "A"})
        review_service.sync_grade([wrong], self._paper([wrong], context))
        reset = review_service.list_records()[0]

        self.assertEqual(0, reset["review_stage"])
        self.assertEqual(0, reset["review_pass_count"])
        self.assertEqual("active", reset["status"])

    def test_four_passes_graduate_record(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        review_id = review_service.list_records()[0]["review_id"]
        context = {"kind": "review_practice", "review_ids": [review_id]}
        correct = dict(question)
        correct.update({"is_correct": True, "user_answer": ["B"], "user_answer_text": "B"})

        for expected_passes in range(1, 5):
            state = json.loads(review_service.REVIEW_FILE.read_text(encoding="utf-8"))
            state["records"][0]["next_review_at"] = 0
            review_service.REVIEW_FILE.write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            review_service.sync_grade([correct], self._paper([correct], context))
            record = review_service.list_records()[0]
            self.assertEqual(expected_passes, record["review_pass_count"])

        self.assertEqual("graduated", record["status"])
        self.assertEqual(0, record["next_review_at"])

    def test_manual_delete_archives_without_removing_history(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        key = question["stem"][:120]
        quiz_service.MISTAKES_FILE.write_text(
            json.dumps([{**question, "key": key}], ensure_ascii=False),
            encoding="utf-8",
        )

        quiz_service.delete_mistake(key)
        records = review_service.list_records()

        self.assertEqual([], quiz_service.list_mistakes())
        self.assertEqual("archived", records[0]["status"])
        self.assertTrue(records[0]["question_key"])

    def test_practice_paper_binds_due_records(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        state = json.loads(review_service.REVIEW_FILE.read_text(encoding="utf-8"))
        state["records"][0]["next_review_at"] = 0
        review_service.REVIEW_FILE.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        fake_paper = {
            **self._paper([question]),
            "paper_id": "generated_review_paper",
        }
        with (
            patch(
                "backend.app.services.knowledge.list_documents",
                return_value=[{"doc_id": "doc_rag", "title": "RAG 讲义"}],
            ),
            patch(
                "backend.app.services.quiz.generate_paper",
                return_value=fake_paper,
            ) as fake_generate,
        ):
            paper = review_service.generate_practice_paper("rag分块")

        self.assertEqual("generated_review_paper", paper["paper_id"])
        self.assertEqual("review_practice", paper["generation_context"]["kind"])
        self.assertEqual(1, len(paper["generation_context"]["review_ids"]))
        fake_generate.assert_called_once()
        self.assertTrue(
            (quiz_service.PAPERS_DIR / "generated_review_paper.json").exists()
        )

    def test_error_cause_is_validated_and_saved(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        review_id = review_service.list_records()[0]["review_id"]

        record = review_service.update_error_cause(review_id, "概念不清")
        with self.assertRaises(review_service.ReviewError):
            review_service.update_error_cause(review_id, "不存在的错因")

        self.assertEqual(["概念不清"], record["error_causes"])

    def test_failed_sync_can_be_retried(self):
        question = self._question()
        paper = self._paper([question])
        review_service.enqueue_failed_sync(
            [question],
            paper,
            RuntimeError("temporary sync failure"),
        )

        result = review_service.retry_pending_syncs()

        self.assertEqual({"retried": 1, "pending_count": 0}, result)
        self.assertEqual(1, len(review_service.list_records()))

    def test_review_streak_counts_one_day_once(self):
        day1 = int(time.mktime(time.strptime("2026-09-01 10:00:00", "%Y-%m-%d %H:%M:%S")))
        day2 = day1 + 86400

        meta1 = review_service.update_completion_streak(day1)
        same_day = review_service.update_completion_streak(day1 + 3600)
        meta2 = review_service.update_completion_streak(day2)

        self.assertEqual(1, meta1["current_streak"])
        self.assertEqual(1, same_day["total_completed_days"])
        self.assertEqual(2, same_day["total_passed_sessions"])
        self.assertEqual(2, meta2["current_streak"])
        self.assertEqual(2, meta2["longest_streak"])
        self.assertEqual(2, meta2["total_completed_days"])

    def test_generated_expression_question_is_cached(self):
        expression_dir = self.data_dir / "expression"
        generated_dir = expression_dir / "generated_questions"
        originals = {
            "session_dir": expression_service.SESSION_DIR,
            "keyword_dir": expression_service.KEYWORD_CACHE_DIR,
            "material_dir": expression_service.SPEAKING_MATERIAL_CACHE_DIR,
            "generated_dir": expression_service.GENERATED_QUESTION_CACHE_DIR,
        }
        expression_service.SESSION_DIR = expression_dir / "sessions"
        expression_service.KEYWORD_CACHE_DIR = expression_dir / "keyword_cache"
        expression_service.SPEAKING_MATERIAL_CACHE_DIR = expression_dir / "materials"
        expression_service.GENERATED_QUESTION_CACHE_DIR = generated_dir
        payload = {
            "question": "请解释固定大小分块怎么配置？",
            "reference_answer": "固定大小分块会设置 chunk_size 和 overlap。",
            "keywords": ["固定大小分块"],
        }
        try:
            with patch(
                "backend.app.services.expression._get_or_start_generation",
                return_value=Queue(),
            ) as fake_start:
                fake_start.return_value.put_nowait(payload)
                with (
                    patch("backend.app.services.expression._topic_matches", return_value=False),
                    patch(
                        "backend.app.services.expression._search_chunks_for_topic",
                        return_value=[{"title": "RAG 讲义"}],
                    ),
                ):
                    first = expression_service.drill_question(
                        "ai_app_dev",
                        "固定大小分块",
                        ["doc_rag"],
                        "wrong_review",
                    )
                    second = expression_service.drill_question(
                        "ai_app_dev",
                        "固定大小分块",
                        ["doc_rag"],
                        "wrong_review",
                    )
            self.assertTrue(generated_dir.exists())
            self.assertEqual(1, fake_start.call_count)
            self.assertEqual(first["question"], second["question"])
        finally:
            expression_service.SESSION_DIR = originals["session_dir"]
            expression_service.KEYWORD_CACHE_DIR = originals["keyword_dir"]
            expression_service.SPEAKING_MATERIAL_CACHE_DIR = originals["material_dir"]
            expression_service.GENERATED_QUESTION_CACHE_DIR = originals["generated_dir"]

    def test_expression_question_merges_same_topic_mistakes_into_one_open_question(self):
        first = self._question()
        second = self._question(
            stem="Chunk overlap 越大越好。",
            correct="B",
        )
        second.update(
            {
                "type": "judge",
                "options": [
                    {"key": "A", "text": "正确"},
                    {"key": "B", "text": "错误"},
                ],
                "explanation": "Overlap 过大会增加重复内容。",
            }
        )
        review_service.sync_wrong_answers(
            [first, second],
            self._paper([first, second]),
        )
        review_id = review_service.list_records()[0]["review_id"]
        fake_payload = {
            "question": "在实际切分文档时，你会如何设置 chunk overlap，为什么？",
            "reference_answer": "我会保留适度重叠，避免语义被边界切断，同时控制重复内容。",
            "keywords": ["chunk overlap", "边界切断", "重复内容"],
            "standard_key_points": ["适度重叠", "控制重复内容"],
            "reference_script": "结论先行：我会保留适度重叠，再说明原因和取舍。",
            "source_title": "RAG 讲义",
        }
        captured = {}

        def fake_generate(role_name, topic, records, question_type=""):
            captured["records"] = records
            return fake_payload

        expression_dir = self.data_dir / "expression"
        with (
            patch.object(
                expression_service,
                "GENERATED_QUESTION_CACHE_DIR",
                expression_dir / "generated_questions",
            ),
            patch.object(
                expression_service,
                "_prefetch_speaking_materials",
            ),
            patch.object(
                expression_service,
                "_generate_mistake_question_via_llm",
                side_effect=fake_generate,
            ),
            patch(
                "backend.app.services.question_bank.load_role_questions",
                return_value=[],
            ) as load_bank,
        ):
            result = expression_service.drill_question(
                "ai_app_dev",
                "RAG 分块",
                [],
                "wrong_review",
                "mistake",
                review_id,
            )

        load_bank.assert_not_called()
        self.assertEqual("mistake", result["source_type"])
        self.assertEqual(review_id, result["source_ref"])
        self.assertEqual(2, result["group_size"])
        self.assertEqual("RAG 分块", result["group_topic"])
        self.assertEqual(fake_payload["question"], result["question"])
        self.assertNotRegex(result["question"], r"(下列|哪项|正确的是|判断题)")
        self.assertEqual(2, len(captured["records"]))
        self.assertEqual("RAG 讲义", result["source_title"])
        self.assertEqual("RAG 分块", result["section"])

    def test_mistake_expression_fallback_uses_open_question_without_options(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        review_id = review_service.list_records()[0]["review_id"]
        expression_dir = self.data_dir / "expression"

        with (
            patch.object(
                expression_service,
                "GENERATED_QUESTION_CACHE_DIR",
                expression_dir / "generated_questions",
            ),
            patch.object(
                expression_service,
                "SPEAKING_MATERIAL_CACHE_DIR",
                expression_dir / "materials",
            ),
            patch.object(expression_service, "_prefetch_speaking_materials"),
            patch.object(
                expression_service,
                "_generate_mistake_question_via_llm",
                return_value=None,
            ),
        ):
            result = expression_service.drill_question(
                "ai_app_dev",
                "RAG 分块",
                [],
                "wrong_review",
                "mistake",
                review_id,
            )

        self.assertFalse(result["generated_by_llm"])
        self.assertIn("请结合你的理解", result["question"])
        self.assertIn("RAG 分块", result["question"])
        self.assertNotRegex(result["question"], r"(下列|哪项|正确的是|判断题)")
        self.assertIn("Overlap 让相邻块保留重叠内容。", result["reference_answer"])
        self.assertIn("避免上下文被边界切断", result["reference_answer"])
        self.assertNotIn("B.", result["reference_answer"])

    def test_mistake_fallback_strips_option_by_option_analysis(self):
        record = self._question(
            topic="auto-compact",
            stem="auto-compact 的作用是什么？",
            correct="B",
        )
        record.update(
            {
                "options": [
                    {"key": "A", "text": "只做日志轮转"},
                    {
                        "key": "B",
                        "text": "在上下文变长时硬压缩历史，使连续上下文继续可用",
                    },
                ],
                "explanation": (
                    "Claude Code 靠 auto-compact 在上下文变长时硬压缩历史，"
                    "让连续上下文继续用下去，这也带来上下文越多行为越激进的特点。"
                    "B 是 WorkBuddy 派生子 Agent 的分层思路；"
                    "C 是任务清单持久化机制；"
                    "D 描述的是确定性反馈，与 auto-compact 的压缩职责无关。"
                ),
            }
        )

        answer = expression_service._mistake_spoken_reference([record])

        self.assertIn("硬压缩历史", answer)
        self.assertIn("上下文越多行为越激进", answer)
        self.assertNotIn("B 是", answer)
        self.assertNotIn("C 是", answer)
        self.assertNotIn("D 描述", answer)
        self.assertNotIn("无关", answer)
        self.assertLessEqual(len(answer), 260)

    def test_mistake_fallback_merges_group_into_one_paragraph(self):
        single = self._question(
            topic="Agent = LLM + Harness",
            stem="Harness 的角色是什么？",
            correct="B",
        )
        single.update(
            {
                "options": [
                    {"key": "A", "text": "LLM 本身"},
                    {
                        "key": "B",
                        "text": "让 LLM 以某种组织形式干活的“身体 + 工作制度”",
                    },
                ],
                "explanation": (
                    "资料把 Agent 拆成 LLM 和 Harness 两个角色："
                    "LLM 是大脑，Harness 是让大脑干活的组织形式。"
                    "B 项正确。A 项混淆了两个角色。"
                ),
            }
        )
        judge = {
            "topic": "Agent = LLM + Harness",
            "type": "judge",
            "stem": "Harness 决定聪明大脑以什么组织形式干活。",
            "options": [
                {"key": "A", "text": "正确"},
                {"key": "B", "text": "错误"},
            ],
            "correct_answer": ["A"],
            "correct_answer_text": "A. 正确",
            "explanation": (
                "资料明确给出公式 Agent = LLM + Harness："
                "LLM 是大脑，Harness 是让大脑干活的组织形式。"
                "同一个聪明模型放在不同 Harness 下产出可能完全不同。"
                "因此该说法正确。"
            ),
            "source_title": "ClaudeCode原理解析",
        }

        answer = expression_service._mistake_spoken_reference([single, judge])

        self.assertEqual(1, answer.count("简单说"))
        self.assertEqual(1, len([line for line in answer.splitlines() if line.strip()]))
        self.assertIn("身体 + 工作制度", answer)
        self.assertNotIn("简单说，正确", answer)
        self.assertNotIn("B 项正确", answer)
        self.assertLessEqual(len(answer), 260)

    def test_expression_question_rejects_missing_wrong_review_record(self):
        with self.assertRaisesRegex(
            expression_service.ExpressionError,
            "复盘记录不存在",
        ):
            expression_service.drill_question(
                "ai_app_dev",
                "RAG 分块",
                [],
                "wrong_review",
                "mistake",
                "rv_missing",
            )

    def test_knowledge_question_pool_caps_at_five(self):
        expression_dir = self.data_dir / "expression"
        calls = {"count": 0}

        def fake_generate(
            role_name,
            topic_hint,
            chunks,
            question_type="",
            existing_questions=None,
        ):
            calls["count"] += 1
            index = calls["count"]
            return {
                "question": f"请解释知识点 {index} 的核心原理？",
                "reference_answer": f"第 {index} 个知识点的结论和依据。",
                "keywords": [f"知识点{index}"],
                "standard_key_points": [f"要点{index}"],
                "reference_script": f"结论{index}。",
                "source_title": "测试文档",
            }

        with (
            patch.object(
                expression_service,
                "GENERATED_QUESTION_CACHE_DIR",
                expression_dir / "generated_questions",
            ),
            patch.object(
                expression_service,
                "SPEAKING_MATERIAL_CACHE_DIR",
                expression_dir / "materials",
            ),
            patch.object(
                expression_service,
                "KNOWLEDGE_POOL_DIR",
                expression_dir / "knowledge_pools",
            ),
            patch.object(expression_service, "_prefetch_speaking_materials"),
            patch.object(
                expression_service,
                "_search_chunks_for_topic",
                return_value=[
                    {"doc_id": "doc_x", "title": "测试文档", "text": "知识点内容"}
                ],
            ),
            patch.object(
                expression_service,
                "_generate_question_via_llm",
                side_effect=fake_generate,
            ),
        ):
            results = [
                expression_service.drill_question(
                    "ai_app_dev",
                    "知识点",
                    ["doc_x"],
                    "knowledge",
                    "knowledge",
                    "",
                    "concept",
                    f"n{index}",
                )
                for index in range(1, 6)
            ]
            sixth = expression_service.drill_question(
                "ai_app_dev",
                "知识点",
                ["doc_x"],
                "knowledge",
                "knowledge",
                "",
                "concept",
                "n6",
            )

        self.assertEqual(5, calls["count"])
        self.assertEqual([1, 2, 3, 4, 5], [item["pool_index"] for item in results])
        self.assertTrue(all(item["pool_exhausted"] is False for item in results))
        self.assertTrue(sixth["pool_exhausted"])
        self.assertEqual(5, len(sixth["pool_questions"]))
        self.assertEqual(5, sixth["pool_index"])
        self.assertIn("已生成 5 道", sixth["pool_message"])

    def test_knowledge_question_pool_does_not_count_duplicate(self):
        expression_dir = self.data_dir / "expression"

        def fake_generate(
            role_name,
            topic_hint,
            chunks,
            question_type="",
            existing_questions=None,
        ):
            return {
                "question": "请解释 RAG 分块的核心作用？",
                "reference_answer": "分块是为了控制上下文长度，同时保留语义完整。",
                "keywords": ["RAG", "分块"],
                "standard_key_points": ["控制上下文长度"],
                "reference_script": "结论先行。",
                "source_title": "测试文档",
            }

        with (
            patch.object(
                expression_service,
                "GENERATED_QUESTION_CACHE_DIR",
                expression_dir / "generated_questions",
            ),
            patch.object(
                expression_service,
                "SPEAKING_MATERIAL_CACHE_DIR",
                expression_dir / "materials",
            ),
            patch.object(
                expression_service,
                "KNOWLEDGE_POOL_DIR",
                expression_dir / "knowledge_pools",
            ),
            patch.object(expression_service, "_prefetch_speaking_materials"),
            patch.object(
                expression_service,
                "_search_chunks_for_topic",
                return_value=[
                    {"doc_id": "doc_x", "title": "测试文档", "text": "RAG 分块"}
                ],
            ),
            patch.object(
                expression_service,
                "_generate_question_via_llm",
                side_effect=fake_generate,
            ),
        ):
            first = expression_service.drill_question(
                "ai_app_dev",
                "知识点",
                ["doc_x"],
                "knowledge",
                "knowledge",
                "",
                "concept",
                "n1",
            )
            second = expression_service.drill_question(
                "ai_app_dev",
                "知识点",
                ["doc_x"],
                "knowledge",
                "knowledge",
                "",
                "concept",
                "n2",
            )

        self.assertFalse(first["pool_exhausted"])
        self.assertTrue(second["pool_exhausted"])
        self.assertEqual(1, len(second["pool_questions"]))
        self.assertIn("覆盖得差不多", second["pool_message"])

    def test_knowledge_pool_is_shared_across_roles(self):
        expression_dir = self.data_dir / "expression"
        calls = {"count": 0}

        def fake_generate(
            role_name,
            topic_hint,
            chunks,
            question_type="",
            existing_questions=None,
        ):
            calls["count"] += 1
            return {
                "question": "请解释知识点的核心原理？",
                "reference_answer": "先给结论，再说明依据。",
                "keywords": ["知识点"],
                "standard_key_points": ["核心原理"],
                "reference_script": "结论先行。",
                "source_title": "测试文档",
            }

        with (
            patch.object(
                expression_service,
                "GENERATED_QUESTION_CACHE_DIR",
                expression_dir / "generated_questions",
            ),
            patch.object(
                expression_service,
                "SPEAKING_MATERIAL_CACHE_DIR",
                expression_dir / "materials",
            ),
            patch.object(
                expression_service,
                "KNOWLEDGE_POOL_DIR",
                expression_dir / "knowledge_pools",
            ),
            patch.object(expression_service, "_prefetch_speaking_materials"),
            patch.object(
                expression_service,
                "_search_chunks_for_topic",
                return_value=[
                    {"doc_id": "doc_x", "title": "测试文档", "text": "知识点内容"}
                ],
            ),
            patch.object(
                expression_service,
                "_generate_question_via_llm",
                side_effect=fake_generate,
            ),
        ):
            first = expression_service.drill_question(
                "ai_app_dev",
                "知识点",
                ["doc_x"],
                "knowledge",
                "knowledge",
                "",
                "concept",
                "same-nonce",
            )
            second = expression_service.drill_question(
                "software_qa",
                "知识点",
                ["doc_x"],
                "knowledge",
                "knowledge",
                "",
                "concept",
                "same-nonce",
            )

        self.assertEqual(first["question"], second["question"])
        self.assertEqual(1, calls["count"])

    def test_expression_prompt_omits_role_and_lists_existing(self):
        prompt = prompts.build_expression_generate_user(
            "",
            "知识点",
            [{"doc_id": "doc_x", "title": "测试文档", "text": "内容"}],
            "concept",
            ["旧问题"],
        )

        self.assertNotIn("【练习方向】", prompt)
        self.assertIn("【已生成题目】", prompt)
        self.assertIn("旧问题", prompt)

    def test_first_chunks_for_documents_accepts_string_chunks(self):
        with (
            patch(
                "backend.app.services.knowledge.list_documents",
                return_value=[{"doc_id": "doc_x", "title": "测试文档"}],
            ),
            patch(
                "backend.app.services.knowledge.load_document",
                return_value={
                    "title": "测试文档",
                    "chunks": ["字符串片段一", "字符串片段二"],
                },
            ),
        ):
            chunks = expression_service._first_chunks_for_documents(["doc_x"])

        self.assertEqual(2, len(chunks))
        self.assertEqual("字符串片段一", chunks[0]["text"])
        self.assertEqual("测试文档", chunks[0]["title"])

    def test_slow_generation_returns_close_question_quickly(self):
        expression_dir = self.data_dir / "expression"
        question_item = {
            "label": "AD1",
            "question": "请解释固定大小分块的 chunk_size 和 overlap？",
            "reference_answer": "chunk_size 是每块长度，overlap 是相邻块重叠。",
            "keywords": ["固定大小分块"],
        }
        originals = {
            "fast_wait": expression_service.EXPRESSION_GENERATE_FAST_WAIT_SECONDS,
        }
        expression_service.EXPRESSION_GENERATE_FAST_WAIT_SECONDS = 0.1
        try:
            with (
                patch(
                    "backend.app.services.question_bank.load_role_questions",
                    return_value=[question_item],
                ),
                patch("backend.app.services.expression._topic_matches", return_value=False),
                patch(
                    "backend.app.services.expression._search_chunks_for_topic",
                    return_value=[{"title": "RAG 讲义"}],
                ),
                patch(
                    "backend.app.services.expression._get_or_start_generation",
                    return_value=Queue(),
                ),
            ):
                started = time.time()
                result = expression_service.drill_question(
                    "ai_app_dev",
                    "固定大小分块",
                    ["doc_rag"],
                    "wrong_review",
                )
            self.assertLess(time.time() - started, 2)
            self.assertFalse(result["generated_by_llm"])
            self.assertEqual("AD1", result["label"])
            self.assertIn("最接近", result["disclaimer"])
        finally:
            expression_service.EXPRESSION_GENERATE_FAST_WAIT_SECONDS = originals["fast_wait"]


    def test_today_new_wrong_answer_is_visible_and_practicable(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))

        dashboard = review_service.get_dashboard()
        self.assertEqual(1, dashboard["today_new_count"])
        self.assertEqual(0, dashboard["due_count"])
        self.assertEqual(1, len(dashboard["today_tasks"]))
        self.assertEqual("rag分块", dashboard["today_tasks"][0]["topic_id"])

        with self.assertRaises(review_service.ReviewError):
            review_service.generate_practice_paper("rag分块")

        fake_paper = {
            **self._paper([question]),
            "paper_id": "generated_today_review_paper",
        }
        with (
            patch(
                "backend.app.services.knowledge.list_documents",
                return_value=[{"doc_id": "doc_rag", "title": "RAG 讲义"}],
            ),
            patch(
                "backend.app.services.quiz.generate_paper",
                return_value=fake_paper,
            ) as fake_generate,
        ):
            paper = review_service.generate_practice_paper(
                "rag分块",
                include_today=True,
            )

        self.assertEqual("generated_today_review_paper", paper["paper_id"])
        self.assertTrue(paper["generation_context"]["include_today"])
        self.assertEqual(1, len(paper["generation_context"]["review_ids"]))
        fake_generate.assert_called_once()

    def test_today_new_leaves_after_pass_and_stays_after_fail(self):
        question = self._question()
        review_service.sync_wrong_answers([question], self._paper([question]))
        self.assertEqual(1, review_service.get_dashboard()["today_new_count"])

        review_id = review_service.list_records()[0]["review_id"]
        context = {"kind": "review_practice", "review_ids": [review_id]}
        correct = dict(question)
        correct.update({"is_correct": True, "user_answer": ["B"], "user_answer_text": "B"})
        review_service.sync_grade([correct], self._paper([correct], context))
        self.assertEqual(0, review_service.get_dashboard()["today_new_count"])

        wrong = self._question(stem="另一道今日错题")
        review_service.sync_wrong_answers([wrong], self._paper([wrong]))
        self.assertEqual(1, review_service.get_dashboard()["today_new_count"])
        failed_id = [
            item["review_id"]
            for item in review_service.list_records()
            if item["stem"] == "另一道今日错题"
        ][0]
        failed_context = {"kind": "review_practice", "review_ids": [failed_id]}
        failed = dict(wrong)
        failed.update({"user_answer": ["A"], "user_answer_text": "A"})
        review_service.sync_grade([failed], self._paper([failed], failed_context))
        self.assertEqual(1, review_service.get_dashboard()["today_new_count"])

    def test_dashboard_tasks_keep_their_own_topic_titles(self):
        first = self._question(topic="RAG 分块", stem="第一道错题")
        second = self._question(topic="Transformer 架构", stem="第二道错题")
        review_service.sync_wrong_answers([first, second], self._paper([first, second]))

        dashboard = review_service.get_dashboard()
        today_by_id = {
            task["topic_id"]: task["topic"]
            for task in dashboard["today_tasks"]
        }
        self.assertEqual("RAG 分块", today_by_id["rag分块"])
        self.assertEqual("Transformer 架构", today_by_id["transformer架构"])
        self.assertEqual(2, dashboard["today_new_count"])

if __name__ == "__main__":
    unittest.main()
