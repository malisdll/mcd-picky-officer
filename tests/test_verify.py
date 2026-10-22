"""核价与 MCP 适配层的单元测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd.store import item_from_mcp, items_from_mcp  # noqa: E402
from mcd.verify import MATCH, MISMATCH, UNAVAILABLE, compare  # noqa: E402


class TestVerify(unittest.TestCase):
    def test_match(self):
        r = compare(11450, 11450)
        self.assertEqual(r.status, MATCH)
        self.assertTrue(r.ok)

    def test_mismatch_by_one_cent(self):
        r = compare(11450, 11449)
        self.assertEqual(r.status, MISMATCH)
        self.assertEqual(r.delta, -1)

    def test_unavailable(self):
        r = compare(11450, None)
        self.assertEqual(r.status, UNAVAILABLE)
        self.assertFalse(r.ok)

    def test_describe_never_hides_mismatch(self):
        self.assertIn("不一致", compare(100, 200).describe())


class TestMcpAdapter(unittest.TestCase):
    def test_cents(self):
        item = item_from_mcp({"name": "巨无霸", "price": 2450})
        self.assertIsNotNone(item)
        self.assertEqual(item.price, 2450)

    def test_yuan_float_converted(self):
        item = item_from_mcp({"productName": "薯条", "currentPrice": 10.5})
        self.assertEqual(item.price, 1050)

    def test_missing_price_returns_none(self):
        self.assertIsNone(item_from_mcp({"name": "没有价格的餐品"}))

    def test_allergen_list(self):
        item = item_from_mcp({"name": "x", "price": 1000, "allergens": ["milk", "gluten"]})
        self.assertEqual(item.allergens, ("milk", "gluten"))

    def test_allergen_string(self):
        item = item_from_mcp({"name": "x", "price": 1000, "allergen": "milk、gluten"})
        self.assertEqual(item.allergens, ("milk", "gluten"))

    def test_nested_payload(self):
        payload = {
            "content": [
                {"type": "text", "text": "..."},
                {"type": "json", "json": {"items": [
                    {"id": "a", "name": "汉堡", "price": 2000},
                    {"id": "b", "name": "可乐", "price": 1000},
                ]}},
            ]
        }
        items = items_from_mcp(payload)
        self.assertEqual(len(items), 2)

    def test_garbage_payload(self):
        self.assertEqual(items_from_mcp({"foo": "bar"}), [])


if __name__ == "__main__":
    unittest.main()
