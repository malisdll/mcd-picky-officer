"""真实数据装配层的测试。

这一层是「官方脏数据」和「内部干净模型」之间的唯一闸门，所以重点全在边界：
- 价格是「元」字符串（'33.5'），必须转成分，不能留 float
- 菜单里混着周边、券、权益，必须剔掉
- 营养表只按名称精确命中，命中不了必须留 None，绝不能填 0
- 官方没有配料，allergens 必须是 None（表示「没有数据」），不能是空元组（表示「确认没有」）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd.live import LiveData, _category, _price_to_cents, fetch, find_store  # noqa: E402

NUT_TABLE = ("[2]{productName,nutritionDescription,energyKj,energyKcal,"
             "protein,fat,carbohydrate,sodium,calcium}:\n"
             "  巨无霸,null,2200,525,26,28,42,970,150\n"
             "  中薯条,null,1300,310,4,15,41,250,20")


class FakeClient:
    """模拟官方三个接口的真实返回形状。"""

    def __init__(self, meals=None, stores=None, nutrition=NUT_TABLE, fail=None):
        self.meals = meals if meals is not None else {
            "M001": {"name": "巨无霸", "currentPrice": "24.5", "image": "x.png", "tags": []},
            "M002": {"name": "中薯条", "currentPrice": "12", "image": "y.png", "tags": []},
        }
        self.stores = stores if stores is not None else [
            {"storeCode": "1950564", "storeName": "麦当劳北京建外SOHO(A座)餐厅"},
        ]
        self.nutrition = nutrition
        self.fail = fail or set()
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        if name in self.fail:
            raise RuntimeError(f"{name} boom")
        if name == "query-nearby-stores":
            return {"structuredContent": {"data": self.stores}}
        if name == "query-meals":
            return {"structuredContent": {"data": {"meals": self.meals}}}
        if name == "list-nutrition-foods":
            return {"structuredContent": {"data": self.nutrition}}
        return {"structuredContent": {"data": None}}


class TestPrice(unittest.TestCase):
    def test_yuan_string_to_cents(self):
        self.assertEqual(_price_to_cents("33.5"), 3350)

    def test_integer_string(self):
        self.assertEqual(_price_to_cents("12"), 1200)

    def test_float_is_yuan_not_cents(self):
        """官方给的是「元」。12.0 是 12 元 = 1200 分，不是 12 分。"""
        self.assertEqual(_price_to_cents(12.0), 1200)

    def test_currency_symbols_stripped(self):
        self.assertEqual(_price_to_cents("¥24.90"), 2490)
        self.assertEqual(_price_to_cents("24元"), 2400)

    def test_garbage_returns_none(self):
        self.assertIsNone(_price_to_cents("面议"))

    def test_none_returns_none(self):
        self.assertIsNone(_price_to_cents(None))

    def test_no_float_rounding_drift(self):
        """0.1+0.2 那套坑。分是整数，必须精确。"""
        self.assertEqual(_price_to_cents("0.07") + _price_to_cents("0.03"), 10)


class TestCategory(unittest.TestCase):
    def test_drink(self):
        self.assertEqual(_category("可口可乐（中）"), "drink")

    def test_dessert(self):
        self.assertEqual(_category("朱古力新地"), "dessert")

    def test_side(self):
        self.assertEqual(_category("中薯条"), "side")

    def test_main(self):
        self.assertEqual(_category("巨无霸"), "main")

    def test_drink_beats_side(self):
        """「饮品」里带「鸡」之类的词也不能被判成小食。"""
        self.assertEqual(_category("柠檬味饮品"), "drink")


class TestFindStore(unittest.TestCase):
    def test_city_and_keyword_both_required(self):
        with self.assertRaises(Exception):
            find_store(FakeClient(), "", "国贸")
        with self.assertRaises(Exception):
            find_store(FakeClient(), "北京市", "")

    def test_returns_code_and_name(self):
        code, name = find_store(FakeClient(), "北京市", "国贸")
        self.assertEqual(code, "1950564")
        self.assertIn("建外", name)

    def test_empty_result_raises(self):
        with self.assertRaises(Exception):
            find_store(FakeClient(stores=[]), "北京市", "不存在的地方")

    def test_passes_city_and_keyword_through(self):
        c = FakeClient()
        find_store(c, "上海市", "南京路")
        _, args = c.calls[0]
        self.assertEqual(args["city"], "上海市")
        self.assertEqual(args["keyword"], "南京路")


class TestFetch(unittest.TestCase):
    def test_basic_shape(self):
        d = fetch(FakeClient(), "北京市", "国贸")
        self.assertIsInstance(d, LiveData)
        self.assertEqual(d.store_code, "1950564")
        self.assertEqual(len(d.items), 2)

    def test_prices_are_cents(self):
        d = fetch(FakeClient(), "北京市", "国贸")
        by_name = {i.name: i for i in d.items}
        self.assertEqual(by_name["巨无霸"].price, 2450)
        self.assertEqual(by_name["中薯条"].price, 1200)

    def test_allergens_is_none_not_empty(self):
        """None = 官方没给；() = 官方确认没有。这两者绝不能混。

        如果把 None 写成 ()，产品就会把 82% 查不到的餐品显示成「安全」。
        """
        d = fetch(FakeClient(), "北京市", "国贸")
        for item in d.items:
            self.assertIsNone(item.allergens, item.name)

    def test_nutrition_attached_when_name_matches(self):
        d = fetch(FakeClient(), "北京市", "国贸")
        big_mac = next(i for i in d.items if i.name == "巨无霸")
        self.assertEqual(big_mac.sodium, 970)
        self.assertEqual(big_mac.kcal, 525)
        self.assertEqual(d.matched_nutrition, 2)

    def test_nutrition_left_none_when_no_match(self):
        d = fetch(FakeClient(), "北京市", "国贸")
        item = d.items[0]
        self.assertIsNotNone(item.sodium)  # 两个都命中了
        # 换一个不存在的名字验证留空
        d2 = fetch(FakeClient(meals={"X": {"name": "神秘新品堡", "currentPrice": "30"}}),
                   "北京市", "国贸")
        self.assertIsNone(d2.items[0].sodium)
        self.assertIsNone(d2.items[0].protein)
        self.assertEqual(d2.matched_nutrition, 0)

    def test_nutrition_failure_does_not_break_menu(self):
        """营养表挂了，菜单还得能出。少了营养不能连饭都点不了。"""
        d = fetch(FakeClient(fail={"list-nutrition-foods"}), "北京市", "国贸")
        self.assertEqual(len(d.items), 2)
        self.assertIsNone(d.items[0].sodium)
        self.assertEqual(d.nutrition_rows, 0)

    def test_non_food_filtered(self):
        meals = {
            "A": {"name": "巨无霸", "currentPrice": "24.5"},
            "B": {"name": "开心乐园玩具公仔", "currentPrice": "19"},
        }
        d = fetch(FakeClient(meals=meals), "北京市", "国贸")
        self.assertEqual([i.name for i in d.items], ["巨无霸"])
        self.assertTrue(any("公仔" in x for x in d.dropped))

    def test_real_world_merchandise_filtered(self):
        """这三个是真菜单里混进来的周边，官方没有任何字段能区分它们。

        「薯条脆卜卜毛绒周边」「麦当劳限定丝巾」「按摩捶痒痒挠」跟汉堡一样
        都只有 name/currentPrice/image/canWithOrder，只能按名字挡。
        """
        meals = {
            "A": {"name": "巨无霸", "currentPrice": "24.5"},
            "B": {"name": "薯条脆卜卜毛绒周边", "currentPrice": "24.9"},
            "C": {"name": "麦当劳限定丝巾", "currentPrice": "34.9"},
            "D": {"name": "“葱不求人“按摩捶痒痒挠", "currentPrice": "14.9"},
        }
        d = fetch(FakeClient(meals=meals), "北京市", "国贸")
        self.assertEqual([i.name for i in d.items], ["巨无霸"])
        self.assertEqual(d.drop_reasons["周边/非食品"], 3)

    def test_configurable_combo_filtered(self):
        """「随心配」的标价只是起点，内容还没定，无法判定忌口与钠含量。

        把「人气经典随心配 14.9」写进「你能吃这些」的清单里就是误导。
        """
        meals = {
            "A": {"name": "巨无霸", "currentPrice": "24.5"},
            "B": {"name": "人气经典随心配", "currentPrice": "14.9"},
            "C": {"name": "精选超值随心配", "currentPrice": "13.9"},
            "D": {"name": "鱼牛汉堡四件套随心选", "currentPrice": "32"},
            "E": {"name": "堡堡随心拼（7件任选）", "currentPrice": "72.5"},
        }
        d = fetch(FakeClient(meals=meals), "北京市", "国贸")
        self.assertEqual([i.name for i in d.items], ["巨无霸"])
        self.assertEqual(d.drop_reasons["可配置套餐"], 4)

    def test_fixed_combo_is_kept(self):
        """「三件套」「四件套」是固定组合，价格就是实价，必须留下。"""
        meals = {
            "A": {"name": "麦辣鸡腿汉堡三件套", "currentPrice": "33.5"},
            "B": {"name": "龙焰鸡腿堡四件套", "currentPrice": "29"},
        }
        d = fetch(FakeClient(meals=meals), "北京市", "国贸")
        self.assertEqual(len(d.items), 2)

    def test_drop_summary_breaks_down_reasons(self):
        meals = {
            "A": {"name": "巨无霸", "currentPrice": "24.5"},
            "B": {"name": "限定丝巾", "currentPrice": "34.9"},
            "C": {"name": "人气经典随心配", "currentPrice": "14.9"},
            "D": {"name": "赠品", "currentPrice": None},
        }
        d = fetch(FakeClient(meals=meals), "北京市", "国贸")
        self.assertIn("周边/非食品 1 个", d.drop_summary)
        self.assertIn("可配置套餐 1 个", d.drop_summary)
        self.assertIn("无价格 1 个", d.drop_summary)

    def test_drop_summary_is_readable_when_nothing_dropped(self):
        d = fetch(FakeClient(), "北京市", "国贸")
        self.assertEqual(d.drop_summary, "无")

    def test_coupon_entries_filtered(self):
        """「随心选」是权益不是餐品，混进配餐会算出假的方案总价。"""
        meals = {
            "A": {"name": "巨无霸", "currentPrice": "24.5"},
            "B": {"name": "饮品随心选", "currentPrice": "0"},
        }
        d = fetch(FakeClient(meals=meals), "北京市", "国贸")
        self.assertEqual([i.name for i in d.items], ["巨无霸"])

    def test_missing_price_filtered(self):
        meals = {
            "A": {"name": "巨无霸", "currentPrice": "24.5"},
            "B": {"name": "赠品券", "currentPrice": None},
        }
        d = fetch(FakeClient(meals=meals), "北京市", "国贸")
        self.assertEqual(len(d.items), 1)

    def test_missing_name_skipped(self):
        d = fetch(FakeClient(meals={"A": {"currentPrice": "10"}}), "北京市", "国贸")
        self.assertEqual(d.items, [])

    def test_coverage_ratio(self):
        meals = {
            "A": {"name": "巨无霸", "currentPrice": "24.5"},
            "B": {"name": "神秘新品堡", "currentPrice": "30"},
        }
        d = fetch(FakeClient(meals=meals), "北京市", "国贸")
        self.assertEqual(d.nutrition_coverage, 0.5)

    def test_coverage_zero_when_no_items(self):
        self.assertEqual(LiveData("x", "y", []).nutrition_coverage, 0.0)

    def test_inference_is_attached(self):
        d = fetch(FakeClient(meals={"A": {"name": "麦辣鸡腿汉堡", "currentPrice": "22.5"}}),
                  "北京市", "国贸")
        item = d.items[0]
        self.assertGreater(item.spicy, 0)
        self.assertEqual(item.meat, "chicken")
        self.assertEqual(item.data_source, "live")

    def test_meal_delivery_structure_mismatch_raises(self):
        """meals 从字典变成列表时必须报错，不能静默返回 0 个餐品。

        静默的后果更糟：界面会显示「没有可选餐品」，看起来像产品没有货，
        实际是接口改版。宁可报错，也不要让人查错方向。
        """

        class Bad(FakeClient):
            def call_tool(self, name, arguments=None):
                if name == "query-meals":
                    return {"structuredContent": {"data": {"meals": [{"name": "巨无霸"}]}}}
                return super().call_tool(name, arguments)

        with self.assertRaises(Exception):
            fetch(Bad(), "北京市", "国贸")

    def test_only_read_tools_are_called(self):
        c = FakeClient()
        fetch(c, "北京市", "国贸")
        for name, _ in c.calls:
            for verb in ("create", "cancel", "pay", "bind", "draw"):
                self.assertNotIn(verb, name.lower(), f"fetch 调用了写接口：{name}")


if __name__ == "__main__":
    unittest.main()
