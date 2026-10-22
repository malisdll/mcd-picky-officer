"""把一句中文口语，解析成结构化的忌口档案。

刻意做成「规则 + 词典」而不是调用大模型：
1. 可离线单元测试，CI 不需要 Token；
2. 结果可复现，同一句话永远得到同一个档案；
3. 解析错的时候，用户能看懂错在哪一条规则上。

大模型负责把用户的话说圆，结构化这一步交给确定性代码。
"""
from __future__ import annotations

import re
from typing import Any

from .models import Profile

# 「过敏」类线索：命中后按过敏原处理（严格排除 + 高风险提示）
ALLERGY_CUES = ("过敏", "不耐受", "不耐", "不能碰", "会起疹", "会肿", "休克")
# 「就是不想吃」类线索：按个人偏好处理
AVOID_CUES = ("不吃", "不喝", "不要", "别放", "不要放", "去掉", "不加", "讨厌", "不喜欢", "拒绝", "忌口")

DIET_CUES = {
    "vegan": ("纯素", "全素", "vegans", "不吃任何动物"),
    "vegetarian": ("素食", "吃素", "斋"),
    "halal": ("清真", "halal"),
}

SPICE_OK_CUES = ("微辣可以", "能吃点辣", "可以辣", "吃辣没问题", "辣一点也行")

CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_SPLIT_RE = re.compile(r"[，,。；;、\n]|(?:然后|另外|还有|以及)")


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in _SPLIT_RE.split(text) if c and c.strip()]


def _match_ids(clause: str, lexicon: dict[str, Any], group: str) -> list[str]:
    """在某个词典分组里，找出该分句命中的条目 id。"""
    hits: list[str] = []
    for entry in lexicon.get(group, []):
        kws = list(entry.get("hit", []))
        # 标签本身也参与匹配，例如「乳制品」「甲壳类海鲜」
        for label in re.split(r"[/（）()]", entry.get("label", "")):
            label = label.strip()
            if len(label) >= 2:
                kws.append(label)
        if any(kw and kw in clause for kw in kws):
            hits.append(entry["id"])
    return hits


def _parse_budget(text: str) -> int | None:
    """抽出人均预算，返回「分」。"""
    m = re.search(r"(?:预算|人均|每人|一个人)\D{0,6}?(\d+(?:\.\d+)?)\s*(?:元|块|rmb)?", text)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:以内|以下|封顶|左右|够)?", text)
    if not m:
        return None
    try:
        yuan = float(m.group(1))
    except ValueError:
        return None
    if yuan <= 0 or yuan > 5000:
        return None
    return int(round(yuan * 100))


def _parse_headcount(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:个)?人", text)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 50 else None
    for cn, val in CN_NUM.items():
        if f"{cn}个人" in text or f"{cn}人" in text:
            return val
    return None


def _parse_nutrients(text: str) -> tuple[int | None, int | None, float | None, list[str]]:
    """解析营养目标。返回 (max_sodium, max_kcal, min_protein, trace)。"""
    trace: list[str] = []
    max_sodium = max_kcal = None
    min_protein = None

    m = re.search(r"(?:钠|盐分?)\D{0,5}?(\d{2,4})\s*(?:mg|毫克)", text, re.I)
    if m:
        max_sodium = int(m.group(1))
        trace.append(f"钠上限：{max_sodium}mg")
    elif any(k in text for k in ("控钠", "低钠", "少盐", "减盐", "低盐")):
        max_sodium = 600
        trace.append("识别到控钠需求 → 默认钠上限 600mg/份")

    m = re.search(r"(?:热量|卡路里|卡)\D{0,5}?(\d{2,4})\s*(?:kcal|大卡|千卡)", text, re.I)
    if m:
        max_kcal = int(m.group(1))
        trace.append(f"热量上限：{max_kcal}kcal")
    elif any(k in text for k in ("低卡", "控卡", "少热量", "低热量", "减脂")):
        max_kcal = 500
        trace.append("识别到低卡需求 → 默认热量上限 500kcal/份")

    m = re.search(r"蛋白\D{0,5}?(\d{1,3})\s*(?:g|克)", text, re.I)
    if m:
        min_protein = float(m.group(1))
        trace.append(f"蛋白质目标：≥ {min_protein}g")
    elif any(k in text for k in ("高蛋白", "多蛋白", "补蛋白")):
        min_protein = 25
        trace.append("识别到高蛋白需求 → 默认蛋白质目标 25g/份")

    return max_sodium, max_kcal, min_protein, trace


def parse_profile(text: str, lexicon: dict[str, Any]) -> tuple[Profile, list[str]]:
    """解析自然语言 → (Profile, 解析轨迹)。

    轨迹会原样展示给用户：「我理解成花生过敏 + 不吃辣 + 人均 25 元」，
    让用户有机会在点单前纠正。猜错比不猜更危险，所以要让它可被检查。
    """
    trace: list[str] = []
    allergens: list[str] = []
    avoid: list[str] = []
    diets: list[str] = []
    spice_ok = any(c in text for c in SPICE_OK_CUES)

    for clause in _clauses(text):
        is_allergy = any(c in clause for c in ALLERGY_CUES)
        is_avoid = any(c in clause for c in AVOID_CUES)

        # 1) 特殊饮食（可能同时出现在「不吃」句里，例如「不吃肉，我吃素」）
        for diet_id, cues in DIET_CUES.items():
            if any(c in clause for c in cues):
                if diet_id not in diets:
                    diets.append(diet_id)
                    trace.append(f"识别到饮食类型：{diet_id}（来自「{clause}」）")

        # 2) 过敏原
        if is_allergy or (is_avoid and not is_allergy):
            for aid in _match_ids(clause, lexicon, "allergens"):
                if aid in allergens:
                    continue
                # 「不吃花生」也算严格排除，按过敏原处理更保守
                allergens.append(aid)
                reason = "过敏/不耐受" if is_allergy else "主动避开"
                trace.append(f"排除过敏原：{aid}（{reason}，来自「{clause}」）")

        # 3) 口味/食材禁忌
        if is_avoid or is_allergy:
            for vid in _match_ids(clause, lexicon, "avoid"):
                if vid == "spicy" and spice_ok:
                    continue
                if vid in avoid:
                    continue
                avoid.append(vid)
                trace.append(f"排除：{vid}（来自「{clause}」）")

    budget = _parse_budget(text)
    if budget:
        trace.append(f"人均预算：{budget / 100:.2f} 元")
    headcount = _parse_headcount(text) or 1
    if headcount > 1:
        trace.append(f"人数：{headcount}")

    max_sodium, max_kcal, min_protein, nut_trace = _parse_nutrients(text)
    trace.extend(nut_trace)

    if not trace:
        trace.append("没有从这句话里识别出忌口信息，按「无限制」处理")

    return (
        Profile(
            allergens=tuple(dict.fromkeys(allergens)),
            avoid=tuple(dict.fromkeys(avoid)),
            diets=tuple(dict.fromkeys(diets)),
            budget=budget,
            headcount=headcount,
            max_sodium=max_sodium,
            max_kcal=max_kcal,
            min_protein=min_protein,
        ),
        trace,
    )
