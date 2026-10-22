"""在「能吃的」集合里，按预算搭配出方案。

目标函数刻意很简单：先满足结构（有主食），再在预算内把热量拉满。
不做花哨的多目标优化 —— 用户要的是一个能立刻下单的答案，不是帕累托前沿。
"""
from __future__ import annotations

from .models import Assessment, Coupon, Item, Plan, Profile, Verdict
from .pricing import best_coupon, closest_threshold, yuan


def allowed_items(assessments: list[Assessment], profile: Profile) -> list[Item]:
    """放行规则：

    - HIT（明确含）—— 一律排除，没有商量
    - RISK（可能含）—— 只有用户显式同意才放行
    - NO_INFO（官方无数据）—— 默认也排除，只在清单里单独列出等用户确认
    - CLEAR —— 放行
    """
    out: list[Item] = []
    for a in assessments:
        if a.verdict == Verdict.CLEAR:
            out.append(a.item)
        elif a.verdict == Verdict.RISK and profile.allow_risk:
            out.append(a.item)
    return out


def _apply(subtotal: int, coupons: list[Coupon], notes: list[str]) -> tuple[Coupon | None, int]:
    coupon, discount = best_coupon(subtotal, coupons)
    if coupon:
        notes.append(f"已命中「{coupon.name}」，省 {yuan(discount)} 元")
    else:
        near = closest_threshold(subtotal, coupons)
        if near:
            c, gap = near
            notes.append(f"再点 {yuan(gap)} 元可触发「{c.name}」（满 {yuan(c.threshold)} 减 {yuan(c.discount)}）")
    return coupon, discount


def _build(label: str, items: list[Item], coupons: list[Coupon], notes: list[str]) -> Plan:
    subtotal = sum(i.price for i in items)
    local_notes = list(notes)
    coupon, discount = _apply(subtotal, coupons, local_notes)
    return Plan(label=label, items=items, subtotal=subtotal,
                coupon=coupon, discount=discount, notes=local_notes)


def build_plans(assessments: list[Assessment], profile: Profile,
                coupons: list[Coupon]) -> list[Plan]:
    pool = allowed_items(assessments, profile)

    mains = [i for i in pool if i.category == "main"]
    drinks = sorted((i for i in pool if i.category == "drink"), key=lambda i: i.price)
    sides = sorted((i for i in pool if i.category == "side"), key=lambda i: i.price)
    desserts = sorted((i for i in pool if i.category == "dessert"), key=lambda i: i.price)

    if not mains:
        return [Plan(
            label="没有可选主食",
            items=[],
            subtotal=0,
            notes=[
                "在当前忌口条件下，没有找到可以确定放行的主食。",
                "两类可能：主食确实都含有你要避开的东西，或者官方未提供配料信息、无法判断。",
                "建议：查看「信息不足」清单，向门店确认后，用 --risk 参数重试。",
            ],
        )]

    budget = profile.budget

    # 「最省一单」要求主食 + 饮品（有饮品时），代表一份完整的餐；
    # 而「吃得饱」允许单点，因为真实场景里 24.5 元的巨无霸单点往往比
    # 硬凑一份「汉堡 + 最便宜饮料」更划算 —— 这是最容易写错的一处逻辑。
    meal_drinks: list[Item | None] = list(drinks) or [None]
    any_drinks: list[Item | None] = [None] + list(drinks)

    plans: list[Plan] = []
    seen: set[tuple[str, ...]] = set()

    def add(plan: Plan) -> None:
        key = tuple(sorted(i.id for i in plan.items)) or (plan.label,)
        if key not in seen:
            seen.add(key)
            plans.append(plan)

    meal_pairs = [(m, d) for m in mains for d in meal_drinks]
    cheapest = min(meal_pairs, key=lambda p: p[0].price + (p[1].price if p[1] else 0))
    cheapest_items = [i for i in cheapest if i]
    cheapest_cost = sum(i.price for i in cheapest_items)

    # ① 最省：一份完整但最便宜的餐（仅在预算允许时才算「方案」）
    if budget is None or cheapest_cost <= budget:
        add(_build("最省一单", cheapest_items, coupons, []))

    # ② 吃得饱：预算内热量最大的组合（允许单点，可加配餐）
    side_options: list[Item | None] = [None] + list(sides)
    feasible: list[list[Item]] = []
    for m in mains:
        for d in any_drinks:
            base = [i for i in (m, d) if i]
            for s in side_options:
                combo = base + ([s] if s else [])
                total = sum(i.price for i in combo)
                if budget is None or total <= budget:
                    feasible.append(combo)

    if not feasible:
        gap = max(0, cheapest_cost - (budget or 0))
        add(Plan(
            label="预算不够",
            items=[],
            subtotal=cheapest_cost,
            notes=[
                f"最便宜的可吃组合需要 {yuan(cheapest_cost)} 元，超出人均预算 {yuan(gap)} 元。",
                "没有生成方案，是因为把超预算的选项硬塞给你，比说「不行」更糟。",
            ],
        ))
    else:
        full = max(feasible, key=lambda c: (sum(i.kcal for i in c), -sum(i.price for i in c)))
        notes = []
        if budget is not None:
            left = budget - sum(i.price for i in full)
            notes.append(
                f"人均预算 {yuan(budget)} 元，本方案 {yuan(sum(i.price for i in full))} 元，剩余 {yuan(left)} 元"
            )
        if not any(i.category == "drink" for i in full) and drinks:
            notes.append("这份没配饮品：在同样预算下，它的总热量比「汉堡 + 饮料」更高")
        add(_build("吃得饱", full, coupons, notes))

        # ③ 加点甜的：在预算内再加一个甜品
        for d in desserts:
            cand = full + [d]
            if budget is None or sum(i.price for i in cand) <= budget:
                add(_build("加个甜的", cand, coupons, ["在「吃得饱」的基础上加了一份甜品"]))
                break

    # 数据缺失必须显式告知，否则「没推荐」会被误读成「不能吃」
    noinfo_count = sum(1 for a in assessments if a.verdict == Verdict.NO_INFO)
    if noinfo_count:
        for p in plans:
            p.notes.append(
                f"另有 {noinfo_count} 个餐品因官方未提供配料信息，未纳入方案（见「信息不足」清单）"
            )

    return plans
