"""MCP 体检逻辑的单元测试。用桩客户端模拟各种真实返回，不需要 Token。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd.doctor import _is_forbidden, diagnose, render_text  # noqa: E402


class StubClient:
    def __init__(self, tools, payload=None, raises: str | None = None):
        self._tools = tools
        self._payload = payload
        self._raises = raises
        self.calls: list[str] = []

    def initialize(self):
        return {"serverInfo": {"name": "stub-mcd", "version": "1.0"}, "protocolVersion": "2025-06-18"}

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments=None):
        self.calls.append(name)
        if self._raises:
            raise RuntimeError(self._raises)
        return self._payload


MENU_WITH_ALLERGENS = {
    "items": [
        {"name": "巨无霸", "price": 2450, "allergens": ["gluten", "milk"], "desc": "双层牛肉饼"},
        {"name": "麦香鱼", "price": 1850, "allergens": ["fish", "gluten"], "desc": "鳕鱼排"},
    ]
}
MENU_NO_ALLERGENS = {"items": [{"name": "巨无霸", "price": 2450, "desc": "双层牛肉饼"}]}

FULL_TOOLS = [
    {"name": "list-menu", "inputSchema": {"type": "object", "properties": {"storeCode": {"type": "string"}}}},
    {"name": "calculate-price", "inputSchema": {"type": "object", "required": ["items"],
                                                "properties": {"items": {}, "storeCode": {}}}},
    {"name": "nearby-stores", "inputSchema": {}},
    {"name": "query-my-coupons", "inputSchema": {}},
]


class TestVerdicts(unittest.TestCase):
    def test_green_when_allergen_fields_present(self):
        r = diagnose(StubClient(FULL_TOOLS, MENU_WITH_ALLERGENS))
        self.assertEqual(r["verdict"]["level"], "green")
        self.assertEqual(r["menu"]["items"], 2)
        self.assertIn("allergens", r["menu"]["allergen_keys"])

    def test_yellow_when_no_allergen_fields(self):
        r = diagnose(StubClient(FULL_TOOLS, MENU_NO_ALLERGENS))
        self.assertEqual(r["verdict"]["level"], "yellow")
        self.assertTrue(r["menu"]["ok"])
        self.assertEqual(r["menu"]["allergen_keys"], [])

    def test_red_when_no_menu_tool(self):
        r = diagnose(StubClient([{"name": "query-my-coupons"}], MENU_WITH_ALLERGENS))
        self.assertEqual(r["verdict"]["level"], "red")

    def test_red_when_call_fails(self):
        r = diagnose(StubClient(FULL_TOOLS, raises="HTTP 500"))
        self.assertEqual(r["verdict"]["level"], "red")
        self.assertIn("HTTP 500", r["menu"]["error"])

    def test_red_when_payload_unparsable(self):
        r = diagnose(StubClient(FULL_TOOLS, {"foo": "bar"}))
        self.assertEqual(r["verdict"]["level"], "red")


class TestCapabilityMatching(unittest.TestCase):
    def test_fuzzy_name_match(self):
        """工具改名时（如 list-menu-v2）应仍能匹配到菜单能力。"""
        r = diagnose(StubClient([{"name": "list-menu-v2"}], MENU_WITH_ALLERGENS))
        menu = next(c for c in r["capabilities"] if c["id"] == "menu")
        self.assertEqual(menu["matched"], "list-menu-v2")

    def test_price_schema_extracted(self):
        r = diagnose(StubClient(FULL_TOOLS, MENU_WITH_ALLERGENS))
        self.assertEqual(r["price"]["tool"], "calculate-price")
        self.assertEqual(r["price"]["required"], ["items"])

    def test_missing_optional_capability_is_not_fatal(self):
        tools = [{"name": "list-menu", "inputSchema": {}}]
        r = diagnose(StubClient(tools, MENU_WITH_ALLERGENS))
        self.assertNotEqual(r["verdict"]["level"], "red")

    def test_embedded_allergen_fields_fix_the_matrix(self):
        """回归测试：配料内嵌在菜单返回里时，矩阵不能仍标「未找到」。

        曾经的问题：结论是绿灯，能力矩阵却把「配料/过敏原」标成 ❌ 未找到，
        同一份报告自相矛盾。
        """
        tools = [{"name": "list-menu", "inputSchema": {}}]
        r = diagnose(StubClient(tools, MENU_WITH_ALLERGENS))
        nutrition = next(c for c in r["capabilities"] if c["id"] == "nutrition")
        self.assertTrue(nutrition["matched"])
        self.assertIn("内嵌", nutrition["matched"])
        self.assertEqual(r["verdict"]["level"], "green")


class TestSafety(unittest.TestCase):
    def test_forbidden_detection(self):
        for name in ("create-order", "cancel-order", "draw-lottery", "claim-coupon", "refund"):
            self.assertTrue(_is_forbidden(name), name)
        for name in ("list-menu", "calculate-price", "query-my-coupons"):
            self.assertFalse(_is_forbidden(name), name)

    def test_never_calls_writing_tools(self):
        """即使返回体里只有下单工具，也绝不能调用它。"""
        stub = StubClient([{"name": "create-order"}], MENU_WITH_ALLERGENS)
        diagnose(stub)
        self.assertEqual(stub.calls, [])

    def test_only_read_only_tool_called(self):
        stub = StubClient(FULL_TOOLS, MENU_WITH_ALLERGENS)
        diagnose(stub)
        for call in stub.calls:
            self.assertFalse(_is_forbidden(call), f"调用了有副作用的工具：{call}")


# 2026-10-22 用真实 Token 拉到的全部 35 个工具名。这是审计的基准事实。
REAL_TOOLS_2026_10_22 = (
    "cancel-order", "now-time-info", "delivery-create-address", "delivery-query-addresses",
    "query-promotions", "query-my-coupons", "list-nutrition-foods", "draw-lottery",
    "mall-product-detail", "mall-order-list", "query-party-store", "delivery-query-stores",
    "mall-order-detail", "party-order-create", "query-meal-detail", "query-my-account",
    "mall-points-products", "query-survey-coupon", "query-lottery-info", "mall-create-order",
    "query-meals", "campaign-calendar", "query-order", "order-list",
    "query-party-store-session", "query-nearby-stores", "query-meal-assistance",
    "query-party-city", "auto-bind-coupons", "query-store-coupons", "create-order",
    "calculate-price", "query-my-prizes", "query-party-store-date", "available-coupons",
)

WRITE_TOOLS = {
    "create-order",            # 下单
    "cancel-order",            # 取消订单
    "mall-create-order",       # 积分兑换下单
    "party-order-create",      # 派对预约下单
    "delivery-create-address",  # 新增配送地址
    "auto-bind-coupons",       # 自动领券（真的会改账号状态）
    "draw-lottery",            # 抽奖扣积分
}


class TestRealToolAudit(unittest.TestCase):
    """把「哪些工具能碰、哪些绝不能碰」钉成测试，而不是写在文档里靠自觉。

    这份名单是真实接口拉下来的，不是猜的。以后 MCP 上新工具时，
    如果新工具名字里带写动词，它会自动被拦；如果不带，这个测试是唯一提醒点。
    """

    def test_all_write_tools_are_blocked(self):
        for name in sorted(WRITE_TOOLS):
            self.assertTrue(_is_forbidden(name), f"写接口未被拦截：{name}")

    def test_read_tools_are_not_blocked(self):
        """误杀的代价是少探一条，但仍应尽量准确——只读工具不该被挡。"""
        for name in REAL_TOOLS_2026_10_22:
            if name in WRITE_TOOLS:
                continue
            self.assertFalse(_is_forbidden(name), f"只读工具被误拦：{name}")

    def test_read_only_order_queries_are_allowed(self):
        """query-order / order-list 名字里有 order，但它们是只读的。"""
        for name in ("query-order", "order-list", "mall-order-list", "mall-order-detail"):
            self.assertFalse(_is_forbidden(name), name)

    def test_capability_matrix_matches_real_names(self):
        """真实工具名下，8 项能力必须全部命中——否则就是候选表又过期了。

        这正是踩过的坑：菜单工具真名是 query-meals，候选表里只有 list-menu，
        导致工具明明存在、体检却报红灯。诊断工具误诊是最难堪的 bug。
        """
        tools = [{"name": n, "inputSchema": {}} for n in REAL_TOOLS_2026_10_22]
        stub = StubClient(tools, MENU_WITH_ALLERGENS)
        r = diagnose(stub)
        missing = [c["id"] for c in r["capabilities"] if not c["matched"]]
        self.assertEqual(missing, [], f"这些能力没匹配上：{missing}")

    def test_verdict_is_red_without_meal_tool(self):
        """只有写接口时，必须红灯且一次调用都不发。"""
        tools = [{"name": n, "inputSchema": {}} for n in sorted(WRITE_TOOLS)]
        stub = StubClient(tools, MENU_WITH_ALLERGENS)
        r = diagnose(stub)
        self.assertEqual(r["verdict"]["level"], "red")
        self.assertEqual(stub.calls, [])


class TestBootstrapping(unittest.TestCase):
    """菜单工具需要 storeCode 时，体检必须自己先查一家门店。"""

    def test_store_bootstrap_feeds_menu_args(self):
        seen: list[tuple[str, dict]] = []

        class ChainClient(StubClient):
            def call_tool(self, name, arguments=None):
                seen.append((name, dict(arguments or {})))
                if name == "query-nearby-stores":
                    return {"structuredContent": {"data": [
                        {"storeCode": "1950564", "storeName": "建外SOHO"},
                    ]}}
                if name == "query-meals":
                    return {"structuredContent": {"data": {"meals": {
                        "m1": {"name": "巨无霸", "currentPrice": "24.5"},
                        "m2": {"name": "麦香鱼", "currentPrice": "18.5"},
                    }}}}
                return {"structuredContent": {"data": ""}}

        tools = [
            {"name": "query-nearby-stores", "inputSchema": {}},
            {"name": "query-meals", "inputSchema": {
                "type": "object", "required": ["storeCode", "orderType", "beType"],
                "properties": {"storeCode": {}, "orderType": {}, "beType": {}}}},
        ]
        r = diagnose(ChainClient(tools))
        menu_args = dict(seen)["query-meals"]
        self.assertEqual(menu_args["storeCode"], "1950564")
        self.assertEqual(r["menu"]["items"], 2)
        self.assertTrue(r["menu"]["ok"])

    def test_reports_missing_required_params_instead_of_blind_call(self):
        """拿不到 storeCode 时，宁可不调用，也不能把空参数打过去。"""
        called: list[str] = []

        class NoStoreClient(StubClient):
            def call_tool(self, name, arguments=None):
                called.append(name)
                return {"structuredContent": {"data": []}}

        tools = [
            {"name": "query-nearby-stores", "inputSchema": {}},
            {"name": "query-meals", "inputSchema": {
                "type": "object", "required": ["storeCode"],
                "properties": {"storeCode": {}}}},
        ]
        r = diagnose(NoStoreClient(tools))
        self.assertEqual(r["verdict"]["level"], "red")
        self.assertNotIn("query-meals", called)  # 宁可不调，也不发空参数
        self.assertNotIn("storeCode", str(r["menu"]["args"]))
        self.assertIn("缺少必填参数", r["menu"]["error"])


class TestNutritionProbe(unittest.TestCase):
    def test_reads_official_text_table(self):
        """营养表是纯文本，不是 JSON。这条链路必须单独验证。"""
        table = ("[2]{productName,nutritionDescription,energyKj,energyKcal,"
                 "protein,fat,carbohydrate,sodium,calcium}:\n"
                 "  猪柳麦满分,null,1288,308,16,16,24,781,213\n"
                 "  苹果片,null,180,43,0.3,0.1,10,1,3")

        class NutClient(StubClient):
            def call_tool(self, name, arguments=None):
                if name == "list-nutrition-foods":
                    return {"structuredContent": {"data": table}}
                return {"structuredContent": {"data": {"meals": {
                    "m1": {"name": "巨无霸", "currentPrice": "24.5"}}}}}

        tools = [
            {"name": "query-meals", "inputSchema": {}},
            {"name": "list-nutrition-foods", "inputSchema": {}},
        ]
        r = diagnose(NutClient(tools))
        self.assertEqual(r["nutrition"]["rows"], 2)
        self.assertIn("sodium", r["nutrition"]["fields"])
        self.assertEqual(r["nutrition"]["allergen_keys"], [])
        # 有营养数据、无配料表 → 黄灯，且结论必须说清「能做营养筛选、不能做配料判定」
        self.assertEqual(r["verdict"]["level"], "yellow")
        self.assertIn("营养", r["verdict"]["headline"])


class TestRender(unittest.TestCase):
    def test_text_report_contains_key_sections(self):
        r = diagnose(StubClient(FULL_TOOLS, MENU_WITH_ALLERGENS))
        text = render_text(r, "abcd****wxyz")
        for kw in ("能力矩阵", "菜单探测", "算价探测", "结论"):
            self.assertIn(kw, text)
        self.assertNotIn("你的Token", text)


if __name__ == "__main__":
    unittest.main()
