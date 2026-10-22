# MCP 集成说明

本文件说明项目**实际使用了哪些 MCP 能力、怎么调用、为什么必须用**。所有工具名、参数、字段
均为 **2026-10-22 用真实 Token 实测**的结果，不是推测。实测报告见 `docs/mcp-tools.json`。

---

## 0. 接真实数据前，先跑体检

```bash
export MCD_MCP_TOKEN="..."                 # PowerShell: $env:MCD_MCP_TOKEN="..."
python run.py doctor --save docs/mcp-tools.json
```

`mcd/doctor.py` 解决的是「在拿到 Token 之前，工具名、字段、参数全靠猜」这个问题。

它不是简单地 `tools/list` 一下，而是自己走完一条**只读探测链**：

```
query-nearby-stores(北京市/国贸) → 拿到 storeCode
        ↓
query-meals(storeCode, orderType=1, beType=1) → 拿到菜单
        ↓
query-meal-detail(code=第一个餐品) → 看有没有配料字段
        ↓
list-nutrition-foods() → 数行数、读表头字段名
```

为什么必须这样：`query-meals` 的 `required` 是 `["storeCode","orderType","beType"]`，
不先查门店就连菜单都调不了。体检报告里那行 `参数 {"storeCode": "1950564", ...}`
就是自举成功的证据——它不是写死的，是当次查出来的。

三档结论：

| 灯 | 判定条件 | 意味着 |
|---|---|---|
| ✅ 绿灯 | 菜单可取 + 任意返回体里检出配料/过敏原字段 | 三态判定可以做到「精确」，产品按设计成立 |
| ⚠️ 黄灯 | 菜单可取，但无配料字段（有无营养数据分两种文案） | 只能做营养筛选 + 菜名推断，叙事要改成「帮你看清哪些查不到」 |
| ❌ 红灯 | 菜单工具找不到 / 调用失败 / 解析不出餐品 | 产品根基不成立，先解决取数问题 |

**2026-10-22 实测结论：黄灯。** 35 个工具里没有一个提供配料或过敏原字段。
具体见第 2 节。

### 硬性安全约束

`doctor` 内置写动词名单（`MUTATING`），命中即拒绝调用，宁可红灯也不冒险：

```python
MUTATING = ("create", "cancel", "bind", "draw", "claim", "receive", "refund",
            "submit", "delete", "pay", "redeem", "exchange")
```

这套规则对真实的 35 个工具名做了手工核对，结果固化成测试
（`tests/test_doctor.py::TestRealToolAudit`）：

- **被拦的 7 个**：`create-order`、`cancel-order`、`mall-create-order`、
  `party-order-create`、`delivery-create-address`、`auto-bind-coupons`、`draw-lottery`
- **放行的 28 个**：其余纯查询工具，包括 `query-order`、`order-list`、
  `query-lottery-info` 这类**名字里带 order / lottery 但实际只读**的

两个真踩过的坑：

1. 最初的拦法是把 `order`、`lottery` 当关键词，结果 `query-order` 被误杀，
   而 `auto-bind-coupons`（真的会往账户里写券）反而漏过去了。方向错得很彻底。
2. 菜单工具真名是 `query-meals`，候选表里只有 `list-menu`，导致
   **工具明明存在、体检却报红灯**。诊断工具自己误诊是最难堪的 bug，
   所以现在有 `test_capability_matrix_matches_real_names` 守着——真实工具名必须全部命中。

另外，**配料字段可能内嵌在菜单返回里**而没有独立的 nutrition 工具。这种情况下矩阵会显示
`xxx（内嵌字段）`，而不是误报「未找到」——否则会出现「结论绿灯、矩阵标红」的自相矛盾。

---

## 1. 接入信息

| 项 | 值 |
|---|---|
| MCP Server | 麦当劳中国 MCP 服务（官方托管，`mcd-mcp v1.0.0`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP + JSON-RPC 2.0，协议版本 `2025-06-18` |
| 鉴权 | 请求头 `Authorization: Bearer <MCD_MCP_TOKEN>` |
| Token 获取 | https://open.mcd.cn/mcp → 手机号登录 → 控制台 → 激活 |
| 频率上限 | 600 次 / 分钟（超限返回 429，客户端已做专门提示） |
| 工具总数 | 35（2026-10-22） |
| 客户端实现 | `mcd/mcp_client.py`，仅用 Python 标准库（`urllib`），无第三方依赖 |

Token 只从环境变量 `MCD_MCP_TOKEN` 读取，**不接受命令行参数传入**——避免它出现在 shell history 和进程列表里。

### 一个容易踩的坑：业务错误不是 JSON-RPC 错误

麦当劳在 HTTP 200 + 正常 JSON-RPC 结果里返回业务失败：

```json
{"structuredContent": {"success": false, "code": "600058", "message": "城市名或者关键词不能为空"}}
```

只看 `error` 字段会漏掉它，表现为「调用成功但数据是空的」。`call_tool()` 里已显式检查
`success is False` 并抛 `McpError`，避免这种静默失败。

---

## 2. 能力 → 工具映射（实测）

> 工具名为 2026-10-22 真实返回。`McdClient.pick_tool()` 保留名称容错：
> 先精确匹配候选名，失败再按子串匹配，避免官方改名导致整个流程挂掉。

| 业务能力 | 实测工具名 | 关键参数 | 在流程中的作用 | 缺了会怎样 |
|---|---|---|---|---|
| 查附近门店 | `query-nearby-stores` | `beType`、`searchType`、`city`、`keyword`（城市与关键词**必须同时给**） | 拿到 `storeCode`，后续所有调用的前提 | 无法确定门店 → 退化为示例数据 |
| 查菜单 / 餐品 | `query-meals` | `storeCode`、`orderType`、`beType` | 餐品主体：`name` / `currentPrice` / `image` / `tags` | **核心依赖**，缺失则无法工作 |
| 套餐结构 | `query-meal-detail` | `storeCode`、`orderType`、`beType`、`code` | 拆三件套、看可选加料 | 套餐类餐品的营养无法对上 |
| 营养数据 | `list-nutrition-foods` | 无 | 160 条精确营养值（热量/蛋白/脂肪/碳水/钠/钙） | 全部降级为「信息不足」 |
| 我的券包 | `query-my-coupons` | 无 | 计算券后价 | 方案不带优惠 |
| 门店可用券 | `query-store-coupons` / `available-coupons` | `storeCode`、`orderType`、`beType` | 校验券在该门店是否可用 | 可能「算得便宜、到店用不了」 |
| 官方算价 | `calculate-price` | `storeCode`、`orderType`、`beType`、`items` | **交叉验证本地算价** | 失去唯一的正确性证据 |
| 服务端时间 | `now-time-info` | 无 | 判断早餐时段与活动有效期 | 用本地时间兜底 |

### 做不到的：配料与过敏原

这是本次实测最重要的发现，也是产品定位被重写的原因。

| 接口 | 实际字段 | 有配料吗 |
|---|---|---|
| `query-meals` | `name` / `currentPrice` / `originalPrice` / `image` / `tags` | ❌ |
| `query-meal-detail` | `code` / `content` / `choices` / `diffPrice` / `maxQuantity` … | ❌ |
| `list-nutrition-foods` | `productName` / `nutritionDescription` / `energyKj` / `energyKcal` / `protein` / `fat` / `carbohydrate` / `sodium` / `calcium` | ❌ |

`nutritionDescription` 这一列 160 行**全是 null**——它看起来像配料描述，但官方没有填。

所以本项目的过敏原能力只到「**可能含有，需向门店确认**」这一档，绝不断言「含」或「不含」。
这条线写在 `mcd/matcher.py` 里，由 `tests/test_matcher.py` 守着。

### 明确未使用的工具及原因

| 工具 | 不用它的原因 |
|---|---|
| `create-order` / `cancel-order` | 生成/取消真实订单，本项目只输出方案 |
| `auto-bind-coupons` | **会自动往用户账户里领券**，未经确认不能执行 |
| `draw-lottery` / `mall-*-order` | 扣减积分、兑换实物，与主线无关 |
| `party-*` / `query-promotions` / `query-meal-assistance` | 企业团餐与派对预约场景，本产品定位是个人忌口确认 |
| `delivery-*` | 麦乐送地址体系；本项目走「到店自取」单一路径以控制复杂度 |

---

## 3. 调用流程（实际跑的路径）

```
用户一句话
   │
   ├─(0) 本地解析：中文口语 → 结构化忌口档案         [不消耗 Token]
   │
   ├─(1) initialize 握手 → 取 Mcp-Session-Id
   │
   ├─(2) query-nearby-stores(city, keyword) → storeCode
   │
   ├─(3) query-meals(storeCode, 1, 1) → 110 个原始条目
   │        └─ 过滤掉周边/券类/无价格的 6 个 → 104 个餐品
   │
   ├─(4) list-nutrition-foods() → 160 行文本表，本地按名称【精确】匹配
   │        └─ 命中 19 个（18%）→ 挂上钠/热量/蛋白
   │        └─ 未命中 → sodium=None, kcal=0 → 判定「信息不足」
   │
   ├─(5) 本地求解：四态判定 + 预算内配餐            [纯本地，可离线测试]
   │
   ├─(6) calculate-price(选中餐品) → 与本地合计逐分比对
   │        └─ 差异 > 0 分 → 报 MISMATCH，不静默修正
   │
   └─(7) 输出：终端摘要 / 单文件 HTML 清单卡
```

**关键点：第 4 步的「命中率只有 18%」是被接受的设计，不是缺陷要藏起来。**

菜单里满是「麦辣鸡腿汉堡三件套」这类套餐，营养表里是「麦辣鸡腿汉堡」这类单品。
`nutrition.lookup()` 故意只做精确匹配——把套餐的热量套用单品值，就是在编数据。
所以 82% 的餐品会落进「信息不足」，界面如实显示，并给出「去问柜台这几样」的可执行建议。

---

## 4. 交叉验证：唯一能证明数据真实性的一环

```python
from mcd.verify import compare

local    = sum(item.price for item in selected)   # 本地计算，单位：分
official = call_tool("calculate-price", ...)      # 官方返回，单位：分

result = compare(local, official)                  # tolerance 默认 0
# MATCH      → 核价一致
# MISMATCH   → 差一分也报错
# UNAVAILABLE→ 官方接口不可用，如实标注「未经核验」
```

为什么容差是 0：差一分钱说明本地少算或多算了某个规格/加价项，这种偏差在几份餐叠加后会放大。
宁可报警打断，也不要悄悄抹平。

注意 `calculate-price` 返回的价格单位是**元**（字符串如 `"33.5"`），
`live._price_to_cents()` 负责转成整数分，避免浮点误差。

---

## 5. 副作用与确认策略

| 操作 | 风险 | 本项目的处理 |
|---|---|---|
| 查门店 / 菜单 / 营养 / 算价 | 无 | 自动执行 |
| 查券包 | 读取个人账户数据 | 自动执行，但**不落盘、不进日志** |
| 一键领券（`auto-bind-coupons`） | 真实写入账户 | **不实现** |
| 下单 | 生成真实订单 | **不实现**。本产品只到「决定吃什么」为止 |
| 支付 | 真实扣款 | **不做** |
| 抽奖 / 积分兑换 | 扣减积分 | **不实现** |

产品边界写在这里而不是「以后再说」：一个帮人确认能不能吃的工具，
没有理由同时握着下单能力。少一个权限，就少一类事故。

---

## 6. 业务价值

**对用户**：把一个「反复问柜台」的线下动作，变成一次 3 秒的筛选。特别是带小孩的家长、
过敏人群、控钠的高血压人群、素食者——他们不是想吃得更省，是想确认自己**能不能吃**。

**对麦当劳**：

- 现有 MCP 的营养数据是「查得到但用不上」的状态：160 行精确数值，谁也不会自己去翻；
  需要一层场景化的编排才能变成用户价值；
- 忌口人群是高频复购但决策成本极高的一群人，降低这一层的摩擦直接对应转化率；
- 「信息不足」清单天然是一个线上到线下的引导动作——它把一个可能流失的用户推回了门店柜台。
  而且它同时是在告诉官方：**补上 `nutritionDescription` 这一列，这个用户就不用去问了**。

---

## 7. 失败降级策略

| 失败点 | 行为 |
|---|---|
| 无 Token | 自动使用示例数据，并在输出中明确标注「示例数据（非实时）」 |
| 握手失败 / 网络错误 | 提示具体错误，回退示例数据，不静默失败 |
| 429 限流 | 明确提示频率上限，不自动重试（避免加剧） |
| 工具改名 | `pick_tool()` 子串匹配兜底；doctor 的候选表需同步更新 |
| 门店查询缺参数（600058） | 明确要求 `--city` 与 `--keyword` 同时提供 |
| 菜单有返回但解析不出餐品 | 明确告知「字段名可能变化」，回退示例数据 |
| 营养表挂掉 | 菜单照常输出，营养字段留空 → 相关约束判「信息不足」，不让用户连饭都点不了 |
| 算价不可用 | 标注「未经核验」，不伪造一致性 |

---

## 8. 数据安全

- Token 仅存于环境变量，仓库内 `mcp-config.example.json` 只有占位符；
- 不持久化任何个人数据（券、订单、手机号）；
- 日志只输出工具名与计数，不输出响应体；
- 提交前对仓库做一次敏感信息扫描（`token` / `Bearer` / 手机号正则）。
