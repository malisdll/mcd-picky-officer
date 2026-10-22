#!/usr/bin/env python3
"""麦麦挑食官 · 命令行入口。

零第三方依赖，所以「三行命令跑起来」是真的：

    git clone <repo> && cd mcd-picky-officer
    python run.py ask "我花生过敏，不吃辣，人均25"
    # 想生成可以直接发群里的清单卡：
    python run.py ask "我花生过敏，不吃辣，人均25" --html card.html

默认使用仓库内的示例数据（离线可用、不消耗 Token）。
接真实数据加 --online，并先设置环境变量 MCD_MCP_TOKEN。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd import __version__, store  # noqa: E402
from mcd.matcher import Matcher  # noqa: E402
from mcd.models import Profile, Verdict  # noqa: E402
from mcd.planner import build_plans  # noqa: E402
from mcd.pricing import yuan  # noqa: E402
from mcd.profile import parse_profile  # noqa: E402

LINE = "─" * 56


def _header() -> None:
    print(f"\n麦麦挑食官 v{__version__}  ·  先确认你能吃什么，再谈吃什么")
    print(LINE)


def _print_profile(profile: Profile, trace: list[str], matcher: Matcher) -> None:
    parts = []
    for a in profile.allergens:
        parts.append(f"过敏[{matcher.allergen_label(a)}]")
    for v in profile.avoid:
        parts.append(f"不吃[{matcher.avoid_label(v)}]")
    for d in profile.diets:
        parts.append(matcher.diet_label(d))
    if profile.budget:
        parts.append(f"人均[{yuan(profile.budget)} 元]")
    if profile.max_sodium:
        parts.append(f"钠≤{profile.max_sodium}mg")
    if profile.max_kcal:
        parts.append(f"热量≤{profile.max_kcal}kcal")
    if profile.min_protein:
        parts.append(f"蛋白≥{profile.min_protein:g}g")
    print("我理解成：" + ("、".join(parts) if parts else "无限制"))
    for t in trace:
        print(f"  · {t}")
    print(LINE)


def _print_plans(plans) -> None:
    if not plans:
        print("没有生成方案。")
        return
    for idx, p in enumerate(plans, 1):
        if p.items:
            total = f"{yuan(p.total)} 元"
            if p.discount:
                total += f"（原价 {yuan(p.subtotal)}，省 {yuan(p.discount)}）"
            print(f"{idx}) {p.label}   {total}")
            for i in p.items:
                print(f"     - {i.name}  {yuan(i.price)} 元")
        else:
            print(f"{idx}) {p.label}")
        for n in p.notes:
            print(f"     · {n}")
    print(LINE)


def _print_verdicts(assessments, limit_per_group: int = 4) -> None:
    # 分组标题必须和判定理由对得上：被钠含量挡下的餐品不是「命中忌口」，
    # 没有营养数据的餐品也不是「官方无配料数据」。文不对题会让人怀疑整套判定。
    groups = [
        (Verdict.HIT, "已排除（明确不符合你的要求）"),
        (Verdict.RISK, "可能含有 / 踩线（建议问柜台）"),
        (Verdict.NO_INFO, "信息不足（官方查不到对应数据）"),
        (Verdict.CLEAR, "未见疑点"),
    ]
    for verdict, title in groups:
        rows = [a for a in assessments if a.verdict == verdict]
        print(f"{title}：{len(rows)} 项")
        for a in rows[:limit_per_group]:
            reason = a.reasons[0] if a.reasons else ""
            print(f"  - {a.item.name:<16} {yuan(a.item.price):>7} 元   {reason}")
        if len(rows) > limit_per_group:
            print(f"  … 还有 {len(rows) - limit_per_group} 项")
    print(LINE)


def _load_menu(online: bool, city: str | None = None, keyword: str | None = None):
    """返回 (items, coupons, source_label, verify_text, store_label)。

    store_label 单独返回，是为了让清单卡上写的门店名和实际查的门店一致 ——
    否则会出现「数据来自建外 SOHO，卡片却写示例门店」这种自相矛盾。
    """
    if not online:
        return (store.sample_items(), store.sample_coupons(),
                "示例数据（离线演示，非实时）", None, "示例门店（模拟）")
    from mcd.live import LiveError, fetch
    from mcd.mcp_client import McdClient, McpError

    city = city or "北京市"
    keyword = keyword or "国贸"
    if not (city and keyword):
        print("⚠ 在线模式需要 --city 与 --keyword 同时提供（官方要求）")
        return (store.sample_items(), store.sample_coupons(), "示例数据（参数不全）", None,
                "示例门店（模拟）")

    try:
        client = McdClient()
        client.initialize()
        data = fetch(client, city, keyword)
    except (McpError, LiveError) as e:
        print(f"⚠ 在线模式失败：{e}")
        print("  已回退到示例数据 —— 流程能跑通，但价格与餐品不是真实的。\n")
        return (store.sample_items(), store.sample_coupons(),
                "示例数据（在线失败后的回退）", None, "示例门店（模拟）")

    print(f"门店：{data.store_name}（{data.store_code}）  城市搜索：{city} · {keyword}")
    print(f"餐品：{data.raw_meals} 个原始条目 → 过滤后 {len(data.items)} 个"
          f"（剔除 {len(data.dropped)} 个：{data.drop_summary}）")
    print(f"营养：官方表 {data.nutrition_rows} 条，按名称精确命中 {data.matched_nutrition} 个"
          f"（覆盖 {data.nutrition_coverage:.0%}）")
    if data.nutrition_coverage < 0.5:
        print("      覆盖率低于 50%：多数餐品在营养表里查不到，钠/热量约束会把它们判为「信息不足」")
    print()

    # 门店名单独放在 store_label 里，这里不再重复，否则卡片上会出现两遍店名。
    source = f"MCP 实时 · 营养覆盖 {data.matched_nutrition}/{len(data.items)}"
    return data.items, [], source, None, f"{data.store_name}（{data.store_code}）"


def cmd_ask(args: argparse.Namespace) -> int:
    lexicon = store.load_lexicon()
    matcher = Matcher(lexicon)
    profile, trace = parse_profile(args.text, lexicon)
    if args.budget:
        profile = replace(profile, budget=int(round(args.budget * 100)))
        trace = trace + [f"命令行指定人均预算：{args.budget:.2f} 元"]
    if args.risk:
        profile = replace(profile, allow_risk=True)
        trace = trace + ["命令行开启：允许「可能含有」的餐品进入方案"]

    items, coupons, source, verify_text, store_label = _load_menu(
        args.online, args.city, args.keyword)
    assessments = matcher.assess_all(items, profile)
    plans = build_plans(assessments, profile, coupons)

    _header()
    _print_profile(profile, trace, matcher)
    print(f"数据来源：{source}   共 {len(items)} 个餐品\n")
    if not args.online:
        print("⚠ 离线示例数据的配料字段是人工填的，用来演示「官方提供配料」时的完整能力。")
        print("  真实 MCP 不返回任何配料字段，--online 的结论会明显更保守（多数餐品只能判「信息不足」）。")
        print("  这不是两个产品，是同一个引擎在两种数据条件下的真实表现。\n")
    _print_plans(plans)
    _print_verdicts(assessments)

    from mcd import counter
    ask_report = counter.build(assessments, lexicon, profile)
    print(counter.render_text(ask_report))
    print(LINE)

    if args.html:
        from mcd.render import write_html
        write_html(
            args.html,
            profile=profile,
            trace=trace,
            assessments=assessments,
            plans=plans,
            meta={"source": source, "store": store_label, "lexicon": lexicon},
            verify_text=verify_text,
        )
        print(f"清单卡已生成：{args.html}")

    if args.json:
        data = {
            "profile": {
                "allergens": list(profile.allergens),
                "avoid": list(profile.avoid),
                "diets": list(profile.diets),
                "budget": profile.budget,
            },
            "trace": trace,
            "plans": [
                {
                    "label": p.label,
                    "items": [i.name for i in p.items],
                    "subtotal": p.subtotal,
                    "discount": p.discount,
                    "total": p.total,
                }
                for p in plans
            ],
            "assessments": [
                {"name": a.item.name, "verdict": a.verdict.name, "reasons": a.reasons}
                for a in assessments
            ],
        }
        Path(args.json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结构化结果已写入：{args.json}")

    return 0


NO_TOKEN_HELP = """
未检测到环境变量 MCD_MCP_TOKEN。

三步拿到 Token（免费，手机号即可）：

  1. 打开 https://open.mcd.cn/mcp ，点右上角「登录」，用手机号完成验证
  2. 登录后点右上角「控制台」→「激活」→ 同意服务协议 → 一键复制 Token
  3. 在当前终端设置环境变量（只影响这个窗口，不会写进任何文件）：

       PowerShell :  $env:MCD_MCP_TOKEN="粘贴你的Token"
       Git Bash   :  export MCD_MCP_TOKEN="粘贴你的Token"
       CMD        :  set MCD_MCP_TOKEN=粘贴你的Token

     然后回到本目录运行：

       python run.py doctor

注意：Token 等同于你的麦当劳账号凭证，不要贴到聊天窗口、Issue 或任何仓库文件里。
"""


def cmd_doctor(args: argparse.Namespace) -> int:
    """体检真实 MCP 能力，回答「这个产品到底能不能成立」。"""
    from mcd.doctor import diagnose, render_text, save_snapshot
    from mcd.mcp_client import McdClient, McpError, mask, token_from_env

    token = token_from_env()
    if not token:
        print(NO_TOKEN_HELP)
        return 2

    menu_args: dict = {}
    if args.args:
        try:
            menu_args = json.loads(args.args)
        except json.JSONDecodeError as e:
            print(f"--args 不是合法 JSON：{e}")
            return 2

    try:
        client = McdClient()
        report = diagnose(client, menu_args, city=args.city, keyword=args.keyword)
    except McpError as e:
        print(f"\n体检失败：{e}\n")
        print(NO_TOKEN_HELP)
        return 1

    print(render_text(report, mask(token)))

    if args.save:
        save_snapshot(report, args.save)
        print(f"快照已保存：{args.save}（可提交到仓库，作为真实接入的证据）")

    return 0 if report["verdict"]["level"] != "red" else 1


def cmd_tools(args: argparse.Namespace) -> int:
    from mcd.mcp_client import McdClient, McpError, mask, token_from_env

    print(f"\nToken：{mask(token_from_env())}")
    print(LINE)
    try:
        client = McdClient()
        info = client.initialize()
        print(f"握手成功：{info.get('serverInfo', {})}")
        tools = client.list_tools()
        print(f"共 {len(tools)} 个工具：\n")
        for t in tools:
            print(f"  · {t.get('name')}")
            desc = (t.get("description") or "").strip().replace("\n", " ")
            if desc:
                print(f"      {desc[:80]}")
        return 0
    except McpError as e:
        print(f"失败：{e}")
        return 1


def cmd_card(args: argparse.Namespace) -> int:
    """直接用示例数据生成一张清单卡，用于 README 截图。"""
    lexicon = store.load_lexicon()
    matcher = Matcher(lexicon)
    profile, trace = parse_profile(args.text, lexicon)
    items, coupons, source, _, store_label = _load_menu(False)
    assessments = matcher.assess_all(items, profile)
    plans = build_plans(assessments, profile, coupons)
    from mcd.render import write_html

    out = args.out or "card.html"
    write_html(out, profile=profile, trace=trace, assessments=assessments, plans=plans,
               meta={"source": source, "store": store_label, "lexicon": lexicon})
    print(f"已生成：{out}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """打开交互演示页。

    演示页是自包含的静态文件，不需要服务器、不需要 Token。这里只负责把它在你的
    默认浏览器里打开 —— 让「想看一下」这件事的门槛降到一条命令。
    """
    page = ROOT / "demo" / "index.html"
    if not page.exists():
        print(f"找不到演示页：{page}")
        return 1

    url = page.as_uri()
    if args.preset:
        url += f"?p={args.preset}"

    print(f"\n演示页：{page}")
    if args.preset:
        print(f"预设场景：{args.preset}")
    print(f"地址：{url}\n")

    if args.print_only:
        return 0

    import webbrowser
    if webbrowser.open(url):
        print("已在默认浏览器中打开。没反应的话，把上面的地址手动粘进浏览器。")
        return 0

    print("没能自动打开浏览器。把上面的地址手动粘进浏览器即可。")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="麦麦挑食官 · 忌口与过敏原感知的点餐助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run.py demo                                    # 打开交互演示（不用 Token）
  python run.py ask "我花生过敏，不吃辣，人均25"
  python run.py ask "乳糖不耐，预算30" --html card.html
  python run.py ask "纯素，预算40" --risk
  python run.py doctor         # 体检真实 MCP 能力（需 Token），拿到准确工具名
  python run.py tools          # 需要 MCD_MCP_TOKEN
  python run.py card --out card.html
""",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    dm = sub.add_parser("demo", help="在浏览器里打开交互演示页（零依赖、不用 Token）")
    dm.add_argument("--preset", default=None,
                    choices=["peanut", "lactose", "vegan", "office"],
                    help="直接落到某个场景：peanut / lactose / vegan / office")
    dm.add_argument("--print-only", action="store_true", help="只打印地址，不打开浏览器")
    dm.set_defaults(func=cmd_demo)

    d = sub.add_parser("doctor", help="体检真实 MCP 能力：工具名、配料字段、算价参数、红黄绿灯")
    d.add_argument("--args", default=None, help='调用菜单工具时传的参数（JSON），例如 --args \'{"storeCode":"..."}\'')
    d.add_argument("--city", default="北京市", help="体检用的城市（默认北京市）")
    d.add_argument("--keyword", default="国贸", help="体检用的门店关键词（默认国贸）")
    d.add_argument("--save", default=None, help="把体检结果保存为 JSON，可作为接入证据提交")
    d.set_defaults(func=cmd_doctor)

    a = sub.add_parser("ask", help="用一句话描述忌口，得到可吃清单与方案")
    a.add_argument("text", help="例如：我花生过敏，不吃辣，人均25")
    a.add_argument("--budget", type=float, default=None, help="人均预算（元），覆盖文本里的值")
    a.add_argument("--risk", action="store_true", help="允许「可能含有」的餐品进入方案")
    a.add_argument("--city", default=None, help="在线模式：城市，例如 北京市（与 --keyword 同时必填）")
    a.add_argument("--keyword", default=None, help="在线模式：门店关键词，例如 国贸")
    a.add_argument("--online", action="store_true", help="使用麦当劳 MCP 实时数据（需 MCD_MCP_TOKEN）")
    a.add_argument("--html", default=None, help="生成单文件 HTML 清单卡到指定路径")
    a.add_argument("--json", default=None, help="输出结构化结果到指定 JSON 路径")
    a.set_defaults(func=cmd_ask)

    t = sub.add_parser("tools", help="列出 MCP 实际提供的工具（用于核对工具名）")
    t.set_defaults(func=cmd_tools)

    c = sub.add_parser("card", help="用示例数据生成清单卡")
    c.add_argument("--out", default="card.html")
    c.add_argument("--text", default="我花生过敏，不吃辣，不吃油炸，人均25")
    c.set_defaults(func=cmd_card)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
