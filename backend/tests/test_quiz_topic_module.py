"""知识点模块归类回归测试。"""

import unittest

from backend.app.services.quiz import _topic_module


def catalog_for(topic: str = "", category: str = "") -> dict:
    return {
        "aliases": {},
        "categories": {topic.casefold().replace(" ", ""): category}
        if topic and category
        else {},
    }


class QuizTopicModuleTests(unittest.TestCase):
    def test_workbuddy_workflow_topics_are_ai_app_development(self):
        for topic in (
            "入口判断",
            "备料",
            "query增强",
            "能力预筛",
            "能力展开",
            "任务规划",
            "文件元信息预读",
            "need_clarification",
        ):
            with self.subTest(topic=topic):
                self.assertEqual(
                    "AI 应用开发",
                    _topic_module(topic, catalog_for(topic, "意图识别与调度")),
                )

    def test_ai_evaluation_topics_remain_ai_testing(self):
        self.assertEqual("AI 测试", _topic_module("幻觉评测", catalog_for("幻觉评测", "AI 测试")))
        self.assertEqual("AI 测试", _topic_module("Prompt 注入", catalog_for("Prompt 注入", "AI 测试")))

    def test_traditional_testing_topics_remain_traditional_testing(self):
        self.assertEqual(
            "传统软件测试",
            _topic_module("HTTP 状态码", catalog_for("HTTP 状态码", "接口测试")),
        )
        self.assertEqual(
            "传统软件测试",
            _topic_module("接口自动化", catalog_for("接口自动化", "接口测试")),
        )

    def test_unknown_topic_is_not_silently_classified_as_traditional_testing(self):
        self.assertEqual("待分类", _topic_module("普通知识点", catalog_for()))

    def test_ai_application_development_workflow_topics_are_ai_app(self):
        for topic, category in (
            ("SDD", "需求工程"),
            ("项目级skills", "工程规范"),
            ("个人skills", "工程规范"),
            ("验收标准", "AI研发流程"),
            ("温度采样", "AI大模型"),
        ):
            with self.subTest(topic=topic):
                self.assertEqual("AI 应用开发", _topic_module(topic, catalog_for(topic, category)))


if __name__ == "__main__":
    unittest.main()
