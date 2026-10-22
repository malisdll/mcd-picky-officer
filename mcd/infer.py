"""从餐品名称推断忌口相关属性。

为什么必须做这一步：麦当劳 MCP 全链路（菜单 / 餐品详情 / 营养表）都不返回配料与
过敏原字段，唯一能用的中文信息就是餐品名称本身。

所以这里的每一条结论都只是**推断**，不是官方事实：
- 高置信（high）：名称里直接出现关键词，例如「麦辣」能确定辣
- 低置信（low）：只能给出相关线索，例如「堡」可能含麸质
- 无信息（None）：名称里看不出任何东西

推断结果在界面上必须和官方数据区分展示，不能混为一谈。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 顺序很重要：先匹配更具体的词（鱼 > 猪 > 牛 > 鸡），避免「鱼堡」被判成牛肉。
# 但光靠顺序挡不住「牛奶 → 牛肉」「鸡蛋 → 鸡肉」这类同一个字出现在非肉类词里的情况，
# 所以每个肉类规则还带一份排除词，匹配前先把排除词涂掉。
_MEAT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("fish", ("三文鱼", "鳕鱼", "深海鱼", "虾", "蟹", "麦香鱼", "鱼堡", "鱼排"), ()),
    ("pork", ("猪柳", "培根", "火腿", "猪", "烟肉"), ()),
    ("beef", ("安格斯", "厚牛", "牛肉", "牛堡", "巨无霸", "双层吉士", "吉士汉堡", "牛"),
     ("牛奶", "牛油果", "牛轧")),
    ("chicken", ("鸡", "翅", "腿", "麦乐鸡", "麦脆汁"), ("鸡蛋", "鸡尾")),
)

_MASK = "＿"

_SPICY_WORDS = ("辣", "麻辣", "藤椒", "香辣", "龙焰", "椒麻")
_FRIED_WORDS = ("薯条", "鸡翅", "鸡块", "鸡排", "炸", "脆汁鸡", "脆卜卜", "麦乐鸡", "鸡米花", "洋葱圈", "派")

# 过敏原相关线索：只用于「可能含有，需确认」，绝不当作「含」
_ALLERGEN_HINTS: dict[str, tuple[str, ...]] = {
    "milk": ("牛奶", "奶", "芝士", "奶酪", "冰淇淋", "新地", "麦旋风", "拿铁", "玛奇朵",
             "雪冰", "奶铁", "圣代", "圆筒", "阿芙佳朵", "燕麦奶", "黄油"),
    "gluten": ("堡", "面包", "麦满分", "派", "薯条", "卷", "脆", "酥"),
    "egg": ("蛋", "蛋黄酱"),
    "fish": ("鱼", "鳕", "三文鱼", "虾"),
    "soy": ("豆浆", "豆", "酱油"),
    "peanut": ("花生",),
    "treenut": ("坚果", "核桃", "杏仁", "腰果", "榛子"),
    "sesame": ("芝麻",),
}


@dataclass
class Inference:
    """一次名称推断的全部结果。"""

    spicy: int = 0                      # 0–3
    fried: bool = False
    meat: str | None = None
    caffeine: bool = False
    hints: dict[str, str] = field(default_factory=dict)   # 过敏原 id → 名称依据
    evidence: list[str] = field(default_factory=list)     # 人类可读的依据

    def describe(self) -> str:
        parts = []
        if self.spicy:
            parts.append(f"辣度{self.spicy}")
        if self.fried:
            parts.append("油炸")
        if self.meat:
            parts.append(f"主料{self.meat}")
        if self.hints:
            parts.append("可能含：" + "、".join(sorted(self.hints)))
        return "，".join(parts) if parts else "名称中无可用信息"


def _first(text: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if w in text:
            return w
    return None


def _mask(text: str, words: tuple[str, ...]) -> str:
    """把排除词涂成等长占位符，长度不变、位置不乱。"""
    for w in words:
        text = text.replace(w, _MASK * len(w))
    return text


def infer_from_name(name: str) -> Inference:
    """根据餐品名称做保守推断。"""
    text = re.sub(r"[【】（）()\s]", "", name or "")
    inf = Inference()

    # --- 辣度 ---
    spicy_hit = _first(text, _SPICY_WORDS)
    if spicy_hit:
        inf.spicy = 2
        inf.evidence.append(f"名称含「{spicy_hit}」")

    # --- 油炸 ---
    fried_hit = _first(text, _FRIED_WORDS)
    if fried_hit:
        inf.fried = True
        inf.evidence.append(f"名称含「{fried_hit}」")

    # --- 肉类 ---
    for meat, words, excludes in _MEAT_RULES:
        probe = _mask(text, excludes)
        hit = _first(probe, words)
        if hit:
            inf.meat = meat
            inf.evidence.append(f"名称含「{hit}」→ {meat}")
            break

    # --- 咖啡因 ---
    if "咖啡" in text or "拿铁" in text or "美式" in text or "玛奇朵" in text:
        inf.caffeine = True
        inf.evidence.append("名称含咖啡类")

    # --- 过敏原线索（只作「可能含有」）---
    for aid, words in _ALLERGEN_HINTS.items():
        hit = _first(text, words)
        if hit and aid not in inf.hints:
            inf.hints[aid] = hit

    return inf


def infer_items(names: list[str]) -> dict[str, Inference]:
    return {n: infer_from_name(n) for n in names}
