"""End-to-end UTF-8 fidelity: non-ASCII (CJK / emoji / accents) in a transcript
must survive the read path into reconstructed state — prompts, commands, changed
file paths, and agent text alike. Guards against any locale-dependent decoding.
"""

import unittest

from tests.util import state, user, asst, tool, result


class TestUTF8RoundTrip(unittest.TestCase):
    def setUp(self):
        self.st = state([
            user("请帮我修复 café.py 里的 bug 🐛", 120),
            tool("Bash", {"command": "echo 你好世界 > 输出.txt",
                          "description": "写入中文文件"}, "b1", 60),
            result("b1", content="完成 ✅", ago=55),
            tool("Write", {"file_path": "/proj/笔记/计划-Ω.md",
                           "content": "# 计划\n第一步"}, "w1", 40),
            result("w1", ago=35),
            asst("已完成所有修改 ✅ — café 正常运行", 5),
        ])

    def test_intent_preserved(self):
        joined = " ".join(r.text for r in self.st.intents)
        self.assertIn("café.py", joined)
        self.assertIn("修复", joined)
        self.assertIn("🐛", joined)

    def test_command_preserved(self):
        cmds = " ".join(c.cmd + " " + c.desc for c in self.st.commands)
        self.assertIn("你好世界", cmds)
        self.assertIn("输出.txt", cmds)
        self.assertIn("写入中文文件", cmds)

    def test_changed_path_preserved(self):
        self.assertIn("/proj/笔记/计划-Ω.md", self.st.files)

    def test_agent_text_preserved(self):
        texts = " ".join(r.text for r in self.st.last_agent_texts)
        self.assertIn("已完成所有修改", texts)
        self.assertIn("café", texts)
        self.assertIn("✅", texts)


if __name__ == "__main__":
    unittest.main()
