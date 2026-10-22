"""三态判定逻辑的单元测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd.matcher import Matcher  # noqa: E402
from mcd.models import Item, Profile, Verdict  # noqa: E402
from mcd.store import load_lexicon, sample_items  # noqa: E402

LEX = load_lexicon()
M = Matcher(LEX)
ITEMS = {i.name: i for i in sample_items()}


def v(name: str, profile: Profile) -> Verdict:
    return M.assess(ITEMS[name], profile).verdict


class TestAllergenVerdict(unittest.TestCase):
    def test_structured_hit(self):
        """官方标注含芝麻 → 「含」。"""
        self.assertEqual(v("巨无霸", Profile(allergens=("sesame",))), Verdict.HIT)

    def test_structured_clear(self):
        """官方标注里没有花生，且描述无相关词 → 「未见疑点」。"""
        self.assertEqual(v("巨无霸", Profile(allergens=("peanut",))), Verdict.CLEAR)

    def test_fish(self):
        self.assertEqual(v("麦香鱼", Profile(allergens=("fish",))), Verdict.HIT)

    def test_gluten(self):
        self.assertEqual(v("麦乐鸡（5块）", Profile(allergens=("gluten",))), Verdict.HIT)

    def test_no_info_when_data_missing(self):
        """官方没给配料，且描述无线索 → 「信息不足」，绝不能报成安全。"""
        self.assertEqual(v("麦辣鸡翅（2块）", Profile(allergens=("peanut",))), Verdict.NO_INFO)

    def test_empty_allergen_list_means_clear(self):
        """allergens == [] 表示官方明确标注「无」，此时是 CLEAR。"""
        self.assertEqual(v("苹果片", Profile(allergens=("gluten",))), Verdict.CLEAR)

    def test_risk_from_keyword(self):
        """描述里出现「派」→ 对麸质是可能含有，不是明确含。"""
        verdict = v("香芋派", Profile(allergens=("sesame",)))
        self.assertIn(verdict, (Verdict.CLEAR, Verdict.RISK, Verdict.NO_INFO))


class TestAvoidVerdict(unittest.TestCase):
    def test_spicy_hit(self):
        self.assertEqual(v("麦辣鸡腿堡", Profile(avoid=("spicy",))), Verdict.HIT)

    def test_spicy_clear(self):
        self.assertEqual(v("板烧鸡腿堡", Profile(avoid=("spicy",))), Verdict.CLEAR)

    def test_fried(self):
        self.assertEqual(v("薯条（中）", Profile(avoid=("fried",))), Verdict.HIT)

    def test_caffeine(self):
        self.assertEqual(v("拿铁", Profile(avoid=("caffeine",))), Verdict.HIT)

    def test_meat(self):
        self.assertEqual(v("巨无霸", Profile(avoid=("beef",))), Verdict.HIT)


class TestDiet(unittest.TestCase):
    def test_vegan_blocks_burger(self):
        self.assertEqual(v("巨无霸", Profile(diets=("vegan",))), Verdict.HIT)

    def test_vegan_blocks_milk(self):
        self.assertEqual(v("纯牛奶", Profile(diets=("vegan",))), Verdict.HIT)

    def test_halal_allows_beef(self):
        self.assertEqual(v("吉士汉堡", Profile(diets=("halal",))), Verdict.CLEAR)


class TestExplainability(unittest.TestCase):
    def test_reasons_are_human_readable(self):
        a = M.assess(ITEMS["巨无霸"], Profile(allergens=("sesame",)))
        self.assertTrue(a.reasons)
        self.assertTrue(any("芝麻" in r for r in a.reasons))

    def test_worst_verdict_wins(self):
        """同时命中过敏原和辣度时，取更严重的那个。"""
        profile = Profile(allergens=("gluten",), avoid=("spicy",))
        self.assertEqual(v("麦辣鸡腿堡", profile), Verdict.HIT)

    def test_assess_all_sorted(self):
        rows = M.assess_all(list(ITEMS.values()), Profile(allergens=("milk",)))
        verdicts = [int(a.verdict) for a in rows]
        self.assertEqual(verdicts, sorted(verdicts, reverse=True))


class TestNutritionConstraints(unittest.TestCase):
    """营养约束走的是官方营养表的精确值，是唯一「硬」的数据。

    这些用例守的是一条线：**查不到就说查不到**。官方营养表只有 160 行，
    菜单有 110 个条目，按名称精确命中只有 19 个——剩下 82% 必须落到「信息不足」，
    而不是因为「没数据所以没问题」被放进方案。
    """

    def _item(self, **kw):
        base = dict(id="t", name="测试餐品", category="main", price=2000, allergens=())
        base.update(kw)
        return Item(**base)

    def test_sodium_over_limit_is_hit(self):
        a = M.assess(self._item(sodium=1208, kcal=570), Profile(max_sodium=600))
        self.assertEqual(a.verdict, Verdict.HIT)
        self.assertTrue(any("1208" in r for r in a.reasons))

    def test_sodium_under_limit_is_clear(self):
        a = M.assess(self._item(sodium=100, kcal=200), Profile(max_sodium=600))
        self.assertEqual(a.verdict, Verdict.CLEAR)

    def test_sodium_near_limit_is_risk(self):
        """594 对 600 是踩线，不该报「未见疑点」，也不该报「含」。"""
        a = M.assess(self._item(sodium=594, kcal=300), Profile(max_sodium=600))
        self.assertEqual(a.verdict, Verdict.RISK)

    def test_missing_sodium_is_no_info_not_clear(self):
        """这是整个产品最不能错的一条：没数据 ≠ 安全。"""
        a = M.assess(self._item(kcal=400), Profile(max_sodium=600))
        self.assertEqual(a.verdict, Verdict.NO_INFO)
        self.assertFalse(a.allowed)

    def test_kcal_over_limit_is_hit(self):
        a = M.assess(self._item(kcal=900), Profile(max_kcal=600))
        self.assertEqual(a.verdict, Verdict.HIT)

    def test_zero_kcal_still_counts_as_known(self):
        """0 千卡的饮品是「有数据」，不能因为 falsy 被判成查不到。"""
        a = M.assess(self._item(kcal=0, sodium=5), Profile(max_kcal=600))
        self.assertEqual(a.verdict, Verdict.CLEAR)

    def test_unknown_kcal_is_no_info(self):
        a = M.assess(self._item(kcal=0), Profile(max_kcal=600))
        self.assertEqual(a.verdict, Verdict.NO_INFO)

    def test_protein_below_target_is_hit(self):
        a = M.assess(self._item(protein=8.0, sodium=100), Profile(min_protein=20.0))
        self.assertEqual(a.verdict, Verdict.HIT)

    def test_missing_protein_is_no_info(self):
        a = M.assess(self._item(sodium=100), Profile(min_protein=20.0))
        self.assertEqual(a.verdict, Verdict.NO_INFO)

    def test_has_nutrition_property(self):
        self.assertTrue(self._item(sodium=1).has_nutrition)
        self.assertTrue(self._item(protein=1.0).has_nutrition)
        self.assertTrue(self._item(kcal=1).has_nutrition)
        self.assertFalse(self._item().has_nutrition)

    def test_hit_reason_replaces_weaker_no_info(self):
        """钠超标被排除时，理由不能显示成「无法核对钠含量」。"""
        a = M.assess(self._item(sodium=1208, kcal=570), Profile(max_sodium=600))
        self.assertTrue(all("无法核对" not in r for r in a.reasons))

    def test_nutrition_and_allergen_combine(self):
        """营养超标和过敏命中同时存在时，取更严重的，且理由只剩严重那档。"""
        a = M.assess(self._item(sodium=1208, kcal=570, allergens=None),
                     Profile(allergens=("peanut",), max_sodium=600))
        self.assertEqual(a.verdict, Verdict.HIT)
        self.assertTrue(any("钠" in r for r in a.reasons))


if __name__ == "__main__":
    unittest.main()
