"""把「信息不足」翻译成能念给柜台的问句。

为什么需要这个模块：

实测发现麦当劳 MCP 的 35 个工具里没有一个返回配料或过敏原。于是对花生过敏的人，
99 个在售餐品里**没有一个是「官方确认可吃」的**——全部落进「信息不足」。

如果产品到这里就结束，它给出的结论是「你什么都别吃」，等于没用。
真正的出路是：把这些查不到的餐品，变成几句可以直接念给店员听的话。
用户原本要在柜台前一条条问（这也是这个产品的起点），现在话是现成的。
"""
from __future__ import annotations

from typing import Any

from .models import Assessment, Profile, Verdict


def _entry(lexicon: dict[str, Any], group: str, ident: str) -> dict[str, Any]:
    return next((e for e in lexicon.get(group, []) if e.get("id") == ident), {})


def _fallback_ask(lexicon: dict[str, Any], group: str, ident: str) -> str:
    e = _entry(lexicon, group, ident)
    label = e.get("label") or ident
    return e.get("ask") or f"这份含{label}吗"


def universal_questions(lexicon: dict[str, Any], profile: Profile) -> list[str]:
    """不管点什么都该问的那几句。

    交叉接触是所有接口都查不到的一件事，也是严重过敏最常见的出事方式，
    所以它排在具体餐品问题之前。
    """
    asks: list[str] = []
    if profile.allergens:
        labels = "、".join(_entry(lexicon, "allergens", a).get("label", a) for a in profile.allergens)
        asks.append(f"和含{labels}的餐品共用同一个炸锅或操作台吗")
    if profile.diets:
        for d in profile.diets:
            asks.append(_fallback_ask(lexicon, "diet", d) + "？")
    if profile.max_sodium or profile.max_kcal or profile.min_protein:
        asks.append("有包装上的营养成分表可以看一下吗")
    return asks


# 饮品和甜点问「放辣椒吗」是废话。少问一句没意义的，用户才愿意把清单念完。
_IRRELEVANT_AVOID: dict[str, tuple[str, ...]] = {
    "drink": ("spicy", "fried"),
    "dessert": ("spicy",),
    "side": (),
    "main": (),
}


def questions_for(assessment: Assessment, lexicon: dict[str, Any], profile: Profile) -> list[str]:
    """针对单个餐品，把忌口项翻译成问句。只看用户真的设置了的那几项。"""
    asks: list[str] = []
    skip = _IRRELEVANT_AVOID.get(assessment.item.category, ())
    for aid in profile.allergens:
        asks.append(f"{_fallback_ask(lexicon, 'allergens', aid)}？")
    for vid in profile.avoid:
        if vid in skip:
            continue
        asks.append(f"{_fallback_ask(lexicon, 'avoid', vid)}？")
    for did in profile.diets:
        asks.append(f"{_fallback_ask(lexicon, 'diet', did)}？")
    if profile.max_sodium is not None:
        asks.append(f"钠含量是多少？我这边要求不超过 {profile.max_sodium}mg")
    if profile.max_kcal is not None:
        asks.append(f"热量是多少？我这边要求不超过 {profile.max_kcal}kcal")
    if profile.min_protein is not None:
        asks.append(f"蛋白质有多少克？我这边要求至少 {profile.min_protein:g}g")
    return asks


def build(
    assessments: list[Assessment],
    lexicon: dict[str, Any],
    profile: Profile,
    limit_names: int = 12,
) -> dict[str, Any]:
    """生成本次问询清单。

    只收「信息不足」和「可能含有」两类 —— 已经明确排除的不必再问，
    已经确认没疑点的也不需要问。

    按**问句组合**分组，而不是按餐品逐个列。实测 86 个餐品要问的话一模一样，
    逐条列出来只会把真正重要的「交叉接触」那句埋掉。
    """
    targets = [a for a in assessments if a.verdict in (Verdict.NO_INFO, Verdict.RISK)]

    buckets: dict[tuple[str, ...], list[str]] = {}
    for a in targets:
        key = tuple(questions_for(a, lexicon, profile))
        buckets.setdefault(key, []).append(a.item.name)

    groups = [
        {"asks": list(asks), "names": names[:limit_names], "count": len(names)}
        for asks, names in sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    ]

    return {
        "universal": universal_questions(lexicon, profile),
        "groups": groups,
        "total": len(targets),
    }


def render_text(report: dict[str, Any]) -> str:
    """终端版问询清单。"""
    line = "─" * 56
    out: list[str] = ["去柜台时念这几句", line]

    if report["universal"]:
        out.append("先问（对所有餐品都成立，也最容易被漏掉）：")
        for q in report["universal"]:
            out.append(f"  · {q}")
        out.append("")

    if not report["groups"]:
        out.append("没有需要单独确认的餐品。")
        return "\n".join(out)

    out.append(f"再按这几句问（覆盖 {report['total']} 个官方查不到数据的餐品）：")
    for g in report["groups"]:
        out.append("")
        for q in g["asks"]:
            out.append(f"  · {q}")
        names = "、".join(g["names"])
        more = f" 等 {g['count']} 项" if g["count"] > len(g["names"]) else ""
        out.append(f"    ↳ 适用于：{names}{more}")
    return "\n".join(out)
