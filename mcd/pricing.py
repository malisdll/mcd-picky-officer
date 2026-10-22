"""金额与优惠计算。全部使用「分」，不出现 float。"""
from __future__ import annotations

from .models import Coupon


def yuan(fen: int) -> str:
    """分 → 人类可读的元，保留两位小数。"""
    sign = "-" if fen < 0 else ""
    fen = abs(int(fen))
    return f"{sign}{fen // 100}.{fen % 100:02d}"


def best_coupon(subtotal: int, coupons: list[Coupon]) -> tuple[Coupon | None, int]:
    """在可用券里挑出「实际减得最多」的一张。

    简单线性扫描 —— 券的数量是个位数，没必要上优化算法。
    真实场景里券通常是互斥的（不能叠加），所以只返回一张。
    """
    best: Coupon | None = None
    best_discount = 0
    for c in coupons:
        if subtotal < c.threshold:
            continue
        # 防止「减完变负数」这种脏数据
        d = min(c.discount, subtotal)
        if d > best_discount:
            best, best_discount = c, d
    return best, best_discount


def closest_threshold(subtotal: int, coupons: list[Coupon]) -> tuple[Coupon, int] | None:
    """如果还不够用券，告诉用户「再差多少能减多少」。"""
    candidates = [c for c in coupons if subtotal < c.threshold]
    if not candidates:
        return None
    c = min(candidates, key=lambda x: x.threshold - subtotal)
    return c, c.threshold - subtotal


def split_evenly(total: int, people: int) -> list[int]:
    """均摊到人，余数从前往后各补 1 分，保证 sum(result) == total。"""
    if people <= 0:
        raise ValueError("people must be >= 1")
    base, rem = divmod(total, people)
    return [base + (1 if i < rem else 0) for i in range(people)]
