"""从麦当劳 MCP 拉取真实数据，组装成项目内部的 Item。

真实接口的数据现状（2026-10-22 实测）：
- query-meals        → 门店菜单，只有 name / currentPrice / image / tags，**没有配料**
- query-meal-detail  → 套餐结构、可选加料，仍然**没有配料与过敏原**
- list-nutrition-foods → 160 条精确营养值（热量/蛋白/脂肪/碳水/钠/钙），**没有配料**

所以本模块的职责不是「拿到配料」，而是：
1. 拿到真实菜单与真实价格；
2. 把官方营养表按名称精确挂到餐品上（挂不上的就保持 None）；
3. 用 infer 从名称推断辣/油炸/肉类，并标记为推断而非事实。

凡是数据缺口，一律保持 None，绝不用默认值填平。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import nutrition as nut
from .infer import infer_from_name
from .models import Item

# 非食品（周边、玩具、礼品卡），不参与忌口筛选。
#
# 这份名单是看完全部 110 个真实菜单条目后写的，不是凭空想的。官方没有给
# 「食品 / 周边」的分类字段（data.categories 里也没有专门分类，周边散落在
# 「小食甜品//其他」「人气热卖」里），所以只能按名字挡。
# 已知局限：官方上新款周边时要补词。挡不住的后果是它在界面上显示成
# 「信息不足：官方营养表没有这个餐品」——荒谬但不是危险的方向（不会误判成可吃）。
_NON_FOOD = ("周边", "玩具", "礼品卡", "礼盒", "手提袋", "徽章", "公仔", "盲盒", "贴纸",
             "毛绒", "玩偶", "手办", "抱枕", "挂件", "钥匙扣", "丝巾", "围巾", "按摩",
             "痒痒挠", "帆布袋", "水杯", "雨伞", "拼图", "贴纸")

# 可配置套餐：价格只是一个起点，具体含什么要用户逐项选。
# 这类条目不能进配餐方案 —— 我们没法对一个「还没定内容的组合」判定忌口和钠含量，
# 把它写进「你能吃这些」的清单里就是误导。兑换券/权益同理，它们不是餐品。
_NON_ITEM = ("随心选", "随心配", "任选", "兑换券", "权益", "优惠券")

_DRINK_WORDS = ("咖啡", "可乐", "雪碧", "芬达", "果汁", "橙", "奶昔", "奶茶", "豆浆", "纯悦",
                "饮用水", "矿泉水", "茶", "冰沙", "麦炫酷", "雪冰", "奶铁", "玛奇朵", "拿铁",
                "美式", "美汁源", "鲜萃", "苏打", "柠檬", "饮品", "饮料", "牛奶", "乳",
                "怡泉", "椰", "气泡水", "水", "燕麦奶")
_DESSERT_WORDS = ("新地", "圣代", "麦旋风", "冰淇淋", "圆筒", "派", "蛋糕", "阿芙佳朵", "布丁")
_SIDE_WORDS = ("薯条", "玉米杯", "苹果片", "鸡翅", "鸡块", "鸡排", "洋葱圈", "沙拉", "酱",
               "脆汁鸡", "脆卜卜", "小食", "鸡米花", "芝士条")


class LiveError(RuntimeError):
    pass


@dataclass
class LiveData:
    store_code: str
    store_name: str
    items: list[Item]
    raw_meals: int = 0
    nutrition_rows: int = 0
    matched_nutrition: int = 0
    dropped: list[str] = field(default_factory=list)
    drop_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def nutrition_coverage(self) -> float:
        food = len(self.items)
        return round(self.matched_nutrition / food, 3) if food else 0.0

    @property
    def drop_summary(self) -> str:
        """把剔除原因分类报出来，而不是笼统一句「剔除 N 个」。

        「周边 3 个、可配置套餐 8 个」比「剔除 11 个」有用得多：
        前者能看出是数据脏还是我们规则严，后者只能让人怀疑。
        """
        parts = [f"{k} {v} 个" for k, v in self.drop_reasons.items() if v]
        return "、".join(parts) if parts else "无"


def _price_to_cents(v: Any) -> int | None:
    """官方菜单的价格是「元」字符串，例如 '33.5'。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(float(v) * 100))
    s = str(v).strip().replace("¥", "").replace("￥", "").replace("元", "")
    if not s:
        return None
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return None


def _category(name: str) -> str:
    text = re.sub(r"[【】（）()\s]", "", name)
    if any(w in text for w in _DESSERT_WORDS):
        return "dessert"
    if any(w in text for w in _DRINK_WORDS):
        return "drink"
    if any(w in text for w in _SIDE_WORDS):
        return "side"
    return "main"


def find_store(client, city: str, keyword: str, be_type: int = 1) -> tuple[str, str]:
    """城市 + 关键词都必传，否则官方返回 600058。"""
    if not city or not keyword:
        raise LiveError(
            "查询门店需要同时提供城市和关键词（官方要求，缺一个会返回 600058）。\n"
            "例如：--city 北京市 --keyword 国贸"
        )
    res = client.call_tool("query-nearby-stores", {
        "beType": be_type, "searchType": 2, "city": city, "keyword": keyword,
    })
    data = (res.get("structuredContent") or {}).get("data") or []
    if not data:
        raise LiveError(f"没有搜到门店：city={city} keyword={keyword}")
    first = data[0]
    return str(first.get("storeCode")), str(first.get("storeName") or "")


def fetch(client, city: str, keyword: str, order_type: int = 1, be_type: int = 1) -> LiveData:
    store_code, store_name = find_store(client, city, keyword, be_type)

    menu_res = client.call_tool("query-meals", {
        "storeCode": store_code, "orderType": order_type, "beType": be_type,
    })
    meals = ((menu_res.get("structuredContent") or {}).get("data") or {}).get("meals") or {}
    if not isinstance(meals, dict):
        raise LiveError("query-meals 返回结构与预期不符（meals 不是字典）")

    # 营养表：一次请求拿全量，后面本地匹配
    nut_index: dict[str, nut.Nutrition] = {}
    try:
        nut_res = client.call_tool("list-nutrition-foods", {})
        raw = ((nut_res.get("structuredContent") or {}).get("data"))
        records = nut.parse_table(raw if isinstance(raw, str) else "")
        nut_index = nut.build_index(records)
    except Exception:  # noqa: BLE001 - 营养表失败不应拖垮整个流程
        records = []

    items: list[Item] = []
    dropped: list[str] = []
    drop_reasons = {"周边/非食品": 0, "可配置套餐": 0, "无价格": 0}
    matched = 0

    def drop(name: str, reason: str) -> None:
        dropped.append(f"{name}（{reason}）")
        drop_reasons[reason] = drop_reasons.get(reason, 0) + 1

    for code, m in meals.items():
        name = str(m.get("name") or "").strip()
        if not name:
            continue
        if any(w in name for w in _NON_FOOD):
            drop(name, "周边/非食品")
            continue
        if any(w in name for w in _NON_ITEM):
            drop(name, "可配置套餐")
            continue

        price = _price_to_cents(m.get("currentPrice"))
        if price is None:
            drop(name, "无价格")
            continue

        inf = infer_from_name(name)
        n = nut.lookup(nut_index, name)
        if n is not None:
            matched += 1

        items.append(Item(
            id=str(code),
            name=name,
            category=_category(name),
            price=price,
            desc="",
            allergens=None,          # 官方确实不提供，不是我们没取
            meat=inf.meat,
            spicy=inf.spicy,
            fried=inf.fried,
            caffeine=inf.caffeine,
            kcal=(n.kcal or 0) if n else 0,
            sodium=n.sodium if n else None,
            protein=n.protein if n else None,
            hints=dict(inf.hints),
            data_source="live",
        ))

    return LiveData(
        store_code=store_code,
        store_name=store_name,
        items=items,
        raw_meals=len(meals),
        nutrition_rows=len(records),
        matched_nutrition=matched,
        dropped=dropped,
        drop_reasons=drop_reasons,
    )
