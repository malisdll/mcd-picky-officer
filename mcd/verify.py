"""本地算出金额后，和官方算价接口做交叉验证。

两条独立链路算出同一个数字，是这个项目最硬的一条正确性证据，
也是唯一能证明「我们真的调了官方接口、而不是自己编价」的东西。
"""
from __future__ import annotations

from dataclasses import dataclass

MATCH = "MATCH"
MISMATCH = "MISMATCH"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class VerifyResult:
    status: str
    local: int
    official: int | None
    delta: int = 0

    @property
    def ok(self) -> bool:
        return self.status == MATCH

    def describe(self) -> str:
        if self.status == UNAVAILABLE:
            return f"官方算价不可用，本地价格 {self.local} 分未经核验"
        if self.status == MATCH:
            return f"核价一致：{self.local} 分"
        return f"核价不一致：本地 {self.local} 分 / 官方 {self.official} 分（差 {self.delta} 分）"


def compare(local: int, official: int | None, tolerance: int = 0) -> VerifyResult:
    """tolerance 默认 0 —— 差一分钱也算不一致，宁可报错也不要悄悄抹平。"""
    if official is None:
        return VerifyResult(UNAVAILABLE, local, None, 0)
    delta = official - local
    status = MATCH if abs(delta) <= tolerance else MISMATCH
    return VerifyResult(status, local, official, delta)
