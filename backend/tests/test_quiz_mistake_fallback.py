import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services import quiz as quiz_service
from backend.app.services.quiz import QuizError


class QuizMistakeFallbackTest(unittest.TestCase):
    """错题重练在模型不可用时的原题兜底测试。"""

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
            "created_at": 1000,
        }
        self.knowledge_patch = patch.object(
            quiz_service.knowledge_service,
            "list_documents",
            return_value=[self.doc_a],
        )
        self.knowledge_patch.start()
        self._originals = {
            name: getattr(quiz_service, name)
            for name in ("QUIZ_DIR", "PAPERS_DIR", "ATTEMPTS_DIR", "MISTAKES_FILE", "TOPICS_FILE")
        }
        quiz_service.QUIZ_DIR = self.quiz_dir
        quiz_service.PAPERS_DIR = self.papers_dir
        quiz_service.ATTEMPTS_DIR = self.attempts_dir
        quiz_service.MISTAKES_FILE = self.quiz_dir / "mistakes.json"
        quiz_service.TOPICS_FILE = self.quiz_dir / "topics.json"
        self.topics_file = self.quiz_dir / "topics.json"
        self.topics_file.write_text("{}", encoding="utf-8")

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(quiz_service, name, value)
        self.knowledge_patch.stop()
        self._temp.cleanup()

    @staticmethod
    def _question(
        qid: str,
        topic: str,
        qtype: str,
        stem: str,
        *,
        correct_answer: list[str],
        is_correct: bool,
        doc_id: str = "doc-a",
    ) -> dict:
        return {
            "question_id": qid,
            "type": qtype,
            "stem": stem,
            "options": [
                {"key": "A", "text": "选项A"},
                {"key": "B", "text": "选项B"},
                {"key": "C", "text": "选项C"},
                {"key": "D", "text": "选项D"},
            ],
            "topic": topic,
            "difficulty": "medium",
            "user_answer": ["B"] if is_correct else correct_answer[:1],
            "correct_answer": correct_answer,
            "is_correct": is_correct,
            "explanation": f"{stem}的解析",
            "source_title": "Agent 应用开发指南",
            "source_doc_id": doc_id,
            "source_docs": [{"doc_id": doc_id, "title": "Agent 应用开发指南"}],
            "source_chunk_ids": [],
            "user_answer_text": "选项A",
            "correct_answer_text": "选项A",
        }

    def _write_attempt(self, attempt_id: str, questions: list[dict]) -> None:
        (self.attempts_dir / f"{attempt_id}.json").write_text(
            json.dumps(
                {
                    "attempt_id": attempt_id,
                    "paper_id": "paper-1",
                    "paper_title": "测试卷",
                    "questions": questions,
                    "review": {},
                    "created_at": 1000,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_current_scope_uses_original_mistakes_when_generation_fails(self):
        questions = [
            self._question(
                "q1", "RAG", "single", "什么是 RAG？",
                correct_answer=["A"], is_correct=False,
            ),
            self._question(
                "q2", "Agent", "single", "什么是 Agent？",
                correct_answer=["A"], is_correct=False,
            ),
        ]
        self._write_attempt("attempt-fail", questions)

        with patch.object(
            quiz_service, "generate_paper", side_effect=QuizError("模型暂时不可用")
        ):
            paper = quiz_service.generate_mistake_paper(
                scope="current",
                attempt_id="attempt-fail",
                limit=8,
            )

        context = paper["generation_context"]
        self.assertEqual(context["kind"], "mistake_redo_fallback")
        self.assertEqual(context["scope"], "current")
        self.assertEqual(context["source_attempt_id"], "attempt-fail")
        self.assertTrue(context["fallback_reason"])
        self.assertGreaterEqual(len(paper["questions"]), 1)
        stems = {question["stem"] for question in paper["questions"]}
        self.assertIn("什么是 RAG？", stems)
        self.assertIn("什么是 Agent？", stems)

    def test_current_scope_keeps_normal_generation_when_model_works(self):
        questions = [
            self._question(
                "q1", "RAG", "single", "什么是 RAG？",
                correct_answer=["A"], is_correct=False,
            ),
        ]
        self._write_attempt("attempt-ok", questions)
        fake_paper = {
            "paper_id": "fake-paper",
            "title": "变形题",
            "keywords": [],
            "questions": [
                {
                    "question_id": "q1",
                    "type": "single",
                    "stem": "RAG 的变形题",
                    "options": [
                        {"key": "A", "text": "选项A"},
                        {"key": "B", "text": "选项B"},
                        {"key": "C", "text": "选项C"},
                        {"key": "D", "text": "选项D"},
                    ],
                    "answer": ["A"],
                }
            ],
        }

        with patch.object(quiz_service, "generate_paper", return_value=fake_paper):
            paper = quiz_service.generate_mistake_paper(
                scope="current",
                attempt_id="attempt-ok",
                limit=8,
            )

        self.assertEqual(paper["generation_context"]["kind"], "mistake_redo")
        self.assertEqual(paper["paper_id"], "fake-paper")

