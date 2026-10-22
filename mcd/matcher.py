"""把「餐品」和「忌口档案」对撞，产出三态判定 + 可解释理由。

判定优先级：HIT（明确含） > RISK（可能含） > NO_INFO（无数据） > CLEAR（未见疑点）

关键设计：「无数据」不等于「安全」。当官方没提供配料信息时，我们输出 NO_INFO
而不是默认放行，并且在界面上把它和「未见疑点」明确区分开。
"""
from __future__ import annotations

from typing import Any

from .models import Assessment, Item, Profile, Verdict


class Matcher:
    def __init__(self, lexicon: dict[str, Any]) -> None:
        self.lexicon = lexicon
        self._allergens = {e["id"]: e for e in lexicon.get("allergens", [])}
        self._avoid = {e["id"]: e for e in lexicon.get("avoid", [])}
        self._diets = {e["id"]: e for e in lexicon.get("diet", [])}

    # ---- 对外 ----
    def allergen_label(self, aid: str) -> str:
        return self._allergens.get(aid, {}).get("label", aid)

    def avoid_label(self, vid: str) -> str:
        return self._avoid.get(vid, {}).get("label", vid)

    def diet_label(self, did: str) -> str:
        return self._diets.get(did, {}).get("label", did)

    def assess(self, item: Item, profile: Profile) -> Assessment:
        verdict = Verdict.CLEAR
        reasons: list[str] = []

        def bump(v: Verdict, reason: str) -> None:
            """只保留「最严重那一档」的理由。

            否则会出现这样的误导：一个餐品因为「油炸」被排除，
            理由却显示成「官方未提供配料信息」——用户会以为是数据缺失导致的。
            """
            nonlocal verdict
            if v > verdict:
                verdict = v
                reasons.clear()
                if reason:
                    reasons.append(reason)
            elif v == verdict and reason and reason not in reasons:
                reasons.append(reason)

        text = item.searchable

        # 1) 过敏原
        for aid in profile.allergens:
            label = self.allergen_label(aid)
            entry = self._allergens.get(aid, {})

            if item.allergens is not None and aid in item.allergens:
                bump(Verdict.HIT, f"官方配料标注含「{label}」")
                continue

            hit_kw = self._first_kw(text, entry.get("hit", []))
            if hit_kw:
                bump(Verdict.HIT, f"名称/描述出现「{hit_kw}」，与{label}相关")
                continue

            # 名称推断出的线索：只够支撑「可能含有，需确认」，不能断言「含」
            hint = item.hints.get(aid)
            if hint:
                bump(Verdict.RISK, f"名称含「{hint}」，可能涉及{label}，需向门店确认")
                continue

            risk_kw = self._first_kw(text, entry.get("risk", []))
            if risk_kw:
                bump(Verdict.RISK, f"描述出现「{risk_kw}」，可能涉及{label}")
                continue

            if item.allergens is None:
                bump(Verdict.NO_INFO, f"官方未提供配料信息，无法确认是否含{label}")

        # 2) 口味 / 食材禁忌
        for vid in profile.avoid:
            label = self.avoid_label(vid)
            entry = self._avoid.get(vid, {})

            if vid == "spicy":
                if item.spicy >= 2:
                    bump(Verdict.HIT, f"辣度标记为 {item.spicy}/3")
                elif item.spicy == 1:
                    bump(Verdict.RISK, "轻微辣度，建议确认")
                continue

            if vid == "fried" and item.fried:
                bump(Verdict.HIT, "制作方式为油炸")
                continue

            if vid == "caffeine" and item.caffeine:
                bump(Verdict.HIT, "含咖啡因")
                continue

            if vid == item.meat:
                bump(Verdict.HIT, f"主料为{label}")
                continue

            hit_kw = self._first_kw(text, entry.get("hit", []))
            if hit_kw:
                bump(Verdict.HIT, f"名称/描述出现「{hit_kw}」")
                continue

            risk_kw = self._first_kw(text, entry.get("risk", []))
            if risk_kw:
                bump(Verdict.RISK, f"描述出现「{risk_kw}」，需确认是否含{label}")

        # 3) 饮食类型
        for did in profile.diets:
            entry = self._diets.get(did, {})
            label = self.diet_label(did)
            forbidden_meat = entry.get("forbidden_meat", [])
            if item.meat and item.meat in forbidden_meat:
                bump(Verdict.HIT, f"{label}：该餐品主料为「{item.meat}」")
            for fa in entry.get("forbidden_allergens", []):
                if item.allergens is not None and fa in item.allergens:
                    bump(Verdict.HIT, f"{label}：含「{self.allergen_label(fa)}」")
                elif item.allergens is None:
                    bump(Verdict.NO_INFO, f"{label}：官方未提供配料信息")

        # 4) 营养约束（官方营养表的精确值，是唯一「硬」的数据）
        if profile.max_sodium is not None:
            if item.sodium is None:
                bump(Verdict.NO_INFO, "官方营养表没有这个餐品，无法核对钠含量")
            elif item.sodium > profile.max_sodium:
                bump(Verdict.HIT, f"钠 {item.sodium}mg，超过上限 {profile.max_sodium}mg")
            elif item.sodium > profile.max_sodium * 0.8:
                bump(Verdict.RISK, f"钠 {item.sodium}mg，接近上限 {profile.max_sodium}mg")

        if profile.max_kcal is not None:
            # 「热量未知」不等于「0 千卡」，用 has_nutrition 判断，不看 kcal 的真假值。
            if not item.has_nutrition:
                bump(Verdict.NO_INFO, "官方营养表没有这个餐品，无法核对热量")
            elif item.kcal > profile.max_kcal:
                bump(Verdict.HIT, f"热量 {item.kcal}kcal，超过上限 {profile.max_kcal}kcal")

        if profile.min_protein is not None:
            if item.protein is None:
                bump(Verdict.NO_INFO, "官方营养表没有这个餐品，无法核对蛋白质")
            elif item.protein < profile.min_protein:
                bump(Verdict.HIT, f"蛋白质 {item.protein}g，低于目标 {profile.min_protein}g")

        if verdict == Verdict.CLEAR and not reasons:
            reasons.append("未命中你设置的忌口项")

        return Assessment(item=item, verdict=verdict, reasons=reasons)

    def assess_all(self, items: list[Item], profile: Profile) -> list[Assessment]:
        order = {"main": 0, "side": 1, "drink": 2, "dessert": 3}
        result = [self.assess(i, profile) for i in items]
        result.sort(key=lambda a: (-int(a.verdict), order.get(a.item.category, 9), a.item.price))
        return result

    # ---- 内部 ----
    @staticmethod
    def _first_kw(text: str, keywords: list[str]) -> str | None:
        for kw in keywords:
            if kw and kw in text:
                return kw
        return None
