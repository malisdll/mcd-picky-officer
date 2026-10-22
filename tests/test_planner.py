"""方案生成与金额计算的单元测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd.matcher import Matcher  # noqa: E402
from mcd.models import Coupon, Profile, Verdict  # noqa: E402
from mcd.planner import allowed_items, build_plans  # noqa: E402
from mcd.pricing import best_coupon, closest_threshold, split_evenly, yuan  # noqa: E402
from mcd.store import load_lexicon, sample_coupons, sample_items  # noqa: E402

LEX = load_lexicon()
M = Matcher(LEX)
ITEMS = sample_items()
COUPONS = sample_coupons()


class TestMoney(unittest.TestCase):
    def test_yuan(self):
        self.assertEqual(yuan(2450), "24.50")
        self.assertEqual(yuan(5), "0.05")
        self.assertEqual(yuan(0), "0.00")

    def test_split_sums_exactly(self):
        for total in (100, 101, 999, 1):
            for n in (1, 3, 7):
                parts = split_evenly(total, n)
                self.assertEqual(sum(parts), total)
                self.assertLessEqual(max(parts) - min(parts), 1)

    def test_best_coupon(self):
        c, d = best_coupon(5000, COUPONS)
        self.assertIsNotNone(c)
        self.assertEqual(d, 800)

    def test_no_coupon_below_threshold(self):
        c, d = best_coupon(1000, COUPONS)
        self.assertIsNone(c)
        self.assertEqual(d, 0)

    def test_closest_threshold(self):
        near = closest_threshold(2600, COUPONS)
        self.assertIsNotNone(near)
        c, gap = near
        self.assertEqual(c.threshold - 2600, gap)

    def test_discount_never_exceeds_subtotal(self):
        weird = [Coupon("x", "离谱券", 100, 99999)]
        c, d = best_coupon(100, weird)
        self.assertEqual(d, 100)


class TestPlanner(unittest.TestCase):
    def _plans(self, profile: Profile):
        return build_plans(M.assess_all(ITEMS, profile), profile, COUPONS)

    def test_budget_respected(self):
        plans = self._plans(Profile(budget=2500))
        real = [p for p in plans if p.items]
        self.assertTrue(real)
        for p in real:
            self.assertLessEqual(p.subtotal, 2500)

    def test_never_includes_hit(self):
        profile = Profile(allergens=("gluten",))
        assessments = M.assess_all(ITEMS, profile)
        hit_ids = {a.item.id for a in assessments if a.verdict == Verdict.HIT}
        for p in build_plans(assessments, profile, COUPONS):
            for item in p.items:
                self.assertNotIn(item.id, hit_ids)

    def test_never_includes_no_info(self):
        """「信息不足」的餐品不许进方案 —— 这是本项目的核心约束。"""
        profile = Profile(allergens=("peanut",))
        assessments = M.assess_all(ITEMS, profile)
        noinfo_ids = {a.item.id for a in assessments if a.verdict == Verdict.NO_INFO}
        self.assertTrue(noinfo_ids, "示例数据里应当存在信息缺失的餐品")
        for p in build_plans(assessments, profile, COUPONS):
            for item in p.items:
                self.assertNotIn(item.id, noinfo_ids)

    def test_risk_allowed_only_with_flag(self):
        """「可能含有」默认排除；显式加 --risk 后才进方案。"""
        profile = Profile(allergens=("egg",))
        assessments = M.assess_all(ITEMS, profile)
        risky = [a for a in assessments if a.verdict == Verdict.RISK]
        self.assertTrue(risky, "示例数据里应当有产生 RISK 的餐品（如描述含「裹粉」的鸡翅）")
        risky_ids = {a.item.id for a in risky}

        default = build_plans(assessments, profile, COUPONS)
        ids_default = {i.id for p in default for i in p.items}
        self.assertFalse(risky_ids & ids_default)

        allow = Profile(allergens=("egg",), allow_risk=True)
        relaxed_ids = {i.id for i in allowed_items(M.assess_all(ITEMS, allow), allow)}
        # 允许之后，RISK 餐品进入候选池
        self.assertTrue(risky_ids & relaxed_ids)

    def test_plan_without_side_is_considered(self):
        """回归测试：不能因为「配餐加不进预算」就判定整体预算不够。

        曾经的 bug：候选组合强制带配餐，导致 25 元预算却报「预算不够」。
        """
        profile = Profile(avoid=("fried",), budget=2500)
        plans = build_plans(M.assess_all(ITEMS, profile), profile, COUPONS)
        self.assertFalse(any(p.label == "预算不够" for p in plans))
        self.assertTrue(any(p.items for p in plans))

    def test_over_budget_is_honest(self):
        """预算不够时必须说不够，而不是硬塞一个超预算方案。"""
        plans = self._plans(Profile(budget=500))
        self.assertTrue(any(p.label == "预算不够" for p in plans))
        self.assertFalse(any(p.items for p in plans))

    def test_vegan_has_no_main(self):
        plans = self._plans(Profile(diets=("vegan",), budget=4000))
        self.assertEqual(plans[0].label, "没有可选主食")
        self.assertTrue(plans[0].notes)

    def test_plan_has_main(self):
        plans = [p for p in self._plans(Profile(budget=3000)) if p.items]
        for p in plans:
            self.assertTrue(p.has("main"))

    def test_no_duplicate_plans(self):
        plans = self._plans(Profile(budget=6000))
        keys = [tuple(sorted(i.id for i in p.items)) for p in plans if p.items]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
