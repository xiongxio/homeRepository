"""组卷"有效题目不足"回归测试。

覆盖 2026-10-20 暴露的三个根因：
1. 提示词不得再把历史题干原文贴给模型（会让模型逐字复述旧题）
2. 知识点少而题量多时，提示词必须给出知识点分配上限
3. 只差少量题目时应当交付已有题目，而不是整份作废
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import prompts
from backend.app.services import quiz as quiz_service


class QuizPromptAvoidListTests(unittest.TestCase):
    def test_history_stems_are_not_injected_into_prompt(self):
        """历史题干原文一旦进入提示词，模型会直接复述，必须杜绝。"""
        history = [
            "在开发AI Agent时，工程师需要封装一个发送邮件的工具。根据资料，应该怎么做？",
            "某公司需要处理大量扫描版合同和复杂PDF表格，应该优先选择哪个多模态模型？",
        ]
        built = prompts.build_quiz_user(
            "【学习资料】正文", ["AI Agent"], {"single": 2}, "mixed", history, [], []
        )
        for stem in history:
            self.assertNotIn(stem, built)
        # 仍然要保留"换角度"的约束，否则模型不知道该避开什么
        self.assertIn("换角度", built)
        self.assertIn("不要重复出题", built)

    def test_no_history_list_adds_no_avoid_section(self):
        built = prompts.build_quiz_user(
            "【学习资料】正文", ["AI Agent"], {"single": 2}, "mixed", [], [], []
        )
        self.assertNotIn("已经考过的题目", built)

    def test_topic_distribution_cap_is_added_when_topics_fewer_than_questions(self):
        built = prompts.build_quiz_user(
            "【学习资料】正文",
            ["AI Agent", "多模态LLM", "Prompt工程"],
            {"single": 10, "multiple": 6, "judge": 4},
            "mixed",
            [],
            [],
            [],
        )
        self.assertIn("知识点分配", built)
        # 20 道题分给 3 个知识点 -> 每个知识点最多 7 道
        self.assertIn("每个知识点最多出 7 道", built)
        self.assertIn("必须覆盖全部 3 个知识点", built)

    def test_single_topic_paper_allows_all_questions_on_that_topic(self):
        """单知识点出 5 道题时上限必须是 5，不能再被"最多 2 道"卡住。"""
        built = prompts.build_quiz_user(
            "【学习资料】正文", ["FastAPI"], {"single": 5}, "mixed", [], [], []
        )
        self.assertIn("每个知识点最多出 5 道", built)


class QuizShortPaperTests(unittest.TestCase):
    """缺 1~2 道题时应交付已有题目，而不是整份作废。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._papers_dir = quiz_service.PAPERS_DIR
        quiz_service.PAPERS_DIR = Path(self._tmp.name) / "papers"
        quiz_service.PAPERS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        quiz_service.PAPERS_DIR = self._papers_dir
        self._tmp.cleanup()

    @staticmethod
    def _raw_question(index: int, qtype: str = "single") -> dict:
        # 题干必须彼此真正不同：去重用的是中文 2-gram Jaccard，
        # 只有改一个数字的题干会被判为重复。
        stems = [
            "向量数据库把文本映射为高维向量，从而支持按语义而非关键词检索。",
            "检索增强生成先从知识库召回片段，再把片段交给大模型组织答案。",
            "工具调用让模型以结构化参数请求外部函数接口执行具体动作。",
            "多智能体分工协作时，调度器负责把子任务分派给不同的执行体。",
            "模型上下文窗口决定了单次请求最多能容纳多少输入 token。",
            "结构化输出约束模型返回固定 JSON 字段，降低下游解析成本。",
            "评估环节需要同时看检索命中率和最终答案的准确率。",
            "提示词里的少样本示例会影响模型对任务格式的理解。",
        ]
        return {
            "type": qtype,
            "stem": stems[index % len(stems)],
            "options": [
                {"key": "A", "text": "选项甲"},
                {"key": "B", "text": "选项乙"},
                {"key": "C", "text": "选项丙"},
                {"key": "D", "text": "选项丁"},
            ],
            "answer": ["A"],
            "explanation": "解析正文。" * 12,
            "topic": "测试知识点",
            "difficulty": "medium",
            "source_title": "资料一",
        }

    def _run_with_raw(self, raw_questions: list[dict], total: int):
        """用固定的原始题目桩掉 LLM，跑通 generate_paper 的兜底链路。"""

        def fake_request(material, keywords, counts, difficulty, avoid_stems, weak_topics, coverage_hint):
            return {"title": "测试卷", "questions": raw_questions}

        with (
            patch.object(quiz_service, "_collect_material",
                         return_value=("【资料】正文", [], [], [])),
            patch.object(quiz_service, "_topic_catalog", return_value={}),
            patch.object(quiz_service, "list_weak_topics", return_value=[]),
            patch.object(quiz_service, "_recent_stems", return_value=[]),
            patch.object(quiz_service, "_request_quiz_raw", side_effect=fake_request),
        ):
            return quiz_service.generate_paper(
                ["测试知识点"], None, total, 0, 0, "mixed", False, "normal", "", []
            )

    def test_short_paper_is_delivered_instead_of_raising(self):
        """要 5 道只出 4 道：应交付 4 道并给出缺题说明，而不是抛 QuizError。"""
        raw = [self._raw_question(i) for i in range(4)]
        paper = self._run_with_raw(raw, total=5)

        self.assertEqual(4, len(paper["questions"]))
        self.assertIn("4/5", paper["shortfall_note"])
        self.assertIn("单选题缺 1 道", paper["shortfall_note"])

    def test_well_short_paper_still_raises(self):
        """要 5 道只出 1 道：缺口过大，仍应报错让用户重试。"""
        raw = [self._raw_question(0)]
        with self.assertRaises(quiz_service.QuizError):
            self._run_with_raw(raw, total=5)

    def test_complete_paper_has_no_shortfall_note(self):
        raw = [self._raw_question(i) for i in range(5)]
        paper = self._run_with_raw(raw, total=5)

        self.assertEqual(5, len(paper["questions"]))
        self.assertEqual("", paper["shortfall_note"])


if __name__ == "__main__":
    unittest.main()
