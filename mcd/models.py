"""麦麦挑食官 · 核心数据模型。

金额一律使用「分」（int），避免浮点误差。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Verdict(IntEnum):
    """忌口判定的三态 + 一态。

    刻意不使用「安全 / 不安全」的说法 —— 我们只报告「有没有发现疑点」，
    最终判断权留给用户和门店。这是本项目的核心设计约束。
    """

    CLEAR = 0      # 官方数据已标注，且未列出该过敏原
    NO_INFO = 1    # 没有可用数据，无法判断
    RISK = 2       # 可能含有 / 需向门店确认
    HIT = 3        # 名称、描述或官方标注中明确出现

    @property
    def label(self) -> str:
        return {
            Verdict.CLEAR: "未见疑点",
            Verdict.NO_INFO: "信息不足",
            Verdict.RISK: "可能含有",
            Verdict.HIT: "含",
        }[self]

    @property
    def hint(self) -> str:
        return {
            Verdict.CLEAR: "官方标注中未列出",
            Verdict.NO_INFO: "官方未提供配料信息",
            Verdict.RISK: "建议向门店确认",
            Verdict.HIT: "已命中你的忌口",
        }[self]


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    category: str          # main / side / drink / dessert
    price: int             # 分
    desc: str = ""
    allergens: tuple[str, ...] | None = None   # None = 官方无数据；() = 官方明确标注无
    meat: str | None = None
    spicy: int = 0
    fried: bool = False
    caffeine: bool = False
    kcal: int = 0

    # ---- 官方营养数据（来自 list-nutrition-foods，精确值）----
    sodium: int | None = None      # mg
    protein: float | None = None   # g

    # ---- 名称推断线索（非官方数据，只能支撑「可能含有」）----
    hints: dict[str, str] = field(default_factory=dict)   # 过敏原 id → 名称依据

    # ---- 数据来源标记，用于在界面上区分「官方」与「推断」----
    data_source: str = "sample"    # sample / live

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Item":
        al = d.get("allergens", None)
        return cls(
            id=str(d["id"]),
            name=str(d["name"]),
            category=str(d.get("category", "main")),
            price=int(d["price"]),
            desc=str(d.get("desc", "")),
            allergens=None if al is None else tuple(al),
            meat=d.get("meat"),
            spicy=int(d.get("spicy", 0)),
            fried=bool(d.get("fried", False)),
            caffeine=bool(d.get("caffeine", False)),
            kcal=int(d.get("kcal", 0)),
            sodium=d.get("sodium"),
            protein=d.get("protein"),
            hints=dict(d.get("hints") or {}),
            data_source=str(d.get("data_source", "sample")),
        )

    @property
    def searchable(self) -> str:
        """用于关键词匹配的合并文本。"""
        return f"{self.name} {self.desc}"

    @property
    def has_nutrition(self) -> bool:
        """是否挂着可用的营养数值。

        不能用 kcal == 0 当「无数据」——真有无糖饮品是 0 千卡；
        也不能默认 kcal 有值就是有数据——示例数据里的 kcal 是手写参考值。
        所以三个字段任一有值才算有数据，这与「0 不等于未知」的原则一致。
        """
        return self.sodium is not None or self.protein is not None or self.kcal > 0


@dataclass
class Assessment:
    item: Item
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """默认只放行 CLEAR。

        NO_INFO（官方无配料数据）同样不进方案 —— 没有证据就不是证据，
        把它和「未见疑点」分开处理，是这东西敢用在真过敏的人身上的前提。
        """
        return self.verdict == Verdict.CLEAR


@dataclass(frozen=True)
class Coupon:
    id: str
    name: str
    threshold: int
    discount: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Coupon":
        return cls(
            id=str(d["id"]),
            name=str(d["name"]),
            threshold=int(d["threshold"]),
            discount=int(d["discount"]),
        )


@dataclass
class Plan:
    label: str
    items: list[Item]
    subtotal: int
    coupon: Coupon | None = None
    discount: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return max(0, self.subtotal - self.discount)

    @property
    def kcal(self) -> int:
        return sum(i.kcal for i in self.items)

    def has(self, category: str) -> bool:
        return any(i.category == category for i in self.items)


@dataclass(frozen=True)
class Profile:
    """用户的忌口档案。immutable，便于缓存与测试。"""

    allergens: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    diets: tuple[str, ...] = ()
    budget: int | None = None      # 分/人
    headcount: int = 1
    allow_risk: bool = False       # 是否允许「可能含有」的餐品进入方案

    # 营养目标：这些是官方营养表能给到的精确约束
    max_sodium: int | None = None   # mg/份
    max_kcal: int | None = None     # kcal/份
    min_protein: float | None = None  # g/份

    @property
    def is_empty(self) -> bool:
        return not (self.allergens or self.avoid or self.diets
                    or self.max_sodium or self.max_kcal or self.min_protein)
