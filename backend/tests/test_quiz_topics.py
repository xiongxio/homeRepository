"""关键词提炼加载策略测试。"""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services import quiz as quiz_service


class QuizTopicLoadingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._topics_file = quiz_service.TOPICS_FILE
        quiz_service.TOPICS_FILE = Path(self._tmp.name) / "topics.json"

    def tearDown(self):
        quiz_service.TOPICS_FILE = self._topics_file
        self._tmp.cleanup()

    @staticmethod
    def _documents():
        return [{"doc_id": "doc-1", "title": "资料一", "char_count": 100}]

    def test_automatic_full_library_load_uses_cache_without_calling_llm(self):
        cached_topics = [{"keyword": "缓存关键词", "aliases": [], "category": "测试", "weight": 5}]
        quiz_service.TOPICS_FILE.write_text(
            json.dumps(
                {
                    "__all__": {
                        "signature": "doc-1:100",
                        "topics": cached_topics,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with (
            patch.object(quiz_service.knowledge_service, "list_documents", return_value=self._documents()),
            patch.object(quiz_service.llm_service, "chat_json") as chat_json,
        ):
            result = quiz_service.extract_topics([], allow_full_scan=False)

        self.assertEqual(cached_topics, result)
        chat_json.assert_not_called()

    def test_automatic_full_library_load_without_cache_does_not_call_llm(self):
        with (
            patch.object(quiz_service.knowledge_service, "list_documents", return_value=self._documents()),
            patch.object(quiz_service.llm_service, "chat_json") as chat_json,
        ):
            result = quiz_service.extract_topics([], allow_full_scan=False)

        self.assertEqual([], result)
        chat_json.assert_not_called()

    def test_manual_full_library_load_still_calls_llm(self):
        with (
            patch.object(quiz_service.knowledge_service, "list_documents", return_value=self._documents()),
            patch.object(quiz_service, "_load_document_text", return_value="资料正文"),
            patch.object(
                quiz_service.llm_service,
                "chat_json",
                return_value={"topics": []},
            ) as chat_json,
        ):
            result = quiz_service.extract_topics([], allow_full_scan=True)

        self.assertEqual([], result)
        chat_json.assert_called_once()

    def test_concurrent_same_scope_requests_share_one_llm_call(self):
        documents = self._documents()
        entered = threading.Event()
        release = threading.Event()

        def slow_chat_json(*args, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return {"topics": []}

        with (
            patch.object(quiz_service.knowledge_service, "list_documents", return_value=documents),
            patch.object(quiz_service, "_load_document_text", return_value="资料正文"),
            patch.object(quiz_service.llm_service, "chat_json", side_effect=slow_chat_json) as chat_json,
        ):
            results = []
            first = threading.Thread(
                target=lambda: results.append(quiz_service.extract_topics([], allow_full_scan=True))
            )
            second = threading.Thread(
                target=lambda: results.append(quiz_service.extract_topics([], allow_full_scan=True))
            )
            first.start()
            self.assertTrue(entered.wait(timeout=1))
            second.start()
            release.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertEqual([[], []], results)
        chat_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
