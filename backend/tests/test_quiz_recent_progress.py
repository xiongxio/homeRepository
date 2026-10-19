import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services import quiz as quiz_service


class QuizRecentProgressTest(unittest.TestCase):
    """近 7 天薄弱知识点聚合、样本分档和来源归因测试。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.quiz_dir = self.root / "quiz"
        self.papers_dir = self.quiz_dir / "papers"
        self.attempts_dir = self.quiz_dir / "attempts"
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.attempts_dir.mkdir(parents=True, exist_ok=True)

        self.doc_a = {
            "doc_id": "doc-a",
            "title": "Agent 应用开发指南",
            "source_type": "text",
            "source_url": "",
            "chunk_count": 2,
            "char_count": 100,
            "created_at": 1000,
        }
        self.doc_b = {
            "doc_id": "doc-b",
            "title": "Prompt 工程实践",
            "source_type": "text",
            "source_url": "",
            "chunk_count": 2,
            "char_count": 120,
            "created_at": 1001,
        }
        self.knowledge_patch = patch.object(
            quiz_service.knowledge_service,
            "list_documents",
            return_value=[self.doc_a, self.doc_b],
        )
        self.knowledge_patch.start()
        self._originals = {
            name: getattr(quiz_service, name)
            for name in (
                "QUIZ_DIR",
                "PAPERS_DIR",
                "ATTEMPTS_DIR",
                "MISTAKES_FILE",
                "TOPICS_FILE",
            )
        }
        quiz_service.QUIZ_DIR = self.quiz_dir
        quiz_service.PAPERS_DIR = self.papers_dir
        quiz_service.ATTEMPTS_DIR = self.attempts_dir
        quiz_service.MISTAKES_FILE = self.quiz_dir / "mistakes.json"
        self.topics_file = self.quiz_dir / "topics.json"
        self.topics_file.write_text("{}", encoding="utf-8")
        quiz_service.TOPICS_FILE = self.topics_file
        quiz_service._reset_recent_topic_progress_cache()

    def tearDown(self):
        quiz_service._reset_recent_topic_progress_cache()
        for name, value in self._originals.items():
            setattr(quiz_service, name, value)
        self.knowledge_patch.stop()
        self._temp.cleanup()

    def _write_paper(
        self, paper_id: str, title: str, source_docs: list[dict] | None = None
    ) -> None:
        (self.papers_dir / f"{paper_id}.json").write_text(
            json.dumps(
                {
                    "paper_id": paper_id,
                    "title": title,
                    "source_docs": source_docs or [],
                    "questions": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _write_attempt(
        self,
        attempt_id: str,
        created_at: int,
        questions: list[dict],
        *,
        paper_id: str = "",
        paper_title: str = "",
        review: dict | None = None,
    ) -> None:
        (self.attempts_dir / f"{attempt_id}.json").write_text(
            json.dumps(
                {
                    "attempt_id": attempt_id,
                    "paper_id": paper_id,
                    "paper_title": paper_title,
                    "questions": questions,
                    "review": review or {},
                    "created_at": created_at,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _question(
        topic: str,
        is_correct: bool,
        qtype: str = "single",
        *,
        source_doc_id: str = "",
        source_title: str = "",
    ) -> dict:
        return {
            "topic": topic,
            "type": qtype,
            "is_correct": is_correct,
            "source_doc_id": source_doc_id,
            "source_title": source_title,
        }

    def test_recent_window_sample_states_and_source_docs(self):
        now = int(time.time())
        self._write_attempt(
            "old",
            now - 8 * 86400,
            [self._question("旧薄弱点", False) for _ in range(5)],
        )
        self._write_attempt(
            "recent",
            now - 86400,
            [
                self._question(
                    "可靠薄弱点",
                    True,
                    source_doc_id="doc-a",
                    source_title="Agent 应用开发指南",
                ),
                self._question(
                    "可靠薄弱点",
                    False,
                    "multiple",
                    source_doc_id="doc-a",
                    source_title="Agent 应用开发指南",
                ),
                self._question(
                    "可靠薄弱点",
                    False,
                    source_doc_id="doc-a",
                    source_title="Agent 应用开发指南",
                ),
                self._question(
                    "小样本",
                    False,
                    source_doc_id="doc-b",
                    source_title="Prompt 工程实践",
                ),
                self._question(
                    "小样本",
                    False,
                    source_doc_id="doc-b",
                    source_title="Prompt 工程实践",
                ),
                *[
                    self._question(
                        "已掌握",
                        True,
                        source_doc_id="doc-b",
                        source_title="Prompt 工程实践",
                    )
                    for _ in range(4)
                ],
            ],
            review={
                "weak_topics": [
                    {
                        "topic": "可靠薄弱点",
                        "diagnosis": "基于当前题目和有限原文片段的初步判断：把框架层当成了 Harness。",
                    }
                ]
            },
        )

        result = quiz_service.get_recent_topic_progress(7)
        topics = [item["topic"] for item in result["weak_topics"]]

        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["answered"], 9)
        self.assertEqual(result["source_count"], 2)
        self.assertNotIn("旧薄弱点", topics)
        self.assertEqual(topics, ["可靠薄弱点", "小样本"])

        reliable = result["weak_topics"][0]
        self.assertEqual(reliable["sample_state"], "verified")
        self.assertEqual(reliable["total"], 3)
        self.assertEqual(reliable["correct"], 1)
        self.assertEqual(reliable["wrong_count"], 2)
        self.assertEqual(reliable["diagnosis"], "把框架层当成了 Harness。")
        self.assertEqual(reliable["source_docs"][0]["title"], "Agent 应用开发指南")
        self.assertEqual(reliable["source_docs"][0]["count"], 3)

        tentative = result["weak_topics"][1]
        self.assertEqual(tentative["sample_state"], "tentative")
        self.assertEqual(tentative["total"], 2)
        self.assertEqual(tentative["diagnosis"], "")
        self.assertEqual(tentative["source_docs"][0]["title"], "Prompt 工程实践")

    def test_paper_source_fallback_and_latest_diagnosis(self):
        now = int(time.time())
        self._write_paper(
            "paper-1",
            "单文档测验",
            [{"doc_id": "doc-a", "title": "Agent 应用开发指南"}],
        )
        self._write_attempt(
            "older",
            now - 2 * 86400,
            [self._question("函数调用", False) for _ in range(3)],
            paper_id="paper-1",
            paper_title="单文档测验",
            review={
                "weak_topics": [
                    {"topic": "函数调用", "diagnosis": "旧诊断"},
                ]
            },
        )
        self._write_attempt(
            "newer",
            now - 86400,
            [self._question("函数调用", False)],
            paper_id="paper-1",
            paper_title="单文档测验",
            review={
                "weak_topics": [
                    {"topic": "函数调用", "diagnosis": "新诊断：未区分参数和返回值。"},
                ]
            },
        )

        result = quiz_service.get_recent_topic_progress(7)
        item = result["weak_topics"][0]

        self.assertEqual(item["total"], 4)
        self.assertEqual(item["diagnosis"], "新诊断：未区分参数和返回值。")
        self.assertEqual(item["source_docs"][0]["title"], "Agent 应用开发指南")
        self.assertEqual(item["source_docs"][0]["count"], 4)

    def test_no_recent_attempts(self):
        now = int(time.time())
        self._write_attempt(
            "old",
            now - 30 * 86400,
            [self._question("旧知识点", False)],
        )

        result = quiz_service.get_recent_topic_progress(7)

        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["answered"], 0)
        self.assertEqual(result["weak_topics"], [])

    def test_get_progress_exposes_recent_board(self):
        now = int(time.time())
        self._write_attempt(
            "recent",
            now - 3600,
            [
                self._question("入口判断", False),
                self._question("入口判断", False),
                self._question("入口判断", True),
            ],
        )
        guide = {
            "type_stats": [],
            "module_stats": [],
            "mastered_topics": [],
            "focus_topics": [],
            "next_paper": {},
            "interview_ready": False,
            "interview_reasons": [],
            "summary": "",
            "scope_note": "",
        }
        with (
            patch.object(quiz_service, "_ensure_mastery_schema", return_value={}),
            patch.object(quiz_service, "_build_learning_guide", return_value=guide),
            patch.object(quiz_service, "list_mistakes", return_value=[]),
        ):
            result = quiz_service.get_progress()

        self.assertEqual(result["recent_window"]["attempts"], 1)
        self.assertEqual(result["recent_window"]["answered"], 3)
        self.assertEqual(result["recent_weak_topics"][0]["topic"], "入口判断")
        self.assertEqual(result["recent_weak_topics"][0]["accuracy"], 0.333)


if __name__ == "__main__":
    unittest.main()
