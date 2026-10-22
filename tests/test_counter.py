"""柜台问询清单的测试。

这个模块存在的理由：实测发现官方 MCP 没有任何配料/过敏原字段，所以对花生过敏的人，
99 个在售餐品里没有一个能被「官方确认可吃」。如果产品只输出「全部信息不足」，
它就等于没用。问询清单把这些查不到的餐品变成几句能直接念给店员的话。

所以测试重点不在文案，而在三件事：
1. 交叉接触那一问必须排在前面（严重过敏最常见的出事方式，且任何接口都查不到）
2. 已经明确排除的餐品不该再问（问了也没用）
3. 对饮品问「放辣椒吗」这种废话必须被过滤掉（否则用户不会念完清单）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd import counter  # noqa: E402
from mcd.matcher import Matcher  # noqa: E402
from mcd.models import Assessment, Item, Profile, Verdict  # noqa: E402
from mcd.store import load_lexicon  # noqa: E402

LEX = load_lexicon()
M = Matcher(LEX)
PEANUT = Profile(allergens=("peanut",))


def item(name: str, category: str = "main", price: int = 2000) -> Item:
    return Item(id=name, name=name, category=category, price=price, allergens=None)


def a(name: str, verdict: Verdict, category: str = "main") -> Assessment:
    return Assessment(item=item(name, category), verdict=verdict, reasons=[])


class TestUniversalQuestions(unittest.TestCase):
    def test_cross_contact_comes_first(self):
        qs = counter.universal_questions(LEX, PEANUT)
        self.assertTrue(qs)
        self.assertIn("炸锅", qs[0])
        self.assertIn("花生", qs[0])

    def test_nutrition_question_for_sodium(self):
        qs = counter.universal_questions(LEX, Profile(max_sodium=600))
        self.assertTrue(any("营养成分表" in q for q in qs))

    def test_diet_question(self):
        qs = counter.universal_questions(LEX, Profile(diets=("vegan",)))
        self.assertTrue(any("动物来源" in q for q in qs))

    def test_empty_profile_asks_nothing(self):
        self.assertEqual(counter.universal_questions(LEX, Profile()), [])


class TestQuestionsFor(unittest.TestCase):
    def test_allergen_question_uses_lexicon(self):
        qs = counter.questions_for(a("麦乐鸡", Verdict.NO_INFO), LEX, PEANUT)
        self.assertTrue(any("花生油" in q for q in qs))

    def test_avoid_question(self):
        qs = counter.questions_for(a("麦辣鸡腿汉堡", Verdict.NO_INFO), LEX, Profile(avoid=("spicy",)))
        self.assertTrue(any("辣" in q for q in qs))

    def test_fried_question(self):
        qs = counter.questions_for(a("薯条", Verdict.NO_INFO), LEX, Profile(avoid=("fried",)))
        self.assertTrue(any("油炸" in q for q in qs))

    def test_sodium_question_carries_the_limit(self):
        qs = counter.questions_for(a("麦香鸡", Verdict.NO_INFO), LEX, Profile(max_sodium=600))
        self.assertTrue(any("600" in q for q in qs))

    def test_drink_is_not_asked_about_spicy(self):
        """对可乐问「放辣椒了吗」会把清单的可信度毁掉。"""
        qs = counter.questions_for(a("可乐", Verdict.NO_INFO, "drink"), LEX,
                                   Profile(allergens=("peanut",), avoid=("spicy",)))
        self.assertTrue(any("花生" in q for q in qs))
        self.assertFalse(any("辣椒" in q for q in qs))

    def test_dessert_is_not_asked_about_spicy(self):
        qs = counter.questions_for(a("圆筒冰淇淋", Verdict.NO_INFO, "dessert"), LEX,
                                   Profile(avoid=("spicy",)))
        self.assertEqual(qs, [])

    def test_only_user_selected_items_are_asked(self):
        """用户只设了控钠，就只问钠，不要自作主张问一堆过敏原。"""
        qs = counter.questions_for(a("麦香鸡", Verdict.NO_INFO), LEX, Profile(max_sodium=600))
        self.assertEqual(len(qs), 1)
        self.assertIn("钠", qs[0])


class TestBuild(unittest.TestCase):
    def test_groups_similar_questions_together(self):
        """86 个餐品要问的话一模一样，逐条列只会把关键那句埋掉。"""
        prof = Profile(allergens=("peanut",), avoid=("spicy",))
        rows = [a("麦乐鸡", Verdict.NO_INFO), a("麦香鸡", Verdict.NO_INFO),
                a("可乐", Verdict.NO_INFO, "drink")]
        r = counter.build(rows, LEX, prof)
        # 主食一组（问花生 + 辣椒），饮品一组（只问花生）
        self.assertEqual(len(r["groups"]), 2)
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["groups"][0]["count"], 2)
        self.assertFalse(any("辣椒" in q for q in r["groups"][1]["asks"]))

    def test_excluded_items_are_not_asked_about(self):
        """已经明确排除的餐品不必再问——问了也不会吃。"""
        rows = [a("麦辣鸡腿汉堡", Verdict.HIT), a("苹果片", Verdict.CLEAR),
                a("麦乐鸡", Verdict.NO_INFO)]
        r = counter.build(rows, LEX, PEANUT)
        self.assertEqual(r["total"], 1)
        self.assertIn("麦乐鸡", r["groups"][0]["names"])

    def test_risk_items_are_included(self):
        """「可能含有」必须问，它是最需要确认的一类。"""
        r = counter.build([a("薄皮焦香V翅", Verdict.RISK)], LEX, PEANUT)
        self.assertEqual(r["total"], 1)

    def test_nothing_to_ask(self):
        r = counter.build([a("苹果片", Verdict.CLEAR)], LEX, PEANUT)
        self.assertEqual(r["groups"], [])
        self.assertIn("没有需要单独确认", counter.render_text(r))

    def test_name_list_is_capped(self):
        rows = [a(f"餐品{i}", Verdict.NO_INFO) for i in range(30)]
        r = counter.build(rows, LEX, PEANUT, limit_names=5)
        self.assertEqual(len(r["groups"][0]["names"]), 5)
        self.assertEqual(r["groups"][0]["count"], 30)

    def test_render_mentions_cross_contact_and_count(self):
        rows = [a("麦乐鸡", Verdict.NO_INFO)]
        text = counter.render_text(counter.build(rows, LEX, PEANUT))
        self.assertIn("炸锅", text)
        self.assertIn("花生油", text)
        self.assertIn("麦乐鸡", text)
        self.assertIn("去柜台", text)


class TestEndToEndWithSampleMenu(unittest.TestCase):
    def test_real_menu_produces_usable_questions(self):
        from mcd.store import sample_items
        prof = Profile(allergens=("peanut",), avoid=("spicy",))
        rows = M.assess_all(sample_items(), prof)
        r = counter.build(rows, LEX, prof)
        self.assertTrue(r["universal"])
        self.assertTrue(r["groups"])
        for g in r["groups"]:
            self.assertTrue(g["asks"])
            self.assertTrue(g["names"])


if __name__ == "__main__":
    unittest.main()
