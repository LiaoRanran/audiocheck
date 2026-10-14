"""分辨率感知字号（B14 §3A）：字号 = `f(分辨率档)`，同屏比例成组。

## 为什么单独一个模块

`theme.Type` 是**一档固定 px**（10/11/12/14/15/16/20/40）——在 2K/4K 屏上偏小，
这是此前所有界面的**结构性**问题（B12 从 GR 拆出来时确认：它的字号表是
`HD_QHD_4K(valHD, valQHD)`，按分辨率档取值）。

## 两条设计（都从 GR 语法账来，可追溯）

1. **档位 + 递推系数**：HD < 2000 ≤ QHD < 3400 ≤ 4K；HD→QHD ≈ **1.17×**、
   QHD→4K ≈ **1.33×**（GR `gr-helpers.js:4341` 同思想；GR 的 `HD_QHD_4K(12,14)`
   正是 12→14 ≈1.17）。
2. **同屏比例成组**（CS3，替代"≤5 种字号"的计数式纪律）：不数种类，
   保证**同一屏内的比例关系**。这条的性质是**跨档不变**——所有角色同乘一个
   系数，所以比例在三个档位上逐位相同。判据钉的就是这个不变性。

## ⚠ 与 QSS 的关系

QSS 的 `font-size` 是**静态**的（模板在装载时定型），所以分辨率感知要靠
**装载时按当前屏幕选档**：`tier(screen_w)` → `fs(role)` → 写进令牌。
窗口跨屏拖动时按 B14 §3A 的要求重算（落地轮接 `changeEvent`）。
"""
from __future__ import annotations

#: 档位阈值（逻辑像素宽）：HD < 2000 ≤ QHD < 3400 ≤ 4K。
#: 取值理由：1440（HD 基准，本项目所有截图与调参都在这个宽度上做）、
#: 2560（QHD 最常见）、3840（4K）。阈值取在两档的**中间**，
#: 免得 2000/3400 这种边界宽度来回跳档。
TIERS = (("HD", 0, 2000), ("QHD", 2000, 3400), ("4K", 3400, 10 ** 9))

#: 递推系数（相对 HD 基准）。GR 的 `HD_QHD_4K(12, 14)` 就是 1.17。
TIER_SCALE = {"HD": 1.0, "QHD": 1.17, "4K": 1.56}   # 1.17 × 1.33 ≈ 1.56

#: HD 基准字号 = 现有 `Type` 的值（1440 宽下调好的，**不重调**）。
#: 角色名与 `theme.Type` 的字段一一对应，另加 matplotlib 的 `chart_tick`。
BASE = {
    "micro": 10.0, "caption": 11.0, "chart_tick": 9.0, "body_sm": 12.0,
    "body": 14.0, "body_md": 15.0, "title": 16.0, "heading": 20.0,
    "display": 27.0, "hero": 40.0,
}

#: 同屏比例组（CS3）。**一个屏内只允许这几组比例**，组与组之间不约束——
#: 因为"标题:正文"和"hero:标题"服务的是不同层级，硬凑一个比例反而失真。
#: 判据钉的是"组内比例跨档不变"，不是"组等于某几个数"。
GROUPS = (
    ("hero:title:body", ("hero", "title", "body")),
    ("heading:body_md:body", ("heading", "body_md", "body")),
    ("caption:micro", ("caption", "micro")),
)


def tier(width: int) -> str:
    """逻辑像素宽 → 档位名（HD / QHD / 4K）。"""
    for name, lo, hi in TIERS:
        if lo <= width < hi:
            return name
    return TIERS[-1][0]


def scale_of(width: int) -> float:
    """该宽度对应的字号系数。"""
    return TIER_SCALE[tier(width)]


def fs(role: str, width: int = 1440) -> float:
    """角色 → 该分辨率下的字号（px，**一位小数**，避免浮点尾巴进 QSS）。"""
    if role not in BASE:
        raise KeyError("未知字号角色 %r（可用：%s）" % (role, sorted(BASE)))
    return round(BASE[role] * scale_of(width), 1)


def table(width: int = 1440) -> dict:
    """该档位下全部角色的字号表（给 `theme` 装载时用）。"""
    return {r: fs(r, width) for r in BASE}


def ratios(width: int = 1440) -> dict:
    """同屏比例组的实测比例（**报数用**：判据与文档都引它）。"""
    out = {}
    for name, roles in GROUPS:
        vals = [fs(r, width) for r in roles]
        base = vals[-1]
        out[name] = tuple(round(v / base, 3) for v in vals)
    return out


if __name__ == "__main__":                      # 自检：三档梯度 + 比例不变
    print("%-12s %s" % ("档位", "  ".join("%-8s" % r for r in BASE)))
    for w in (1440, 2560, 3840):
        print("%-12s %s" % ("%s(%d)" % (tier(w), w),
                            "  ".join("%-8.1f" % fs(r, w) for r in BASE)))
    print("\n比例组（应三档完全一致）:")
    for w in (1440, 2560, 3840):
        print("  %-12s %s" % ("%s(%d)" % (tier(w), w), ratios(w)))
