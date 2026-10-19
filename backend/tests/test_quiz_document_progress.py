import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services import quiz as quiz_service


class QuizDocumentProgressTest(unittest.TestCase):
    """get_document_progress 的只读聚合与归因规则测试。"""

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
        quiz_service.QUIZ_DIR = self.quiz_dir
        quiz_service.PAPERS_DIR = self.papers_dir
        quiz_service.ATTEMPTS_DIR = self.attempts_dir
        quiz_service.MISTAKES_FILE = self.quiz_dir / "mistakes.json"

    def tearDown(self):
        self.knowledge_patch.stop()
        self._temp.cleanup()

    def _write_paper(self, paper_id: str, title: str, source_docs: list[dict] | None = None) -> None:
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
        questions: list[dict],
        paper_id: str = "",
        paper_title: str = "",
    ) -> None:
        (self.attempts_dir / f"{attempt_id}.json").write_text(
            json.dumps(
                {
                    "attempt_id": attempt_id,
                    "paper_id": paper_id,
                    "paper_title": paper_title,
                    "questions": questions,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _write_mistakes(self, mistakes: list[dict]) -> None:
        self.quiz_dir.mkdir(parents=True, exist_ok=True)
        (self.quiz_dir / "mistakes.json").write_text(
            json.dumps(mistakes, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_exact_source_attribution_and_weak_topics(self):
        self._write_paper("paper-1", "单文档测验")
        self._write_attempt(
            "attempt-1",
            [
                {
                    "topic": "函数调用",
                    "is_correct": True,
                    "source_doc_id": "doc-a",
                    "source_title": "Agent 应用开发指南",
                },
                {
                    "topic": "函数调用",
                    "is_correct": False,
                    "source_doc_id": "doc-a",
                    "source_title": "Agent 应用开发指南",
                },
                {
                    "topic": "上下文窗口",
                    "is_correct": False,
                    "source_doc_id": "doc-b",
                    "source_title": "Prompt 工程实践",
                },
            ],
            paper_id="paper-1",
            paper_title="单文档测验",
        )

        result = quiz_service.get_document_progress()
        by_id = {item["doc_id"]: item for item in result["documents"]}
        self.assertEqual(result["unattributed_total"], 0)
        self.assertEqual(by_id["doc-a"]["total"], 2)
        self.assertEqual(by_id["doc-a"]["correct"], 1)
        self.assertEqual(by_id["doc-a"]["accuracy"], 0.5)
        self.assertEqual(by_id["doc-a"]["attempt_count"], 1)
        self.assertEqual(by_id["doc-b"]["total"], 1)
        self.assertEqual(by_id["doc-b"]["accuracy"], 0.0)

        weak = by_id["doc-a"]["weak_topics"]
        self.assertEqual(len(weak), 1)
        self.assertEqual(weak[0]["topic"], "函数调用")
        self.assertEqual(weak[0]["total"], 2)
        self.assertEqual(weak[0]["streak"], 0)
        self.assertEqual(by_id["doc-a"]["topics"][0]["topic"], "函数调用")

    def test_single_doc_paper_fallback(self):
        self._write_paper(
            "paper-2",
            "Prompt 专项卷",
            [{"doc_id": "doc-b", "title": "Prompt 工程实践"}],
        )
        self._write_attempt(
            "attempt-2",
            [
                {
                    "topic": "结构化输出",
                    "is_correct": True,
                    "source_doc_id": "",
                    "source_title": "",
                }
            ],
            paper_id="paper-2",
            paper_title="Prompt 专项卷",
        )

        result = quiz_service.get_document_progress()
        by_id = {item["doc_id"]: item for item in result["documents"]}
        self.assertIn("doc-b", by_id)
        self.assertEqual(by_id["doc-b"]["attribution"]["paper"], 1)

    def test_multi_doc_title_match(self):
        self._write_paper(
            "paper-3",
            "复合卷",
            [
                {"doc_id": "doc-a", "title": "Agent 应用开发指南"},
                {"doc_id": "doc-b", "title": "Prompt 工程实践"},
            ],
        )
        self._write_attempt(
            "attempt-3",
            [
                {
                    "topic": "MCP",
                    "is_correct": False,
                    "source_doc_id": "",
                    "source_title": "Prompt 工程实践",
                }
            ],
            paper_id="paper-3",
            paper_title="复合卷",
        )

        result = quiz_service.get_document_progress()
        by_id = {item["doc_id"]: item for item in result["documents"]}
        self.assertEqual(by_id["doc-b"]["total"], 1)
        self.assertEqual(by_id["doc-b"]["attribution"]["title"], 1)
        self.assertNotIn("doc-a", by_id)

    def test_unattributed_bucket_and_no_data_docs(self):
        self._write_paper("paper-4", "无来源卷")
        self._write_attempt(
            "attempt-4",
            [
                {
                    "topic": "未知知识点",
                    "is_correct": False,
                    "source_doc_id": "",
                    "source_title": "不存在的资料",
                }
            ],
            paper_id="paper-4",
            paper_title="无来源卷",
        )

        result = quiz_service.get_document_progress()
        self.assertEqual(result["unattributed_total"], 1)
        self.assertEqual(len(result["documents"]), 0)
        self.assertEqual(
            {item["doc_id"] for item in result["no_data_docs"]},
            {"doc-a", "doc-b"},
        )

    def test_active_mistake_count_from_metadata(self):
        self._write_paper(
            "paper-5",
            "Agent 丢失卷",
            [{"doc_id": "doc-a", "title": "Agent 应用开发指南"}],
        )
        self._write_attempt(
            "attempt-5",
            [
                {
                    "topic": "Agent 缺陷",
                    "is_correct": True,
                    "source_doc_id": "doc-a",
                    "source_title": "Agent 应用开发指南",
                }
            ],
            paper_id="paper-5",
            paper_title="Agent 丢失卷",
        )
        self._write_mistakes(
            [
                {
                    "key": "q1",
                    "stem": "哪项属于 Agent 缺陷？",
                    "topic": "Agent 缺陷",
                    "source_doc_id": "doc-a",
                    "paper_id": "paper-5",
                },
                {
                    "key": "q2",
                    "stem": "哪项属于 Prompt 缺陷？",
                    "topic": "Prompt 缺陷",
                    "source_doc_id": "",
                    "paper_id": "paper-5",
                },
            ]
        )

        result = quiz_service.get_document_progress()
        by_id = {item["doc_id"]: item for item in result["documents"]}
        self.assertEqual(by_id["doc-a"]["active_mistake_count"], 2)


if __name__ == "__main__":
    unittest.main()
