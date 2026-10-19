"""飞书文档完整度判定单元测试。"""

import unittest

from backend.app.services.feishu import _document_is_complete


class FeishuCompletenessTests(unittest.TestCase):
    def test_short_doc_delivered_in_initial_page_is_complete(self):
        self.assertTrue(_document_is_complete([], False, 0, False))

    def test_paginated_doc_with_final_page_is_complete(self):
        self.assertTrue(_document_is_complete([], True, 3, None))

    def test_missing_blocks_are_incomplete(self):
        self.assertFalse(_document_is_complete(["block-9"], True, 3, False))

    def test_paginated_doc_without_final_marker_is_incomplete(self):
        self.assertFalse(_document_is_complete([], False, 3, False))

    def test_initial_page_without_has_more_is_incomplete(self):
        self.assertFalse(_document_is_complete([], False, 0, None))


if __name__ == "__main__":
    unittest.main()
