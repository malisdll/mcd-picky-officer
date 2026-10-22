"""忌口档案解析的单元测试。全部离线，不需要 Token。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd.profile import parse_profile  # noqa: E402
from mcd.store import load_lexicon  # noqa: E402

LEX = load_lexicon()


def p(text: str):
    return parse_profile(text, LEX)[0]


class TestAllergen(unittest.TestCase):
    def test_peanut_allergy(self):
        prof = p("我花生过敏")
        self.assertIn("peanut", prof.allergens)

    def test_milk_lactose(self):
        prof = p("我乳糖不耐")
        self.assertIn("milk", prof.allergens)

    def test_shellfish_allergy(self):
        prof = p("吃虾会过敏")
        self.assertIn("shellfish", prof.allergens)

    def test_multiple(self):
        prof = p("花生过敏，牛奶过敏")
        self.assertIn("peanut", prof.allergens)
        self.assertIn("milk", prof.allergens)

    def test_avoid_is_treated_as_allergen(self):
        """"不吃花生" 比 "花生过敏" 宽松，但保守起见同样严格排除。"""
        prof = p("不吃花生")
        self.assertIn("peanut", prof.allergens)


class TestAvoid(unittest.TestCase):
    def test_no_spicy(self):
        self.assertIn("spicy", p("不吃辣").avoid)

    def test_spicy_is_fine(self):
        prof = p("我可以微辣")
        self.assertNotIn("spicy", prof.avoid)

    def test_no_fried(self):
        self.assertIn("fried", p("不吃油炸").avoid)

    def test_no_beef(self):
        self.assertIn("beef", p("不吃牛肉").avoid)

    def test_no_caffeine(self):
        self.assertIn("caffeine", p("不喝咖啡").avoid)

    def test_not_allergen(self):
        """"不吃牛肉" 是偏好，不应该被记成过敏原。"""
        self.assertNotIn("beef", p("不吃牛肉").allergens)


class TestDiet(unittest.TestCase):
    def test_vegan_wins_over_vegetarian(self):
        prof = p("我吃纯素")
        self.assertIn("vegan", prof.diets)

    def test_vegetarian(self):
        self.assertIn("vegetarian", p("我吃素").diets)

    def test_halal(self):
        self.assertIn("halal", p("清真饮食").diets)


class TestBudgetAndCount(unittest.TestCase):
    def test_budget_yuan(self):
        self.assertEqual(p("预算25").budget, 2500)

    def test_budget_per_person(self):
        self.assertEqual(p("人均 30 以内").budget, 3000)

    def test_budget_with_cents(self):
        self.assertEqual(p("预算 24.5").budget, 2450)

    def test_budget_not_from_headcount(self):
        """「6个人」不能被误读成预算 6 元。"""
        prof = p("我们6个人")
        self.assertIsNone(prof.budget)
        self.assertEqual(prof.headcount, 6)

    def test_empty_text(self):
        prof = p("随便吃点")
        self.assertTrue(prof.is_empty)

    def test_trace_is_readable(self):
        _, trace = parse_profile("我花生过敏，不吃辣", LEX)
        self.assertTrue(any("peanut" in t for t in trace))
        self.assertTrue(any("spicy" in t for t in trace))


if __name__ == "__main__":
    unittest.main()
