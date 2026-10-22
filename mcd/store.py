"""数据加载，以及把 MCP 返回值适配成本项目的 Item。

适配层存在的理由：MCP 的工具返回字段名可能变动，而上层算法只认 Item。
把「不确定的外部格式」挡在一个函数里，是让 CI 能离线跑绿的前提。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Coupon, Item

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_lexicon(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DATA / "lexicon.json"
    return json.loads(p.read_text(encoding="utf-8"))


def load_sample_menu(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DATA / "sample_menu.json"
    return json.loads(p.read_text(encoding="utf-8"))


def sample_items(menu: dict[str, Any] | None = None) -> list[Item]:
    menu = menu or load_sample_menu()
    return [Item.from_dict(d) for d in menu["items"]]


def sample_coupons(menu: dict[str, Any] | None = None) -> list[Coupon]:
    menu = menu or load_sample_menu()
    return [Coupon.from_dict(d) for d in menu.get("coupons", [])]


# ---- MCP 返回值适配 ----

_PRICE_KEYS = ("price", "currentPrice", "salePrice", "amount", "unitPrice")
_NAME_KEYS = ("name", "productName", "itemName", "title")
_ID_KEYS = ("id", "productId", "itemId", "code", "sku")


def _pick(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def item_from_mcp(d: dict[str, Any], fallback_index: int = 0) -> Item | None:
    """尽量宽容地把一个 MCP 餐品对象映射成 Item。

    价格若明显是「元」而不是「分」（小于 1000 的浮点数），做一个保守换算；
    拿不准就返回 None —— 宁可少一个餐品，也不要塞一个错价格。
    """
    name = _pick(d, _NAME_KEYS)
    price_raw = _pick(d, _PRICE_KEYS)
    if not name or price_raw is None:
        return None

    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        return None

    # 官方接口普遍以「分」为单位（例如 11450 表示 114.50 元）。
    # 若出现小数或极小值，按「元」处理。
    if isinstance(price_raw, float) and not price_raw.is_integer():
        price_cents = int(round(price * 100))
    elif price < 1000:
        price_cents = int(round(price * 100))
    else:
        price_cents = int(price)

    raw_allergens = d.get("allergens") or d.get("allergen") or d.get("allergenList")
    allergens: tuple[str, ...] | None
    if isinstance(raw_allergens, list):
        allergens = tuple(str(x).lower() for x in raw_allergens)
    elif isinstance(raw_allergens, str) and raw_allergens.strip():
        allergens = tuple(x.strip().lower() for x in raw_allergens.replace("、", ",").split(",") if x.strip())
    else:
        allergens = None

    return Item(
        id=str(_pick(d, _ID_KEYS) or f"mcp-{fallback_index}"),
        name=str(name),
        category=str(d.get("category") or d.get("type") or "main"),
        price=price_cents,
        desc=str(d.get("desc") or d.get("description") or d.get("ingredients") or ""),
        allergens=allergens,
        meat=d.get("meat"),
        spicy=int(d.get("spicy") or d.get("spicyLevel") or 0),
        fried=bool(d.get("fried", False)),
        caffeine=bool(d.get("caffeine", False)),
        kcal=int(d.get("kcal") or d.get("calorie") or d.get("energy") or 0),
    )


def items_from_mcp(payload: Any) -> list[Item]:
    """从 tools/call 的返回里捞出所有餐品。容忍多层嵌套。"""
    rows: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            if _pick(node, _NAME_KEYS) and _pick(node, _PRICE_KEYS) is not None:
                rows.append(node)
            else:
                for v in node.values():
                    walk(v)

    walk(payload)
    items = [item_from_mcp(d, i) for i, d in enumerate(rows)]
    return [i for i in items if i is not None]
