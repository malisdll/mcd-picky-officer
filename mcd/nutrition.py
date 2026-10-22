"""解析麦当劳营养表。

官方 `list-nutrition-foods` 返回的是一段**带 schema 声明的纯文本表格**，不是 JSON 数组：

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213

这份数据是官方精确值（钠、热量、蛋白等），是本项目唯一「硬」的数据来源，
所以解析要严格：宁可少一条，也不要把列错位。
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

_HEADER_RE = re.compile(r"\[(\d+)\]\{([^}]+)\}:?\s*$")


@dataclass(frozen=True)
class Nutrition:
    name: str
    kcal: int | None
    protein: float | None
    fat: float | None
    carb: float | None
    sodium: int | None       # mg
    calcium: int | None      # mg

    @property
    def kcal_per_100(self) -> float | None:
        return float(self.kcal) if self.kcal is not None else None


def _num(v: str, cast=int):
    v = (v or "").strip()
    if not v or v.lower() == "null":
        return None
    try:
        return cast(float(v)) if cast is int else cast(v)
    except ValueError:
        return None


def parse_table(raw: str) -> list[Nutrition]:
    """把官方文本表解析成结构化记录。列数不符的行直接跳过。"""
    if not raw or not isinstance(raw, str):
        return []

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return []

    m = _HEADER_RE.search(lines[0])
    expected = None
    if m:
        expected = [c.strip() for c in m.group(2).split(",")]
        lines = lines[1:]
    if not expected:
        return []

    idx = {name: i for i, name in enumerate(expected)}
    need = ("productName", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium")
    if any(k not in idx for k in need):
        return []

    out: list[Nutrition] = []
    for line in lines:
        row = next(csv.reader(io.StringIO(line)))
        if len(row) < len(expected):
            continue
        name = row[idx["productName"]].strip()
        if not name:
            continue
        out.append(Nutrition(
            name=name,
            kcal=_num(row[idx["energyKcal"]]),
            protein=_num(row[idx["protein"]], float),
            fat=_num(row[idx["fat"]], float),
            carb=_num(row[idx["carbohydrate"]], float),
            sodium=_num(row[idx["sodium"]]),
            calcium=_num(row[idx["calcium"]]),
        ))
    return out


def build_index(records: list[Nutrition]) -> dict[str, Nutrition]:
    """按名称建索引。同名保留第一条（官方表里偶有重复）。"""
    index: dict[str, Nutrition] = {}
    for r in records:
        index.setdefault(r.name, r)
    return index


def lookup(index: dict[str, Nutrition], name: str) -> Nutrition | None:
    """只做精确匹配。

    不做模糊匹配，是因为「麦辣鸡腿汉堡三件套」不等于「麦辣鸡腿汉堡」——
    套餐的热量不是单品热量，凑上去就是在编数据。
    """
    return index.get(name.strip())
