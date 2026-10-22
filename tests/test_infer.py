"""从餐品名称推断忌口属性的测试。

这些用例的价值不在「代码有没有 bug」，而在于**把产品承诺的边界钉死**：
能推断到的只能到「可能含有」，推断不到的必须留空。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd.infer import infer_from_name  # noqa: E402


class TestSpicy(unittest.TestCase):
    def test_mala_is_spicy(self):
        self.assertGreater(infer_from_name("麦辣鸡腿汉堡").spicy, 0)

    def test_plain_burger_is_not_spicy(self):
        self.assertEqual(infer_from_name("吉士汉堡").spicy, 0)

    def test_sichuan_pepper_is_spicy(self):
        self.assertGreater(infer_from_name("藤椒鸡腿堡").spicy, 0)


class TestFried(unittest.TestCase):
    def test_fries(self):
        self.assertTrue(infer_from_name("中薯条").fried)

    def test_nuggets(self):
        self.assertTrue(infer_from_name("麦乐鸡").fried)

    def test_grilled_is_not_fried(self):
        self.assertFalse(infer_from_name("板烧鸡腿堡").fried)

    def test_drink_is_not_fried(self):
        self.assertFalse(infer_from_name("可口可乐").fried)


class TestMeat(unittest.TestCase):
    """顺序敏感：更具体的词必须先匹配。

    这是真踩过的坑：「牛奶」含「牛」会被判成牛肉，「鸡蛋」含「鸡」会被判成鸡肉。
    """

    def test_beef(self):
        self.assertEqual(infer_from_name("巨无霸").meat, "beef")

    def test_chicken(self):
        self.assertEqual(infer_from_name("麦辣鸡腿汉堡").meat, "chicken")

    def test_fish(self):
        self.assertEqual(infer_from_name("麦香鱼").meat, "fish")

    def test_pork(self):
        self.assertEqual(infer_from_name("猪柳麦满分").meat, "pork")

    def test_milk_is_not_beef(self):
        """回归：牛奶不是牛肉。"""
        self.assertNotEqual(infer_from_name("纯牛奶").meat, "beef")

    def test_egg_is_not_chicken(self):
        """回归：鸡蛋不是鸡肉。"""
        self.assertNotEqual(infer_from_name("鸡蛋").meat, "chicken")

    def test_shrimp_is_fish_not_chicken(self):
        self.assertEqual(infer_from_name("三文鱼双虾至尊月堡").meat, "fish")

    def test_unknown_has_no_meat(self):
        self.assertIsNone(infer_from_name("苹果片").meat)


class TestAllergenHints(unittest.TestCase):
    def test_milk_hint(self):
        self.assertIn("milk", infer_from_name("芝士汉堡").hints)

    def test_peanut_hint(self):
        self.assertIn("peanut", infer_from_name("花生新地").hints)

    def test_gluten_hint_from_bun(self):
        self.assertIn("gluten", infer_from_name("吉士汉堡").hints)

    def test_no_hint_for_plain_item(self):
        """推断不出就返回空，不能凭空给一个「可能含」。"""
        self.assertEqual(infer_from_name("冰水").hints, {})

    def test_hint_value_is_the_trigger_word(self):
        """hints 的值是依据词，界面上要能解释「为什么说可能含」。"""
        inf = infer_from_name("芝士汉堡")
        self.assertIn(inf.hints["milk"], "芝士汉堡")


class TestCaffeine(unittest.TestCase):
    def test_coffee(self):
        self.assertTrue(infer_from_name("鲜煮咖啡").caffeine)

    def test_latte(self):
        self.assertTrue(infer_from_name("燕麦奶拿铁").caffeine)

    def test_cola_is_not_flagged(self):
        """产品承诺只说名里有咖啡因线索的，可乐不在此列，避免过度声称。"""
        self.assertFalse(infer_from_name("可口可乐").caffeine)


class TestEdgeCases(unittest.TestCase):
    def test_empty_name(self):
        inf = infer_from_name("")
        self.assertEqual(inf.meat, None)
        self.assertEqual(inf.spicy, 0)
        self.assertEqual(inf.hints, {})

    def test_none_name(self):
        inf = infer_from_name(None)  # type: ignore[arg-type]
        self.assertEqual(inf.hints, {})

    def test_brackets_and_spaces_ignored(self):
        self.assertEqual(infer_from_name("【新品】 麦 香 鱼").meat, "fish")

    def test_describe_falls_back(self):
        self.assertEqual(infer_from_name("苹果片").describe(), "名称中无可用信息")


if __name__ == "__main__":
    unittest.main()
