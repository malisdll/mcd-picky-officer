"""麦麦挑食官 · 忌口与过敏原感知的点餐助手。

模块划分：
    profile   中文口语 → 忌口档案
    matcher   餐品 × 档案 → 四态判定
    planner   在可吃集合内搭方案
    counter   把「查不到」编译成能念给柜台的问句
    pricing   分/元换算、优惠计算、均摊
    verify    本地价 vs 官方算价交叉验证
    infer     从菜名推断辣/油炸/主料（只支撑「可能含有」）
    nutrition 解析官方营养文本表（只做精确匹配）
    live      真实 MCP 数据 → 内部模型
    doctor    MCP 能力体检（零副作用，自带写接口拦截）
    render    生成单文件 HTML 清单卡
"""

__version__ = "0.2.0"
__all__ = [
    "models", "profile", "matcher", "planner", "pricing", "verify",
    "counter", "infer", "nutrition", "live", "doctor",
    "mcp_client", "render", "store",
]
