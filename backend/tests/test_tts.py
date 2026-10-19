"""TTS 音色选择与参数归一化测试；不访问微软语音服务。"""

import unittest

from backend.app.services import tts


class TtsVoiceTests(unittest.TestCase):
    def test_default_voice_is_in_catalog(self):
        ids = {item["id"] for item in tts.list_voices()}
        self.assertIn(tts.DEFAULT_VOICE, ids)
        self.assertGreaterEqual(len(ids), 2)

    def test_known_voice_is_preserved(self):
        _, voice, _, _ = tts._normalise(
            "你好，我是面试官。",
            "zh-CN-XiaoxiaoNeural",
            0,
            0,
        )
        self.assertEqual("zh-CN-XiaoxiaoNeural", voice)

    def test_empty_voice_uses_default(self):
        _, voice, _, _ = tts._normalise("你好，我是面试官。", "", 0, 0)
        self.assertEqual(tts.DEFAULT_VOICE, voice)

    def test_unknown_voice_is_rejected_instead_of_silently_replaced(self):
        with self.assertRaises(tts.TTSError):
            tts._normalise("你好，我是面试官。", "not-a-voice", 0, 0)

    def test_empty_text_is_rejected(self):
        with self.assertRaises(tts.TTSError):
            tts._normalise("   ", tts.DEFAULT_VOICE, 0, 0)


if __name__ == "__main__":
    unittest.main()
