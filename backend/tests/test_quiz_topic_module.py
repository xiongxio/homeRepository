"""知识点模块归类回归测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services import quiz as quiz_service
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

    def test_workbuddy_product_topics_are_ai_app_development(self):
        for topic, category in (
            ("工具抽象/数据互通/操作串联", "能力体系"),
            ("工具抽象", "产品定位"),
            ("数据互通", "产品定位"),
            ("操作串联", "产品定位"),
            ("技能/专家/灵感三级能力", "能力体系"),
            ("本地文件5层防护", "数据安全"),
            ("沙箱目录限制", "数据安全"),
            ("数据上云策略", "数据安全"),
            ("操作日志与回滚", "数据安全"),
            ("人工审批", "数据安全"),
            ("CodeBuddy/QClaw/WorkBuddy关系", "产品背景"),
        ):
            with self.subTest(topic=topic):
                self.assertEqual("AI 应用开发", _topic_module(topic, catalog_for(topic, category)))

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

    def test_pending_topics_follow_document_title_rules(self):
        for topic, title in (
            ("兜底机制", "WorkBuddy_小白精讲+面试话术"),
            ("结果喂回模型", "ClaudeCode原理解析_小白精讲+面试话术"),
            ("轻框架重模型", "ClaudeCode原理解析_小白精讲+面试话术"),
        ):
            with self.subTest(topic=topic):
                self.assertEqual(
                    "AI 应用开发",
                    _topic_module(topic, catalog_for(), {title}),
                )

    def test_pending_topics_without_source_stay_pending(self):
        for topic in ("兜底机制", "结果喂回模型", "轻框架重模型"):
            with self.subTest(topic=topic):
                self.assertEqual("待分类", _topic_module(topic, catalog_for()))
                self.assertEqual("待分类", _topic_module(topic, catalog_for(), set()))

    def test_document_rule_never_overrides_existing_classification(self):
        for topic, category in (
            ("控制器选型", "机器人与智能硬件测试"),
            ("AI代码审计原理与架构", "AI 测试"),
        ):
            with self.subTest(topic=topic):
                catalog = catalog_for(topic, category)
                before = _topic_module(topic, catalog)
                after = _topic_module(topic, catalog, {"小白精讲+面试话术"})
                self.assertNotEqual("待分类", before)
                self.assertEqual(before, after)

    def test_interview_without_document_rule_does_not_propagate(self):
        self.assertEqual(
            "待分类",
            _topic_module("普通知识点", catalog_for(), {"机器人测试面试通关指南"}),
        )

    def test_missing_rules_file_keeps_safe_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.json"
            with (
                patch.object(quiz_service, "DOC_DOMAIN_RULES_FILE", missing),
                patch.object(quiz_service, "BUILTIN_DOC_DOMAIN_RULES_FILE", missing),
            ):
                self.assertEqual(
                    "待分类",
                    _topic_module("兜底机制", catalog_for(), {"小白精讲"}),
                )

    def test_topic_source_titles_prefer_question_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            papers_dir = Path(temp_dir)
            (papers_dir / "paper.json").write_text(
                json.dumps(
                    {
                        "doc_titles": ["混合资料", "小白精讲+面试话术"],
                        "questions": [
                            {
                                "topic": "兜底机制",
                                "source_title": "WorkBuddy_小白精讲+面试话术",
                            },
                            {
                                "topic": "无精确来源",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(quiz_service, "PAPERS_DIR", papers_dir):
                index = quiz_service._topic_source_titles()

        self.assertEqual(
            {"WorkBuddy_小白精讲+面试话术"},
            index["兜底机制"],
        )
        self.assertEqual(
            {"混合资料", "小白精讲+面试话术"},
            index["无精确来源"],
        )

    def test_existing_mastery_classifications_are_unchanged(self):
        mastery = quiz_service._read_json(quiz_service.MASTERY_FILE, {})
        if not isinstance(mastery, dict) or not mastery:
            self.skipTest("没有本地掌握度数据，跳过基线对比。")

        catalog = quiz_service._topic_catalog()
        source_title_index = quiz_service._topic_source_titles()
        changed: list[tuple[str, str, str]] = []
        for topic, stat in mastery.items():
            if not isinstance(stat, dict) or int(stat.get("total", 0) or 0) <= 0:
                continue
            before = _topic_module(topic, catalog)
            after = _topic_module(
                topic,
                catalog,
                quiz_service._source_titles_for_topic(
                    topic,
                    catalog,
                    source_title_index,
                ),
            )
            if before != "待分类" and before != after:
                changed.append((topic, before, after))

        self.assertEqual([], changed)


if __name__ == "__main__":
    unittest.main()
