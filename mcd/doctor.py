"""MCP 能力体检。

解决的问题：在没有真实 Token 之前，「工具叫什么名字」「有没有配料字段」「算价接口要什么参数」
全部只能靠猜。猜错的代价是三天后才发现产品根基不存在。

所以这个模块的唯一职责是：用最小代价、零副作用地把真相问出来，并给出明确的红黄绿灯。

安全约束（硬编码，不可绕过）：
- 绝不调用任何下单/支付/领券/抽奖类工具，只读不写；
- 参数不确定时不盲调，改为输出 inputSchema 让人来判断。

2026-10-22 用真实 Token 实测后的两个修正：
1. 工具名是 `query-meals`，不是 `list-menu`。候选表里原先没有它，
   导致「工具明明存在、体检却报红灯」——诊断工具自己误诊是最难堪的 bug。
2. 菜单工具需要 storeCode，所以体检必须先自己查一家门店，不能空参调用。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


class _Client(Protocol):
    def initialize(self) -> dict[str, Any]: ...
    def list_tools(self) -> list[dict[str, Any]]: ...
    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...


# 有真实副作用的动词。宁严勿宽：
# 误杀一个只读工具的代价只是少探一条，误放一个写接口的代价是替用户下了单。
#
# 改这一行之前先看 35 个真实工具名的手工核对结果（tests/test_doctor.py::TestSafety）：
#   被拦：create-order / cancel-order / mall-create-order / party-order-create /
#         auto-bind-coupons / delivery-create-address / draw-lottery
#   放行：其余 28 个纯查询工具（含 query-order、query-lottery-info 这类只读的「可疑名字」）
MUTATING = ("create", "cancel", "bind", "draw", "claim", "receive", "refund",
            "submit", "delete", "pay", "redeem", "exchange")


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    candidates: tuple[str, ...]
    required: bool
    why: str


CAPABILITIES: tuple[Capability, ...] = (
    Capability("menu", "菜单 / 餐品列表",
               ("query-meals", "list-menu", "query-menu", "menu", "list-products"),
               True, "核心依赖，缺了整个产品不成立"),
    Capability("meal_detail", "套餐结构（拆三件套）",
               ("query-meal-detail", "meal-detail", "product-detail", "query-product-detail"),
               False, "能把套餐拆成单品，营养表命中率会明显提高"),
    Capability("nutrition", "配料 / 营养 / 过敏原",
               ("list-nutrition-foods", "nutrition", "list-nutrition", "ingredient"),
               True, "决定能不能做真正的忌口判定；缺失则只能靠名称描述做关键词推测"),
    Capability("price", "官方算价",
               ("calculate-price", "price-calculate", "calc-price", "query-price"),
               False, "用于交叉验证本地算价，是唯一能证明数据真实性的链路"),
    Capability("store", "门店查询",
               ("query-nearby-stores", "nearby-stores", "query-stores", "store-list"),
               False, "缺了可以用固定门店号或跳过"),
    Capability("coupon_mine", "我的券包",
               ("query-my-coupons", "my-coupons", "coupon-list"),
               False, "缺了方案不带优惠，功能仍可用"),
    Capability("coupon_store", "门店可用券",
               ("query-store-coupons", "available-coupons", "store-coupons"),
               False, "缺了无法校验券在该门店是否真的可用"),
    Capability("clock", "服务端时间",
               ("now-time-info", "now-time", "server-time"),
               False, "判断早餐时段与活动有效期，缺了用本地时间兜底"),
)

ALLERGEN_KEY_RE = re.compile(r"(allerg|配料|成分|ingredient|致敏|过敏)", re.I)

# 官方营养表的表头形如 `[160]{productName,nutritionDescription,...}:`
_TABLE_HEADER_RE = re.compile(r"\[(\d+)\]\{([^}]+)\}")


def _match(candidates: tuple[str, ...], names: list[str]) -> str | None:
    lowered = {n.lower(): n for n in names}
    for c in candidates:
        if c.lower() in lowered:
            return lowered[c.lower()]
    for c in candidates:
        for n in names:
            if c.lower() in n.lower():
                return n
    return None


def _is_forbidden(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in MUTATING)


def _collect_keys(payload: Any, depth: int = 0) -> set[str]:
    """捞出返回体里所有出现过的字段名，用于判断有没有配料类字段。"""
    keys: set[str] = set()
    if depth > 6:
        return keys
    if isinstance(payload, dict):
        for k, v in payload.items():
            keys.add(str(k))
            keys |= _collect_keys(v, depth + 1)
    elif isinstance(payload, list):
        for v in payload[:20]:
            keys |= _collect_keys(v, depth + 1)
    return keys


def _sample_rows(payload: Any, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if len(rows) >= limit or depth > 6:
            return
        if isinstance(node, list):
            for x in node:
                walk(x, depth + 1)
        elif isinstance(node, dict):
            name = node.get("name") or node.get("productName") or node.get("itemName")
            price = None
            for k in ("price", "currentPrice", "salePrice", "amount", "unitPrice"):
                if k in node:
                    price = node[k]
                    break
            if name and price is not None:
                rows.append(node)
            else:
                for v in node.values():
                    walk(v, depth + 1)

    walk(payload)
    return rows


def _structured(payload: Any) -> dict[str, Any]:
    """只取 structuredContent，避免把 JSON-RPC 外壳当成业务数据。"""
    if isinstance(payload, dict):
        sc = payload.get("structuredContent")
        if isinstance(sc, dict):
            return sc
        if "data" in payload or "success" in payload:
            return payload
    return {}


def _first_store(payload: Any) -> tuple[str, str] | None:
    data = _structured(payload).get("data")
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("storeCode"):
                return str(row["storeCode"]), str(row.get("storeName") or "")
    if isinstance(data, dict):
        for row in data.values():
            if isinstance(row, dict) and row.get("storeCode"):
                return str(row["storeCode"]), str(row.get("storeName") or "")
    return None


def _meal_entries(payload: Any) -> dict[str, dict[str, Any]]:
    meals = _structured(payload).get("data")
    if isinstance(meals, dict):
        meals = meals.get("meals")
    return meals if isinstance(meals, dict) else {}


def _nutrition_rows(payload: Any) -> tuple[int, list[str]]:
    """官方返回的是一张纯文本表，这里只数行数 + 读表头字段名。"""
    data = _structured(payload).get("data")
    if not isinstance(data, str):
        return 0, []
    m = _TABLE_HEADER_RE.search(data)
    if not m:
        return 0, []
    return int(m.group(1)), [c.strip() for c in m.group(2).split(",") if c.strip()]


def _cap(caps: list[dict[str, Any]], cid: str) -> dict[str, Any] | None:
    return next((c for c in caps if c["id"] == cid), None)


def _callable(cap: dict[str, Any] | None) -> bool:
    return bool(cap and cap["matched"] and not cap["forbidden"])


def diagnose(
    client: _Client,
    menu_args: dict[str, Any] | None = None,
    city: str = "北京市",
    keyword: str = "国贸",
) -> dict[str, Any]:
    report: dict[str, Any] = {"time": datetime.now().isoformat(timespec="seconds")}

    info = client.initialize()
    report["server"] = info.get("serverInfo") or info.get("server") or {}
    report["protocol"] = info.get("protocolVersion")

    tools = client.list_tools()
    names = [t.get("name", "") for t in tools if t.get("name")]
    report["tool_count"] = len(names)
    report["tools"] = names
    report["schemas"] = {t.get("name", ""): t.get("inputSchema", {}) for t in tools}

    caps: list[dict[str, Any]] = []
    for cap in CAPABILITIES:
        matched = _match(cap.candidates, names)
        caps.append({
            "id": cap.id, "label": cap.label, "matched": matched,
            "required": cap.required, "why": cap.why,
            "forbidden": _is_forbidden(matched) if matched else False,
        })
    report["capabilities"] = caps

    # ---- 第一步：查一家门店。菜单工具要 storeCode，不查就没法调 ----
    store_result: dict[str, Any] = {"called": None, "code": None, "name": None,
                                    "query": f"{city} · {keyword}", "error": None}
    store_cap = _cap(caps, "store")
    if _callable(store_cap):
        store_result["called"] = store_cap["matched"]
        try:
            res = client.call_tool(store_cap["matched"], {
                "beType": 1, "searchType": 2, "city": city, "keyword": keyword,
            })
            found = _first_store(res)
            if found:
                store_result["code"], store_result["name"] = found
            else:
                store_result["error"] = "调用成功但没解析出 storeCode（字段名可能不同）"
        except Exception as e:  # noqa: BLE001 - 诊断工具要吞掉异常并如实报告
            store_result["error"] = f"{type(e).__name__}: {e}"
    elif store_cap and store_cap["forbidden"]:
        store_result["error"] = f"匹配到写接口 {store_cap['matched']}，已拒绝调用"
    else:
        store_result["error"] = "MCP 没有门店查询工具，菜单工具需要 storeCode 时无法自举"
    report["store"] = store_result

    # ---- 第二步：用门店号调菜单 ----
    menu = _cap(caps, "menu")
    menu_payload: Any = None
    menu_result: dict[str, Any] = {"called": None, "ok": False, "items": 0, "args": {},
                                   "keys": [], "allergen_keys": [], "sample": [], "error": None}
    if _callable(menu):
        tool_name = menu["matched"]
        args = dict(menu_args or {})
        if not args and store_result["code"]:
            # 官方 query-meals 必填 storeCode / orderType / beType。能自举就自举。
            args = {"storeCode": store_result["code"], "orderType": 1, "beType": 1}
        menu_result["called"] = tool_name
        menu_result["args"] = args
        # 缺不缺参数交给 inputSchema 判断，不靠猜。没有 required 说明可以空参调用。
        schema = report["schemas"].get(tool_name, {}) or {}
        missing = [r for r in schema.get("required", []) if r and r not in args]
        if missing:
            menu_result["error"] = (
                f"缺少必填参数 {missing}，且无法自动补齐"
                f"（门店自举：{store_result['error'] or '未拿到 storeCode'}）。"
                "可用 --args '{\"storeCode\":\"...\"}' 手动传入"
            )
        else:
            try:
                payload = client.call_tool(tool_name, args)
                menu_payload = payload
                keys = _collect_keys(payload)
                rows = _sample_rows(payload)
                # 样例最多取 3 条，不能拿它当总数——那会把 110 个餐品报成 3 个。
                meals = _meal_entries(payload)
                total = len(meals) if meals else len(rows)
                menu_result["ok"] = total > 0
                menu_result["items"] = total
                menu_result["keys"] = sorted(keys)
                menu_result["allergen_keys"] = sorted(k for k in keys if ALLERGEN_KEY_RE.search(k))
                menu_result["sample"] = [
                    {"name": r.get("name") or r.get("productName"), "raw": {k: r[k] for k in list(r)[:8]}}
                    for r in rows[:3]
                ]
                if not rows:
                    menu_result["error"] = "有返回但没解析出餐品，字段名可能与预期不同（见 keys）"
            except Exception as e:  # noqa: BLE001
                menu_result["error"] = f"{type(e).__name__}: {e}"
    elif menu and menu["forbidden"]:
        menu_result["error"] = f"匹配到写接口 {menu['matched']}，已拒绝调用"
    else:
        menu_result["error"] = "没有找到菜单类工具"
    report["menu"] = menu_result

    # ---- 第三步：套餐详情。这是「能不能拆三件套」的关键 ----
    detail_result: dict[str, Any] = {"called": None, "ok": False, "keys": [],
                                     "allergen_keys": [], "error": None}
    detail_cap = _cap(caps, "meal_detail")
    codes = list(_meal_entries(menu_payload).keys()) if menu_result["ok"] else []
    if _callable(detail_cap):
        detail_result["called"] = detail_cap["matched"]
        if not codes:
            detail_result["error"] = "菜单没取到，无法挑一个 code 去试详情"
        else:
            try:
                args = dict(menu_result["args"])
                args["code"] = codes[0]
                res = client.call_tool(detail_cap["matched"], args)
                keys = _collect_keys(res)
                detail_result["ok"] = bool(keys)
                detail_result["keys"] = sorted(keys)
                detail_result["allergen_keys"] = sorted(k for k in keys if ALLERGEN_KEY_RE.search(k))
            except Exception as e:  # noqa: BLE001
                detail_result["error"] = f"{type(e).__name__}: {e}"
    report["meal_detail"] = detail_result

    # ---- 第四步：营养表。这是唯一真有一手数值的接口 ----
    nut_result: dict[str, Any] = {"called": None, "rows": 0, "fields": [],
                                  "allergen_keys": [], "error": None}
    nut_cap = _cap(caps, "nutrition")
    if _callable(nut_cap):
        nut_result["called"] = nut_cap["matched"]
        try:
            res = client.call_tool(nut_cap["matched"], {})
            rows, fields = _nutrition_rows(res)
            nut_result["rows"] = rows
            nut_result["fields"] = fields
            nut_result["allergen_keys"] = [f for f in fields if ALLERGEN_KEY_RE.search(f)]
            if not rows:
                nut_result["error"] = "返回体不是预期的文本表，字段名可能变了（见 keys）"
        except Exception as e:  # noqa: BLE001
            nut_result["error"] = f"{type(e).__name__}: {e}"
    report["nutrition"] = nut_result

    # 配料字段可能内嵌在菜单/详情返回里（没有单独的 nutrition 工具）。
    # 这种情况必须纠正能力矩阵，否则会出现「结论绿灯、矩阵却标红」的自相矛盾。
    embedded_in = None
    if menu_result["allergen_keys"]:
        embedded_in = menu_result["called"]
    elif detail_result["allergen_keys"]:
        embedded_in = detail_result["called"]
    if embedded_in:
        for c in caps:
            if c["id"] == "nutrition" and not c["matched"]:
                c["matched"] = f"{embedded_in}（内嵌字段）"
                c["embedded"] = True

    # ---- 算价工具：只读 schema，不盲调 ----
    price = _cap(caps, "price")
    price_info: dict[str, Any] = {"tool": None, "required": [], "properties": []}
    if _callable(price):
        schema = report["schemas"].get(price["matched"], {}) or {}
        price_info["tool"] = price["matched"]
        price_info["required"] = schema.get("required", [])
        price_info["properties"] = list((schema.get("properties") or {}).keys())
    report["price"] = price_info

    report["verdict"] = _verdict(report)
    return report


def _verdict(r: dict[str, Any]) -> dict[str, Any]:
    caps = {c["id"]: c for c in r["capabilities"]}
    menu = r["menu"]
    nut = r["nutrition"]
    detail = r["meal_detail"]
    actions: list[str] = []

    if not caps["menu"]["matched"]:
        return {"level": "red", "headline": "找不到菜单工具，产品无法成立",
                "actions": ["运行 `python run.py tools` 看完整列表，把菜单相关工具名补进 mcd/doctor.py 的候选列表"]}

    if not menu["ok"]:
        return {"level": "red", "headline": "菜单工具存在，但调用没拿到餐品",
                "actions": [
                    f"工具名：{menu['called']}",
                    f"错误：{menu['error']}",
                    "如果是缺参数，请看 tools 输出里的 inputSchema，用 --args 传入（例如 storeCode）",
                ]}

    allergen_fields = sorted(set(menu["allergen_keys"]) | set(nut["allergen_keys"])
                             | set(detail["allergen_keys"]))

    if allergen_fields:
        level = "green"
        headline = "配料/过敏原字段存在 —— 可以做真正的三态判定"
        actions.append(f"检出字段：{', '.join(allergen_fields[:6])}")
        actions.append("下一步：把字段映射补进 mcd/live.py 的 fetch()")
    elif nut["rows"]:
        level = "yellow"
        headline = f"无配料表，但有 {nut['rows']} 条官方营养数据（钠/热量可用）"
        actions.append("能做：精确的控钠 / 控热量 / 高蛋白筛选，数据来自官方，可交叉核验")
        actions.append("不能做：配料级过敏原判定。过敏只能降级为「菜名关键词提示」，且必须标为推断")
        actions.append("叙事调整：主打「哪些查得到、哪些查不到」，把诚实当卖点")
    else:
        level = "yellow"
        headline = "菜单能取到，但没有配料/营养字段"
        actions.append("当前能力：只能靠菜名与描述做关键词推测，命中即「含」，无线索即「信息不足」")
        actions.append("叙事调整：主打「帮你看清哪些查不到」，把诚实本身当卖点，产品仍成立")
        actions.append("可选：看 sample 里的原始字段，若字段名不常见，把它加进 ALLERGEN_KEY_RE")

    if caps["price"]["matched"]:
        if r["price"]["required"]:
            actions.append(f"算价工具 {r['price']['tool']} 必填参数：{', '.join(r['price']['required'])}")
        actions.append("拿到参数后跑一次核价，把结果存进 docs/ 作为真实性证据")
    else:
        actions.append("没有找到算价工具，核价链路需降级为「未经核验」并如实标注")

    if r["store"]["error"]:
        actions.append(f"门店自举失败：{r['store']['error']}")

    return {"level": level, "headline": headline, "actions": actions}


LEVEL_LABEL = {"green": "✅ 绿灯", "yellow": "⚠️ 黄灯", "red": "❌ 红灯"}


def render_text(r: dict[str, Any], token_masked: str = "") -> str:
    out: list[str] = []
    line = "─" * 62
    out.append("")
    out.append("MCP 能力体检报告")
    out.append(line)
    out.append(f"时间        {r['time']}")
    out.append(f"Token       {token_masked}")
    out.append(f"服务端      {r['server']}")
    out.append(f"协议版本    {r.get('protocol')}")
    out.append(f"工具总数    {r['tool_count']}")
    out.append(line)
    out.append("能力矩阵")
    for c in r["capabilities"]:
        mark = "✅" if c["matched"] else ("❌" if c["required"] else "—")
        name = c["matched"] or "未找到"
        need = "必需" if c["required"] else "可选"
        out.append(f"  {mark} {c['label']:<18} {name:<28} [{need}]")
        if not c["matched"] and c["required"]:
            out.append(f"       └ {c['why']}")
    out.append(line)

    s = r["store"]
    out.append("门店自举")
    if s["called"]:
        out.append(f"  工具        {s['called']}")
        out.append(f"  查询        {s['query']}")
        out.append(f"  结果        {s['name'] or '未取到'}（{s['code'] or '无 storeCode'}）")
        if s["error"]:
            out.append(f"  错误        {s['error']}")
    else:
        out.append(f"  未找到可用门店工具   {s['error'] or ''}")
    out.append(line)

    m = r["menu"]
    out.append("菜单探测")
    if m["called"]:
        out.append(f"  工具        {m['called']}")
        out.append(f"  参数        {json.dumps(m['args'], ensure_ascii=False)}")
        out.append(f"  结果        {'成功' if m['ok'] else '失败'}  解析出 {m['items']} 个餐品")
        if m["error"]:
            out.append(f"  错误        {m['error']}")
        if m["allergen_keys"]:
            out.append(f"  配料字段    {', '.join(m['allergen_keys'])}")
        for smp in m["sample"]:
            out.append(f"  样例        {json.dumps(smp['raw'], ensure_ascii=False)[:110]}")
    else:
        out.append(f"  {m['error']}")
    out.append(line)

    d = r["meal_detail"]
    out.append("套餐详情探测")
    if d["called"]:
        out.append(f"  工具        {d['called']}")
        out.append(f"  结果        {'成功' if d['ok'] else '失败'}")
        if d["error"]:
            out.append(f"  错误        {d['error']}")
        if d["keys"]:
            out.append(f"  字段        {', '.join(d['keys'][:12])}")
    else:
        out.append("  没有套餐详情工具")
    out.append(line)

    n = r["nutrition"]
    out.append("营养 / 配料探测")
    if n["called"]:
        out.append(f"  工具        {n['called']}")
        out.append(f"  结果        {n['rows']} 条记录")
        if n["fields"]:
            out.append(f"  字段        {', '.join(n['fields'])}")
        if n["error"]:
            out.append(f"  错误        {n['error']}")
    else:
        out.append("  没有营养类工具")
    out.append(line)

    p = r["price"]
    out.append("算价探测")
    if p["tool"]:
        out.append(f"  工具        {p['tool']}")
        out.append(f"  必填参数    {', '.join(p['required']) or '（无）'}")
        out.append(f"  可选参数    {', '.join(p['properties'][:8]) or '（无）'}")
    else:
        out.append("  未找到算价工具")
    out.append(line)

    v = r["verdict"]
    out.append(f"结论  {LEVEL_LABEL.get(v['level'], v['level'])}  {v['headline']}")
    for a in v["actions"]:
        out.append(f"  → {a}")
    out.append(line)
    return "\n".join(out)


def save_snapshot(r: dict[str, Any], path: str) -> str:
    """把体检结果存成 JSON。这是「真的接过真实接口」最直接、最不可伪造的证据。"""
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    return path
