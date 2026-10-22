"""生成单文件 HTML 清单卡。

设计约束：
1. 零外部资源（无 CDN、无字体、无 JS），断网也能打开，手机能直接截图转发；
2. 三态用颜色 + 文字双编码，不依赖颜色单独传达信息（色弱可用）；
3. 打印友好，可以直接打出来贴在公司冰箱上。
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from .models import Assessment, Plan, Profile, Verdict
from .pricing import yuan

_CSS = """
:root{--ink:#16181d;--ink2:#3f4652;--ink3:#6b7280;--line:#e5e0d8;--bg:#fbf9f6;--card:#fff;
--hit:#b42318;--risk:#b45309;--info:#6b7280;--clear:#1e7a4f;--accent:#8a4b12}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);line-height:1.65;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:28px 20px 60px}
.hd{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:22px}
.brand{font-size:11.5px;letter-spacing:.16em;color:var(--accent);font-weight:700;margin-bottom:6px}
h1{font-size:24px;margin:0 0 6px;letter-spacing:-.02em}
.meta{font-size:12px;color:var(--ink3)}
h2{font-size:15px;margin:26px 0 10px;padding-left:9px;border-left:3px solid var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{font-size:12.5px;padding:3px 10px;border-radius:999px;background:#fff;border:1px solid var(--line);color:var(--ink2)}
.chip.hit{border-color:#f0b8b3;background:#fef3f2;color:var(--hit);font-weight:600}
.chip.diet{border-color:#c6e5d4;background:#f2faf5;color:var(--clear)}
.trace{font-size:12px;color:var(--ink3);background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:10px 0}
.trace div{margin:2px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
.card.plan{border-left:3px solid var(--accent)}
.plan-h{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.plan-t{font-weight:700;font-size:14.5px}
.price{font-size:18px;font-weight:800;letter-spacing:-.02em}
.price s{font-size:12px;color:var(--ink3);font-weight:400;margin-right:4px}
ul.items{margin:8px 0 0;padding-left:18px;font-size:13.5px;color:var(--ink2)}
ul.items li{margin:3px 0}
.note{font-size:12px;color:var(--ink3);margin-top:8px;padding-top:8px;border-top:1px dashed var(--line)}
table{width:100%;border-collapse:collapse;font-size:13px;background:#fff;margin:8px 0}
th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left}
th{font-size:11.5px;color:var(--ink3);font-weight:600;background:#f7f4ef}
td.r{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.v{font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;white-space:nowrap}
.v-hit{background:#fef3f2;color:var(--hit)}
.v-risk{background:#fdf3e7;color:var(--risk)}
.v-info{background:#f3f4f6;color:var(--info)}
.v-clear{background:#f2faf5;color:var(--clear)}
.reason{font-size:11.5px;color:var(--ink3);display:block;margin-top:2px}
.legend{font-size:12px;color:var(--ink2);background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 14px}
.legend span{display:inline-block;margin-right:12px}
.disc{font-size:11.5px;color:var(--ink3);border-top:1px solid var(--line);margin-top:28px;padding-top:14px}
.disc b{color:var(--ink2)}
.empty{font-size:13px;color:var(--ink3);padding:10px 0}
.ask{background:#fff;border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;padding:14px 16px;margin:10px 0}
.ask .lead{font-size:12.5px;color:var(--ink3);margin:0 0 8px}
.ask ol{margin:6px 0 0;padding-left:20px;font-size:13.5px;color:var(--ink)}
.ask ol li{margin:5px 0}
.ask .who{font-size:12px;color:var(--ink3);display:block;margin-top:3px}
.ask .grp{margin-top:12px;padding-top:10px;border-top:1px dashed var(--line)}
.ask .grp:first-of-type{border-top:none;padding-top:0;margin-top:0}
"""

_V_CLASS = {
    Verdict.HIT: "v-hit",
    Verdict.RISK: "v-risk",
    Verdict.NO_INFO: "v-info",
    Verdict.CLEAR: "v-clear",
}

_CAT_LABEL = {"main": "主食", "side": "配餐", "drink": "饮品", "dessert": "甜品"}


def _chip(text: str, cls: str = "") -> str:
    return f'<span class="chip {cls}">{escape(text)}</span>'


def _rows(assessments: list[Assessment], show_price: bool = True) -> str:
    if not assessments:
        return '<div class="empty">（无）</div>'
    out = ["<table><thead><tr><th>餐品</th><th>判定</th><th class='r'>价格</th></tr></thead><tbody>"]
    for a in assessments:
        # 「未见疑点」不需要逐行解释，否则整页都是同一句话
        if a.verdict == Verdict.CLEAR:
            reasons = ""
        else:
            reasons = "".join(f'<span class="reason">{escape(r)}</span>' for r in a.reasons[:2])
        out.append(
            "<tr>"
            f"<td>{escape(a.item.name)}{reasons}</td>"
            f'<td><span class="v {_V_CLASS[a.verdict]}">{escape(a.verdict.label)}</span></td>'
            f'<td class="r">{yuan(a.item.price)}</td>'
            "</tr>"
        )
    out.append("</tbody></table>")
    return "".join(out)


def _plans_html(plans: list[Plan]) -> str:
    if not plans:
        return '<div class="empty">没有生成可用方案。</div>'
    out = []
    for p in plans:
        if p.items:
            items = "".join(f"<li>{escape(i.name)} — {yuan(i.price)} 元</li>" for i in p.items)
            if p.discount:
                price = f'<span class="price"><s>{yuan(p.subtotal)}</s>{yuan(p.total)} 元</span>'
            else:
                price = f'<span class="price">{yuan(p.total)} 元</span>'
            body = f'<ul class="items">{items}</ul>'
        else:
            price = ""
            body = ""
        notes = "".join(f"<div>{escape(n)}</div>" for n in p.notes)
        out.append(
            '<div class="card plan">'
            f'<div class="plan-h"><span class="plan-t">{escape(p.label)}</span>{price}</div>'
            f"{body}"
            + (f'<div class="note">{notes}</div>' if notes else "")
            + "</div>"
        )
    return "".join(out)


def _ask_html(report: dict[str, Any]) -> str:
    """把「信息不足」变成能念给柜台的句子。

    这一块是整个清单卡里唯一「看完就能立刻行动」的部分：
    前面告诉你哪些查不到，这里告诉你去问什么。
    """
    parts: list[str] = ['<div class="ask">']
    parts.append('<p class="lead">官方查得到的数据有限。下面这几句可以直接念给店员，'
                 '问完再决定点什么。</p>')

    if report.get("universal"):
        parts.append('<div class="grp"><ol>')
        for q in report["universal"]:
            who = "先问这句——任何接口都查不到，但严重过敏最需要它"
            parts.append(f'<li>{escape(q)}<span class="who">{who}</span></li>')
        parts.append("</ol></div>")

    for g in report.get("groups", []):
        parts.append('<div class="grp"><ol>')
        for q in g["asks"]:
            parts.append(f"<li>{escape(q)}</li>")
        names = "、".join(g["names"])
        more = f" 等 {g['count']} 项" if g["count"] > len(g["names"]) else ""
        parts.append(f'</ol><span class="who">适用于：{escape(names)}{escape(more)}</span></div>')

    if not report.get("universal") and not report.get("groups"):
        parts.append('<div class="empty">没有需要单独确认的餐品。</div>')

    parts.append("</div>")
    return "".join(parts)


def render_html(
    profile: Profile,
    trace: list[str],
    assessments: list[Assessment],
    plans: list[Plan],
    meta: dict[str, Any] | None = None,
    verify_text: str | None = None,
) -> str:
    meta = meta or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    from .matcher import Matcher  # 延迟导入，避免循环依赖
    matcher = Matcher(meta["lexicon"]) if meta.get("lexicon") else None

    def label(group: str, key: str) -> str:
        if matcher is None:
            return key
        if group == "allergens":
            return matcher.allergen_label(key)
        if group == "avoid":
            return matcher.avoid_label(key)
        return matcher.diet_label(key)

    chips = []
    for a in profile.allergens:
        chips.append(_chip(f"过敏 {label('allergens', a)}", "hit"))
    for v in profile.avoid:
        chips.append(_chip(f"不吃 {label('avoid', v)}", "hit"))
    for d in profile.diets:
        chips.append(_chip(label("diet", d), "diet"))
    if profile.budget:
        chips.append(_chip(f"人均预算 {yuan(profile.budget)} 元"))
    if profile.allow_risk:
        chips.append(_chip("已开启「可能含有」餐品"))
    if not chips:
        chips.append(_chip("未设置忌口"))

    hit = [a for a in assessments if a.verdict == Verdict.HIT]
    risk = [a for a in assessments if a.verdict == Verdict.RISK]
    clear = [a for a in assessments if a.verdict == Verdict.CLEAR]
    noinfo = [a for a in assessments if a.verdict == Verdict.NO_INFO]

    trace_html = "".join(f"<div>· {escape(t)}</div>" for t in trace)

    from . import counter  # 延迟导入，避免与 matcher 形成环
    ask_report = (counter.build(assessments, meta["lexicon"], profile)
                  if meta.get("lexicon") else {"universal": [], "groups": [], "total": 0})

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>我的可吃清单 · 麦麦挑食官</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<div class="hd">
  <div class="brand">麦麦挑食官 · PICKY OFFICER</div>
  <h1>我的可吃清单</h1>
  <div class="meta">{escape(now)} · {escape(str(meta.get("source", "示例数据（非实时）")))} · {escape(str(meta.get("store", "未指定门店")))}</div>
</div>

<h2>你说了什么，我理解成什么</h2>
<div class="chips">{''.join(chips)}</div>
<div class="trace">{trace_html}</div>

<h2>三个方案</h2>
{_plans_html(plans)}

<div class="legend">
  <span><b class="v v-hit">含</b> 明确命中，已排除</span>
  <span><b class="v v-risk">可能含有</b> 需向门店确认</span>
  <span><b class="v v-info">信息不足</b> 官方查不到对应数据</span>
  <span><b class="v v-clear">未见疑点</b> 官方标注中未列出</span>
</div>

<h2>可以直接点的（{len(clear)} 项）</h2>
{_rows(clear)}

<h2>数据缺失、需要你确认的（{len(noinfo)} 项）</h2>
<p style="font-size:12.5px;color:#6b7280;margin:4px 0 8px">「信息不足」不等于安全。官方没有提供配料信息时，我们不会替你猜。</p>
{_rows(noinfo)}

<h2>去柜台时念这几句</h2>
{_ask_html(ask_report)}

<h2>可能含有，建议问一句柜台（{len(risk)} 项）</h2>
{_rows(risk)}

<h2>已为你排除的（{len(hit)} 项）</h2>
{_rows(hit)}

<div class="disc">
  <b>这不是医疗或营养建议。</b>本清单基于官方公开的配料与营养数据做规则匹配，
  无法覆盖门店现场制作中的交叉接触（同一油炸锅、同一操作台、共用器具）。
  <b>严重过敏请务必在门店当面确认，并以门店答复为准。</b><br>
  {f"核价信息：{escape(verify_text)}<br>" if verify_text else ""}
  本项目为「麦当劳程序员创意开发大赛」参赛作品，由参赛者独立开发，非麦当劳官方产品。
  价格、供应与配料以麦当劳官方渠道实时结果为准。
</div>
</div></body></html>"""


def write_html(path: str, *args: Any, **kwargs: Any) -> str:
    html = render_html(*args, **kwargs)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return path
