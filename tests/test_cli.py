"""CLI 端到端测试。

跑真实的 `python run.py <子命令>`，而不是只测内部函数。
理由：这个项目承诺「三行命令跑起来」，那句承诺的兑现点在 CLI 这一层——
参数名、默认值、输出路径、退出码，任何一处错了，用户第一步就失败。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run  # noqa: E402


def call(*argv: str) -> tuple[int, str]:
    """执行一次 CLI，返回 (退出码, 标准输出)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run.main(list(argv))
    return code, buf.getvalue()


class TestAsk(unittest.TestCase):
    def test_offline_ask_works(self):
        code, out = call("ask", "我花生过敏，不吃辣，人均25")
        self.assertEqual(code, 0)
        self.assertIn("我理解成", out)
        self.assertIn("过敏[花生]", out)
        # 判定分组必须齐全，否则用户看不到「信息不足」那一栏
        self.assertIn("未见疑点", out)
        self.assertIn("信息不足", out)

    def test_offline_ask_prints_data_caveat(self):
        """离线模式必须自己说清楚配料是人工填的，不能让人误以为是真的。"""
        _, out = call("ask", "花生过敏")
        self.assertIn("离线示例数据", out)
        self.assertIn("真实 MCP 不返回任何配料字段", out)

    def test_counter_questions_are_printed(self):
        _, out = call("ask", "我花生过敏，人均30")
        self.assertIn("去柜台时念这几句", out)
        self.assertIn("炸锅", out)

    def test_budget_override(self):
        _, out = call("ask", "花生过敏", "--budget", "40")
        self.assertIn("40.00 元", out)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.json"
            code, _ = call("ask", "我花生过敏，不吃辣，人均25", "--json", str(path))
            self.assertEqual(code, 0)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("peanut", data["profile"]["allergens"])
            self.assertIn("plans", data)
            self.assertTrue(data["assessments"])

    def test_html_output(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "card.html"
            code, _ = call("ask", "我花生过敏，人均25", "--html", str(path))
            self.assertEqual(code, 0)
            html = path.read_text(encoding="utf-8")
            self.assertGreater(len(html), 3000)
            self.assertIn("我的可吃清单", html)
            # 单文件承诺：不能有任何外部资源引用
            self.assertNotIn("<script src=", html)
            self.assertNotIn('href="http', html)


class TestCard(unittest.TestCase):
    def test_card_generated(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "card.html"
            code, out = call("card", "--out", str(path))
            self.assertEqual(code, 0)
            self.assertTrue(path.exists())
            self.assertIn("已生成", out)

    def test_card_contains_counter_section(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "card.html"
            call("card", "--out", str(path))
            self.assertIn("去柜台时念这几句", path.read_text(encoding="utf-8"))


class TestDemo(unittest.TestCase):
    def test_demo_prints_file_uri(self):
        code, out = call("demo", "--print-only")
        self.assertEqual(code, 0)
        self.assertIn("demo", out)
        self.assertIn("index.html", out)

    def test_demo_preset_appends_query(self):
        _, out = call("demo", "--preset", "peanut", "--print-only")
        self.assertIn("?p=peanut", out)

    def test_demo_page_exists(self):
        self.assertTrue((ROOT / "demo" / "index.html").exists())


class TestArgParsing(unittest.TestCase):
    def test_missing_subcommand_exits(self):
        with self.assertRaises(SystemExit):
            call()

    def test_bad_preset_exits(self):
        with self.assertRaises(SystemExit):
            call("demo", "--preset", "nope")

    def test_tools_without_token_prints_help(self):
        """没 Token 时不能崩，要给拿 Token 的步骤。"""
        import os
        old = os.environ.pop("MCD_MCP_TOKEN", None)
        try:
            code, out = call("tools")
        finally:
            if old is not None:
                os.environ["MCD_MCP_TOKEN"] = old
        self.assertNotEqual(code, 0)
        self.assertIn("MCD_MCP_TOKEN", out)

    def test_doctor_without_token_prints_help(self):
        import os
        old = os.environ.pop("MCD_MCP_TOKEN", None)
        try:
            code, out = call("doctor")
        finally:
            if old is not None:
                os.environ["MCD_MCP_TOKEN"] = old
        self.assertEqual(code, 2)
        self.assertIn("open.mcd.cn", out)


if __name__ == "__main__":
    unittest.main()
