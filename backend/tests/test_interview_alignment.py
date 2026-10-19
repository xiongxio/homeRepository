"""面试准备方法对齐接口冒烟测试；不调用真实大模型，不写真实数据目录。"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers import interview as interview_router


class InterviewAlignmentSmokeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.material_patches = [
            patch.object(
                interview_router.resume_service,
                "resolve_profile_text",
                return_value="",
            ),
            patch.object(
                interview_router.resume_service,
                "resolve_profile_dict",
                return_value={},
            ),
            patch(
                "backend.app.services.question_bank.load_role_questions",
                return_value=[],
            ),
        ]
        for item in self.material_patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.material_patches):
            item.stop()

    def _mock_chat_json(self, payload):
        return patch.object(
            interview_router.llm_service,
            "chat_json",
            return_value=payload,
        )

    def test_top_questions_injects_company_and_asks_for_ten(self):
        fake_questions = [
            {
                "question": f"核心问题 {index}",
                "focus": f"考察点 {index}",
                "reference_answer": f"参考回答 {index}",
            }
            for index in range(1, 11)
        ]
        captured = {}

        def fake_chat_json(system, user, **kwargs):
            captured["system"] = system
            captured["user"] = user
            captured["kwargs"] = kwargs
            return {"questions": fake_questions}

        with patch.object(
            interview_router.llm_service,
            "chat_json",
            side_effect=fake_chat_json,
        ):
            response = self.client.post(
                "/api/interview/top-questions",
                json={
                    "company": "测试科技",
                    "jd": "负责大模型应用测试",
                    "resume": "负责过 RAG 评测",
                    "role_key": "ai_qa",
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(10, len(body["questions"]))
        self.assertEqual("核心问题 1", body["questions"][0]["question"])
        self.assertIn("40-60", captured["system"])
        self.assertIn("目标公司", captured["user"])
        self.assertIn("测试科技", captured["user"])
        self.assertIn("必须恰好输出【本次生成数量】", captured["system"])

    def test_top_questions_generates_one_new_question(self):
        captured = {}

        def fake_chat_json(system, user, **kwargs):
            captured["user"] = user
            return {
                "questions": [
                    {
                        "question": "请讲一个你定位过的线上问题。",
                        "focus": "定位与复盘能力",
                        "reference_answer": "先给结论，再讲定位过程。",
                    }
                ]
            }

        with patch.object(
            interview_router.llm_service,
            "chat_json",
            side_effect=fake_chat_json,
        ):
            response = self.client.post(
                "/api/interview/top-questions",
                json={
                    "company": "测试科技",
                    "jd": "负责大模型应用测试",
                    "resume": "负责过 RAG 评测",
                    "role_key": "ai_qa",
                    "count": 1,
                    "existing_questions": ["已有题目：如何设计评测集？"],
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, len(body["questions"]))
        self.assertIn("【本次生成数量】\n1", captured["user"])
        self.assertIn("已有题目：如何设计评测集？", captured["user"])

    def test_interview_answer_returns_40_60_reference(self):
        captured = {}

        def fake_chat_json(messages, **kwargs):
            captured["messages"] = messages
            return {
                "answer": "我在这个自动化项目里负责接口用例建设，核心接口覆盖到 200 个，回归耗时从 800ms 降到 220ms。",
                "key_points": ["结论先行", "给出量化结果"],
            }

        with patch.object(
            interview_router.llm_service,
            "chat_json_messages",
            side_effect=fake_chat_json,
        ):
            response = self.client.post(
                "/api/interview/answer",
                json={
                    "company": "测试科技",
                    "jd": "负责接口自动化测试",
                    "resume": "覆盖 200 个接口",
                    "question": "自动化框架如何保证可维护性？",
                    "candidate_answer": "我用了分层设计。",
                    "role_key": "software_qa",
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertIn("200 个", body["answer"])
        self.assertEqual(["结论先行", "给出量化结果"], body["key_points"])
        self.assertIn("40-60 秒", captured["messages"][0]["content"])
        self.assertIn("自动化框架如何保证可维护性", captured["messages"][1]["content"])
        self.assertIn("候选人目前已说内容", captured["messages"][1]["content"])
        self.assertIn("测试科技", captured["messages"][1]["content"])

    def test_top_questions_normalizes_and_caps_at_ten(self):
        raw_questions = [
            {
                "question": f"核心问题 {index}",
                "focus": f"考察点 {index}",
                "reference_answer": f"参考回答 {index}",
            }
            for index in range(1, 10)
        ]
        raw_questions.extend(
            [
                {"focus": "缺少题干"},
                {"question": "核心问题 1", "focus": "重复题"},
                {"question": "  核心问题 10  ", "focus": None, "reference_answer": 123},
                {"question": "核心问题 11"},
            ]
        )

        with self._mock_chat_json({"questions": raw_questions}):
            response = self.client.post(
                "/api/interview/top-questions",
                json={
                    "company": "测试科技",
                    "jd": "负责大模型应用测试",
                    "resume": "负责过 RAG 评测",
                    "role_key": "ai_qa",
                },
            )

        self.assertEqual(200, response.status_code)
        questions = response.json()["questions"]
        self.assertEqual(10, len(questions))
        self.assertEqual("核心问题 10", questions[-1]["question"])
        self.assertTrue(all(isinstance(item["focus"], str) for item in questions))
        self.assertEqual("", questions[-1]["focus"])
        self.assertEqual("123", questions[-1]["reference_answer"])

    def test_interview_turn_passes_company_into_interviewer_context(self):
        captured = {}

        def fake_chat_json(messages, **kwargs):
            captured["messages"] = messages
            return {
                "speech": "请先做个自我介绍。",
                "is_new_question": True,
                "is_final": False,
                "note": "",
            }

        with patch.object(
            interview_router.llm_service,
            "chat_json_messages",
            side_effect=fake_chat_json,
        ):
            response = self.client.post(
                "/api/interview/turn",
                json={
                    "company": "测试科技",
                    "jd": "负责整机测试",
                    "resume": "做过 AGV 项目",
                    "role_key": "robot_hardware",
                    "history": [],
                    "asked_count": 0,
                    "max_questions": 5,
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual("请先做个自我介绍。", response.json()["speech"])
        self.assertFalse(response.json()["is_new_question"])
        user_content = captured["messages"][1]["content"]
        self.assertIn("【目标公司】", user_content)
        self.assertIn("测试科技", user_content)
        self.assertIn("40-60 秒", captured["messages"][0]["content"])

    def test_interview_turn_follows_planned_questions(self):
        captured = {}

        def fake_chat_json(messages, **kwargs):
            captured["messages"] = messages
            return {
                "speech": "你刚才提到覆盖了 200 个接口，具体怎么保证稳定性？",
                "is_new_question": True,
                "is_final": False,
                "note": "",
            }

        with patch.object(
            interview_router.llm_service,
            "chat_json_messages",
            side_effect=fake_chat_json,
        ):
            response = self.client.post(
                "/api/interview/turn",
                json={
                    "company": "测试科技",
                    "jd": "负责接口自动化测试",
                    "resume": "覆盖 200 个接口",
                    "role_key": "software_qa",
                    "history": [
                        {"role": "interviewer", "content": "请先做个自我介绍。"},
                        {"role": "candidate", "content": "我有五年测试经验。"},
                    ],
                    "asked_count": 0,
                    "max_questions": 3,
                    "planned_questions": ["问题 A", "问题 B", "问题 C"],
                },
            )

        self.assertEqual(200, response.status_code)
        user_content = captured["messages"][1]["content"]
        self.assertIn("【本场面试题目提纲", user_content)
        self.assertIn("问题 A", user_content)
        self.assertIn("下一道主问题应为：问题 A", user_content)
