"""命题资料聚焦：选定文档时按关键词取相关片段，不足时回退全库检索。"""

import unittest
from unittest.mock import patch

from backend.app.services import quiz as quiz_service


class QuizMaterialFocusTests(unittest.TestCase):
    def _docs(self):
        return [
            {"doc_id": "doc-a", "title": "RAG 开发笔记"},
            {"doc_id": "doc-b", "title": "接口测试讲义"},
        ]

    def test_selected_docs_only_take_keyword_matching_chunks(self):
        with (
            patch.object(
                quiz_service.knowledge_service,
                "list_documents",
                return_value=self._docs(),
            ),
            patch.object(
                quiz_service.knowledge_service,
                "load_document",
                side_effect={
                    "doc-a": {
                        "chunks": [
                            "RAG 检索主要依靠 Embedding 召回。",
                            "这里讲接口测试的其他方向，与嵌入无关。",
                            "重排再用 rerank 提升召回质量。",
                        ],
                    },
                    "doc-b": {
                        "chunks": [
                            "接口测试用 Postman 做冒烟。",
                        ],
                    },
                }.get,
            ),
            patch.object(
                quiz_service.knowledge_service,
                "search",
                side_effect=AssertionError("聚焦命中时不应回退到 search"),
            ),
        ):
            material, titles, docs, chunks = quiz_service._collect_material(
                ["RAG", "rerank"], ["doc-a", "doc-b"]
            )

        self.assertIn("RAG 检索主要依靠 Embedding 召回。", material)
        self.assertIn("重排再用 rerank 提升召回质量。", material)
        self.assertNotIn("接口测试的其他方向", material)
        self.assertNotIn("Postman 做冒烟", material)
        self.assertEqual(["RAG 开发笔记"], titles)
        self.assertEqual({"doc-a"}, {item["doc_id"] for item in docs})
        self.assertEqual({"doc-a-0", "doc-a-2"}, {item["chunk_id"] for item in chunks})

    def test_falls_back_to_search_when_focus_has_no_hits(self):
        with (
            patch.object(
                quiz_service.knowledge_service,
                "list_documents",
                return_value=self._docs(),
            ),
            patch.object(
                quiz_service.knowledge_service,
                "load_document",
                return_value={"chunks": ["整份文档都不包含选中关键词。"]},
            ),
            patch.object(
                quiz_service.knowledge_service,
                "search",
                return_value=[
                    {
                        "chunk_id": "doc-b-1",
                        "doc_id": "doc-b",
                        "title": "接口测试讲义",
                        "text": "超时重试属于接口测试的常见考点。",
                    }
                ],
            ) as search,
        ):
            material, titles, docs, chunks = quiz_service._collect_material(
                ["超时重试"], ["doc-a", "doc-b"]
            )

        self.assertIn("超时重试属于接口测试的常见考点。", material)
        self.assertEqual(["接口测试讲义"], titles)
        self.assertEqual("doc-b", docs[0]["doc_id"])
        self.assertEqual("doc-b-1", chunks[0]["chunk_id"])
        search.assert_called_once_with("超时重试", 4)

    def test_no_search_fallback_when_any_selected_doc_has_hits(self):
        with (
            patch.object(
                quiz_service.knowledge_service,
                "list_documents",
                return_value=self._docs(),
            ),
            patch.object(
                quiz_service.knowledge_service,
                "load_document",
                side_effect={
                    "doc-a": {
                        "chunks": [
                            "RAG 检索主要依靠 Embedding 召回。",
                            "不含关键词的无关内容。",
                        ],
                    },
                    "doc-b": {
                        "chunks": [
                            "接口测试用 Postman 做冒烟。",
                            "整份文档都没有命中关键词的相关内容。",
                        ],
                    },
                }.get,
            ),
            patch.object(
                quiz_service.knowledge_service,
                "search",
                side_effect=AssertionError("已有聚焦命中时不应回退到 search"),
            ),
        ):
            material, titles, docs, chunks = quiz_service._collect_material(
                ["RAG"], ["doc-a", "doc-b"]
            )

        self.assertIn("RAG 检索主要依靠 Embedding 召回。", material)
        self.assertNotIn("Postman 做冒烟", material)
        self.assertEqual(["RAG 开发笔记"], titles)
        self.assertEqual({"doc-a"}, {item["doc_id"] for item in docs})
        self.assertEqual(["doc-a-0"], [item["chunk_id"] for item in chunks])


if __name__ == "__main__":
    unittest.main()
