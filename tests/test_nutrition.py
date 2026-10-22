"""官方营养表解析的测试。

解析对象的真实长相（2026-10-22 实测），不是想象出来的 JSON：

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213

所以测试重点全在「脏数据的边界」：null、空列、列数不齐、重复名、套餐不能冒用单品值。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd import nutrition as nut  # noqa: E402

HEADER = ("[3]{productName,nutritionDescription,energyKj,energyKcal,"
          "protein,fat,carbohydrate,sodium,calcium}:")
TABLE = HEADER + "\n" + "\n".join([
    "  猪柳麦满分,null,1288,308,16,16,24,781,213",
    "  苹果片,null,180,43,0.3,0.1,10,1,3",
    "  纯牛奶,null,410,98,6.4,3.6,9.6,100,220",
])


class TestParseTable(unittest.TestCase):
    def test_row_count_from_header(self):
        rows = nut.parse_table(TABLE)
        self.assertEqual(len(rows), 3)

    def test_values_mapped_by_column_name(self):
        """必须按表头字段名取列，不能按位置硬编码——官方列序是可能变的。"""
        first = nut.parse_table(TABLE)[0]
        self.assertEqual(first.name, "猪柳麦满分")
        self.assertEqual(first.kcal, 308)
        self.assertEqual(first.sodium, 781)
        self.assertEqual(first.calcium, 213)
        self.assertAlmostEqual(first.protein, 16.0)

    def test_null_field_is_none(self):
        raw = HEADER + "\n  某餐品,null,100,24,null,null,null,null,null"
        row = nut.parse_table(raw)[0]
        self.assertEqual(row.kcal, 24)     # 第 4 列 energyKcal 有值
        self.assertIsNone(row.sodium)      # null 必须变成 None，不能变成 0
        self.assertIsNone(row.protein)
        self.assertEqual(row.name, "某餐品")

    def test_short_row_skipped(self):
        """列数不齐的行必须跳过。错位比缺数据危险得多。"""
        raw = HEADER + "\n  正常,null,100,24,1,1,1,50,10\n  残缺,null,100,24"
        self.assertEqual(len(nut.parse_table(raw)), 1)

    def test_no_header_returns_empty(self):
        self.assertEqual(nut.parse_table("随便一段话"), [])

    def test_empty_input(self):
        self.assertEqual(nut.parse_table(""), [])
        self.assertEqual(nut.parse_table(None), [])  # type: ignore[arg-type]

    def test_missing_required_column_returns_empty(self):
        """缺 sodium 列就整张表作废，而不是给出一个「钠=某列」的错值。"""
        raw = "[1]{productName,energyKcal}: \n  某餐品,100"
        self.assertEqual(nut.parse_table(raw), [])

    def test_comma_inside_name_survives(self):
        raw = HEADER + "\n  \"堡,加芝士\",null,100,24,1,1,1,50,10"
        self.assertEqual(nut.parse_table(raw)[0].name, "堡,加芝士")


class TestIndexAndLookup(unittest.TestCase):
    def setUp(self):
        self.index = nut.build_index(nut.parse_table(TABLE))

    def test_lookup_exact(self):
        self.assertIsNotNone(nut.lookup(self.index, "苹果片"))

    def test_duplicate_name_keeps_first(self):
        raw = HEADER + "\n  同名,null,100,10,1,1,1,1,1\n  同名,null,900,90,9,9,9,9,9"
        idx = nut.build_index(nut.parse_table(raw))
        self.assertEqual(idx["同名"].kcal, 10)

    def test_set_meal_does_not_borrow_single_item_values(self):
        """核心不变量：套餐 ≠ 单品，绝不模糊匹配。

        「麦辣鸡腿汉堡三件套」如果套用「麦辣鸡腿汉堡」的钠值，就是在编数据。
        """
        self.assertIsNone(nut.lookup(self.index, "麦辣鸡腿汉堡三件套"))

    def test_whitespace_tolerated(self):
        self.assertIsNotNone(nut.lookup(self.index, "  苹果片  "))


class TestRealSnapshot(unittest.TestCase):
    def test_header_column_count_matches_official(self):
        """官方表头就是这 9 列。列数变了说明接口改版，这个测试要提醒我们。"""
        fields = ("productName,nutritionDescription,energyKj,energyKcal,"
                  "protein,fat,carbohydrate,sodium,calcium").split(",")
        self.assertEqual(len(fields), 9)
        raw = f"[1]{{{','.join(fields)}}}:\n  某餐品,null,1,2,3,4,5,6,7"
        row = nut.parse_table(raw)[0]
        self.assertEqual((row.name, row.kcal, row.sodium, row.calcium), ("某餐品", 2, 6, 7))


if __name__ == "__main__":
    unittest.main()
