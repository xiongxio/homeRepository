"""复盘接口冒烟测试；不调用真实大模型，也不写入真实数据目录。"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import quiz as quiz_service
from backend.app.services import review as review_service


class ReviewApiSmokeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self._tmp.name)
        quiz_dir = data_dir / "quiz"
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
        self.client = TestClient(app)

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
    def _mistake():
        stem = "API 自动化断言应该校验什么？"
        return {
            "key": stem[:120],
            "topic": "接口自动化",
            "type": "single",
            "stem": stem,
            "options": [
                {"key": "A", "text": "只校验 HTTP 200"},
                {"key": "B", "text": "状态码、关键业务字段和契约"},
            ],
            "correct_answer": ["B"],
            "correct_answer_text": "B",
            "user_answer_text": "A",
            "explanation": "接口断言要覆盖业务结果，而不是只看状态码。",
            "source_title": "接口测试讲义",
            "wrong_count": 1,
            "created_at": int(time.time()) - 86400,
            "last_seen_at": int(time.time()) - 86400,
        }

    def test_health_dashboard_records_error_cause_and_practice_api(self):
        mistake = self._mistake()
        quiz_service.MISTAKES_FILE.write_text(
            json.dumps([mistake], ensure_ascii=False), encoding="utf-8"
        )

        health = self.client.get("/api/health")
        dashboard = self.client.get("/api/quiz/review/dashboard")
        records = self.client.get("/api/quiz/review/mistakes")
        knowledge_map = self.client.get("/api/quiz/review/knowledge-map")

        self.assertEqual(200, health.status_code)
        self.assertEqual(200, dashboard.status_code)
        self.assertEqual(200, records.status_code)
        self.assertEqual(200, knowledge_map.status_code)
        self.assertEqual(1, dashboard.json()["due_count"])
        self.assertEqual(1, len(records.json()["records"]))

        review_id = records.json()["records"][0]["review_id"]
        saved = self.client.put(
            f"/api/quiz/review/{review_id}/error-causes",
            json={"cause": "概念混淆"},
        )
        self.assertEqual(200, saved.status_code)
        self.assertEqual(["概念混淆"], saved.json()["record"]["error_causes"])

        fake_paper = {
            "paper_id": "paper_api_review",
            "title": "复盘练习",
            "questions": [],
            "generation_context": {
                "kind": "review_practice",
                "topic_id": "接口自动化",
                "review_ids": [review_id],
            },
        }
        with (
            patch.object(
                review_service,
                "generate_practice_paper",
                return_value=fake_paper,
            ),
            patch.object(
                quiz_service,
                "get_paper",
                return_value=fake_paper,
            ),
        ):
            practice = self.client.post("/api/quiz/review/tasks/%E6%8E%A5%E5%8F%A3%E8%87%AA%E5%8A%A8%E5%8C%96/practice")

        self.assertEqual(200, practice.status_code)
        self.assertEqual("review_practice", practice.json()["generation_context"]["kind"])

    def test_sync_retry_endpoint(self):
        retry = self.client.post("/api/quiz/review/sync-retry")

        self.assertEqual(200, retry.status_code)
        self.assertEqual(0, retry.json()["retried"])
        self.assertEqual(0, retry.json()["pending_count"])

    def test_expression_question_api_accepts_wrong_review_source(self):
        question = {
            "topic": "接口自动化",
            "type": "single",
            "stem": "API 自动化断言应该校验什么？",
            "options": [
                {"key": "A", "text": "只校验 HTTP 200"},
                {"key": "B", "text": "状态码、关键业务字段和契约"},
            ],
            "correct_answer": ["B"],
            "correct_answer_text": "B",
            "user_answer": ["A"],
            "user_answer_text": "A",
            "is_correct": False,
            "explanation": "接口断言要覆盖业务结果，而不是只看状态码。",
            "source_title": "接口测试讲义",
            "source_doc_id": "doc_api",
            "source_docs": [{"doc_id": "doc_api", "title": "接口测试讲义"}],
        }
        paper = {
            "paper_id": "paper_expression_source",
            "title": "接口测试卷",
            "questions": [question],
        }
        review_service.sync_wrong_answers([question], paper)
        review_id = review_service.list_records()[0]["review_id"]

        with patch(
            "backend.app.services.question_bank.load_role_questions",
            return_value=[],
        ):
            response = self.client.post(
                "/api/expression/question",
                json={
                    "role_key": "ai_app_dev",
                    "topic": "接口自动化",
                    "source": "wrong_review",
                    "source_type": "mistake",
                    "source_ref": review_id,
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("mistake", body["source_type"])
        self.assertEqual(review_id, body["source_ref"])
        self.assertIn(question["stem"], body["question"])
        self.assertIn("接口断言要覆盖业务结果", body["reference_answer"])

        missing = self.client.post(
            "/api/expression/question",
            json={
                "role_key": "ai_app_dev",
                "source": "wrong_review",
                "source_type": "mistake",
                "source_ref": "rv_missing",
            },
        )
        self.assertEqual(422, missing.status_code)
        self.assertIn("复盘记录不存在", missing.json()["detail"])

    def test_expression_question_api_generates_from_selected_knowledge_docs(self):
        fake_payload = {
            "question": "接口自动化断言到底该校验什么？",
            "reference_answer": "要校验状态码、关键业务字段和契约，不能只看 HTTP 200。",
            "keywords": ["状态码", "业务字段", "契约"],
            "standard_key_points": ["覆盖业务结果", "不只状态码"],
            "reference_script": "先给结论：接口断言要覆盖状态码、关键业务字段和契约；只看 HTTP 200 会漏掉真实业务错误。",
            "source_title": "接口测试讲义",
        }
        with (
            patch(
                "backend.app.services.expression._search_chunks_for_topic",
                return_value=[
                    {
                        "doc_id": "doc_api",
                        "title": "接口测试讲义",
                        "text": "API 自动化断言应该校验状态码、关键业务字段和契约。",
                    }
                ],
            ),
            patch(
                "backend.app.services.expression._generate_question_via_llm",
                return_value=fake_payload,
            ),
            patch(
                "backend.app.services.question_bank.load_role_questions",
                side_effect=AssertionError("知识库题源不应读取岗位真题库"),
            ) as load_bank,
        ):
            response = self.client.post(
                "/api/expression/question",
                json={
                    "role_key": "ai_app_dev",
                    "topic": "接口自动化",
                    "source_doc_ids": ["doc_api"],
                    "source": "knowledge",
                    "source_type": "knowledge",
                    "question_type": "concept",
                },
            )

        load_bank.assert_not_called()
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("knowledge", body["source_type"])
        self.assertIn("doc_api", body["source_ref"])
        self.assertEqual("concept", body["question_type"])
        self.assertTrue(body["generated_by_llm"])
        self.assertIn("接口自动化断言", body["question"])

    def test_expression_question_api_generates_from_active_resume(self):
        fake_payload = {
            "question": "你在接口自动化项目里最值得讲的成果是什么？",
            "reference_answer": "我主导接口自动化，把核心接口覆盖到 200 个，回归耗时从 800ms 降到 220ms。",
            "keywords": ["接口自动化", "200 个", "回归耗时"],
            "standard_key_points": ["真实项目成果", "量化指标"],
            "reference_script": "先给结论：我在接口自动化项目里最值得讲的是覆盖规模和回归耗时；覆盖 200 个接口，回归从 800ms 降到 220ms。",
            "source_title": "测试简历",
        }
        resume_record = {
            "resume_id": "resume_001",
            "title": "测试简历",
            "raw_text": "接口自动化项目：覆盖 200 个接口，回归从 800ms 降到 220ms。",
            "profile": {
                "name": "张三",
                "headline": "接口测试工程师",
                "projects": [
                    {
                        "name": "接口自动化平台",
                        "summary": "覆盖核心接口 200 个",
                        "metrics": ["回归耗时 800ms 降到 220ms"],
                    }
                ],
            },
        }
        with (
            patch(
                "backend.app.services.resume.get_active_resume",
                return_value=resume_record,
            ),
            patch(
                "backend.app.services.expression._generate_question_via_llm",
                return_value=fake_payload,
            ),
            patch(
                "backend.app.services.question_bank.load_role_questions",
                side_effect=AssertionError("简历题源不应读取岗位真题库"),
            ) as load_bank,
        ):
            response = self.client.post(
                "/api/expression/question",
                json={
                    "role_key": "ai_app_dev",
                    "source": "resume",
                    "source_type": "resume",
                    "question_type": "contribution",
                },
            )

        load_bank.assert_not_called()
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("resume", body["source_type"])
        self.assertEqual("resume_001", body["source_ref"])
        self.assertEqual("contribution", body["question_type"])
        self.assertTrue(body["generated_by_llm"])
        self.assertIn("接口自动化项目", body["question"])


    def test_today_new_dashboard_and_include_today_practice_api(self):
        question = {
            "topic": "接口自动化",
            "type": "single",
            "stem": "API 自动化断言应该校验什么？",
            "options": [
                {"key": "A", "text": "只校验 HTTP 200"},
                {"key": "B", "text": "状态码、关键业务字段和契约"},
            ],
            "correct_answer": ["B"],
            "correct_answer_text": "B",
            "user_answer": ["A"],
            "user_answer_text": "A",
            "is_correct": False,
            "explanation": "接口断言要覆盖业务结果。",
            "source_title": "接口测试讲义",
            "source_doc_id": "doc_api",
            "source_docs": [{"doc_id": "doc_api", "title": "接口测试讲义"}],
        }
        paper = {
            "paper_id": "paper_today",
            "title": "今日错题卷",
            "doc_ids": ["doc_api"],
            "doc_titles": ["接口测试讲义"],
            "source_docs": [{"doc_id": "doc_api", "title": "接口测试讲义"}],
            "questions": [question],
            "generation_context": {"kind": "normal_quiz"},
        }
        review_service.sync_wrong_answers([question], paper)

        dashboard = self.client.get("/api/quiz/review/dashboard")
        self.assertEqual(200, dashboard.status_code)
        body = dashboard.json()
        self.assertEqual(1, body["today_new_count"])
        self.assertEqual(0, body["due_count"])
        self.assertEqual(1, len(body["today_tasks"]))
        self.assertEqual("接口自动化", body["today_tasks"][0]["topic"])
        self.assertEqual(0, len(body["tasks"]))

        review_id = body["today_tasks"][0]["review_ids"][0]
        fake_paper = {
            "paper_id": "paper_api_today_review",
            "title": "今日新错即时复盘",
            "questions": [],
            "generation_context": {
                "kind": "review_practice",
                "topic_id": "接口自动化",
                "include_today": True,
                "review_ids": [review_id],
            },
        }
        with (
            patch(
                "backend.app.services.knowledge.list_documents",
                return_value=[{"doc_id": "doc_api", "title": "接口测试讲义"}],
            ),
            patch.object(
                review_service,
                "generate_practice_paper",
                return_value=fake_paper,
            ) as fake_generate,
            patch.object(
                quiz_service,
                "get_paper",
                return_value=fake_paper,
            ),
        ):
            practice = self.client.post(
                "/api/quiz/review/tasks/%E6%8E%A5%E5%8F%A3%E8%87%AA%E5%8A%A8%E5%8C%96/practice?include_today=true"
            )

        self.assertEqual(200, practice.status_code)
        self.assertTrue(practice.json()["generation_context"]["include_today"])
        self.assertEqual(
            True,
            fake_generate.call_args.kwargs.get("include_today")
            if fake_generate.call_args.kwargs
            else fake_generate.call_args.args[2],
        )

if __name__ == "__main__":
    unittest.main()
