"""从种子色生成整套语义色：OKLCH 色彩空间 + WCAG 对比度校验。

为什么不用 HSL/HSV 调色（这不是审美问题，是数学问题）
----------------------------------------------------
HSL 的 L 是**算术**明度：同一个 L 下黄色比蓝色亮得多（感知亮度差可达 3 倍），
于是"主色加亮 10%"在不同色相上是完全不同的视觉幅度，配色只能靠人眼试。
OKLab（Björn Ottosson, 2020）是**感知均匀**空间：等 L 步长 ≈ 等视觉亮度步长，
且能覆盖 sRGB 全域、可无损往返。OKLCH 就是它的圆柱坐标 (L, C, h)。

生成规则（每条都可验证，不靠"手选好看"）
----------------------------------------
1. 种子色 → OKLCH，**只取色相 h**：这是"整主题的色调"的唯一来源
2. 中性阶（bg → surface → surface_hi → surface_raised）：固定 L 阶梯 +
   极低 chroma（calm 深色 0.012 / 浅色 0.008；pop 见下一节），
   得到"带一点种子色温度"的灰
3. 文本阶（text → text_dim → text_faint）：沿 L 轴二分，直到与该背景的
   WCAG 对比度达到目标（正文 7.0 = AAA，次要/弱文本 4.5 = AA）
4. 语义色（success/warning/danger/info）**色相固定在常用区间**
   （绿 145° / 黄 85° / 红 25° / 青 230°），只按背景明度调 L、C；
   悬停与胶囊底由同一色相派生
5. 装饰色阶 `deco`（六等分色环）：并列关系的多色（导航图标 / 卡片强调线 /
   多文件对比曲线），**不承担语义**
6. 生成完跑一遍 `audit()` + `audit_deco()`：**不达标直接抛异常**，不是打个警告放过去

两档风格（`vibe`）：为什么需要"档"而不是"再调几个手选色"
--------------------------------------------------------
上一版只有一套生成规则，做出来的界面是**一色灰底 + 一处主色**——
"克制"是它，但也没法让它"缤纷"：想鲜艳就得手改色号，而手改的色号
不会自己达标。这里把"风格"也变成一组**可验证的参数**：

| | 表面彩度 | 表面色相阶梯 | 装饰色阶彩度 |
|---|---|---|---|
| `calm` | 0.012 / 0.008（近中性） | 无（同一色相） | 0.10（收敛） |
| `pop`  | 0.030 / 0.020（明显带色） | 每层 +10°（bg→raised 共 30°） | 0.18（饱和） |

两档都走同一套 WCAG 判据（`audit` + `audit_deco`），所以"缤纷"不是
"放弃可访问性换来的鲜艳"——它只是把参数推到判据允许的边界。
**明暗（mode）与风格（vibe）是两个正交的轴**：`pop` 也有浅色版本。

为什么对比度要在这里算而不是肉眼看：WCAG 2.1 的 4.5:1 是**可计算**的
（相对亮度 + (L₁+0.05)/(L₂+0.05)），而 4.4 与 4.6 的差别肉眼分辨不出来——
"我觉得够清楚"在可访问性上是无效证据。

已知边界
--------
* 色域映射用"保持色相、二分降 chroma"的简单策略，不做 Ottosson 的
  `oklch_to_oklab` 投影（那需要 L 的微调迭代）。对本项目的用途足够，
  且测试钉住"输出永远落在 sRGB 内"。
* 语义色的对比度只保证**在它自己的胶囊底上**达标（那是它被使用的方式）；
  语义色直接压在中性背景上时另有一条更弱的判据（仅记录，不拦截）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ------------------------------------------------------------------ 色彩空间

_M1 = ((0.4122214708, 0.5363325363, 0.0514459929),
       (0.2119034982, 0.6806995451, 0.1073969566),
       (0.0883024619, 0.2817188376, 0.6299787005))
_M2 = ((0.2104542553, 0.7936177850, -0.0040720468),
       (1.9779984951, -2.4285922050, 0.4505937099),
       (0.0259040371, 0.7827717662, -0.8086757660))
_M1_INV = ((4.0767416621, -3.3077115913, 0.2309699292),
           (-1.2684380046, 2.6097574011, -0.3413193965),
           (-0.0041960863, -0.7034186147, 1.7076147010))
_M2_INV = ((1.0, 0.3963377774, 0.2158037573),
           (1.0, -0.1055613458, -0.0638541728),
           (1.0, -0.0894841775, -1.2914855480))


def _cbrt(x: float) -> float:
    """带符号的立方根（线性 RGB 在色域外会是负数）。"""
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


def _mul(m, v):
    return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def parse_hex(color: str) -> tuple[float, float, float]:
    """`#rrggbb` / `#rgb` → (r, g, b)，取值 0–1。"""
    s = color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError("颜色必须是 #rgb 或 #rrggbb：%r" % color)
    return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def to_hex(rgb) -> str:
    return "#" + "".join("%02x" % max(0, min(255, round(c * 255))) for c in rgb)


def srgb_to_oklab(color: str) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(c) for c in parse_hex(color))
    lms = _mul(_M1, (r, g, b))
    l_, m_, s_ = (_cbrt(v) for v in lms)
    return _mul(_M2, (l_, m_, s_))


def oklab_to_linear(lab) -> tuple[float, float, float]:
    l_, m_, s_ = _mul(_M2_INV, lab)
    return _mul(_M1_INV, (l_ ** 3, m_ ** 3, s_ ** 3))


def in_gamut(lab, tol: float = 1e-4) -> bool:
    return all(-tol <= c <= 1 + tol for c in oklab_to_linear(lab))


def srgb_to_oklch(color: str) -> tuple[float, float, float]:
    """→ (L, C, h)，h 为角度（0–360）。"""
    L, a, b = srgb_to_oklab(color)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0


def oklch_to_srgb(lch, *, gamut: bool = True) -> str:
    """OKLCH → `#rrggbb`。

    `gamut=True` 时若超出 sRGB 就**保色相降 chroma**（二分），
    而不是把 RGB 分量硬夹到 [0,1]——硬夹会连带改变色相，
    于是"我调的是绿，出来的是黄绿"。
    """
    L, C, h = lch
    L = max(0.0, min(1.0, L))
    C = max(0.0, C)
    rad = math.radians(h)
    lab = (L, C * math.cos(rad), C * math.sin(rad))
    if gamut and not in_gamut(lab):
        lo, hi = 0.0, C
        for _ in range(28):
            mid = (lo + hi) / 2
            if in_gamut((L, mid * math.cos(rad), mid * math.sin(rad))):
                lo = mid
            else:
                hi = mid
        lab = (L, lo * math.cos(rad), lo * math.sin(rad))
    lin = oklab_to_linear(lab)
    return to_hex(tuple(_linear_to_srgb(max(0.0, min(1.0, c))) for c in lin))


def with_lch(color: str, *, L=None, C=None, h=None) -> str:
    L0, C0, h0 = srgb_to_oklch(color)
    return oklch_to_srgb((L0 if L is None else L,
                          C0 if C is None else C,
                          h0 if h is None else h))


# ------------------------------------------------------------------ WCAG 对比度

def relative_luminance(color: str) -> float:
    """WCAG 2.1 相对亮度（注意：与 OKLab 的 L 不是一回事）。"""
    r, g, b = (_srgb_to_linear(c) for c in parse_hex(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2.1 对比度，1.0–21.0。AA 要求正文 ≥4.5，大字号 ≥3.0。"""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, weight: float) -> str:
    """在线性 RGB 里按 weight 混合（0 = a，1 = b）。"""
    ra, rb = (_srgb_to_linear(c) for c in parse_hex(a)), (_srgb_to_linear(c) for c in parse_hex(b))
    return to_hex(tuple(_linear_to_srgb(x + (y - x) * weight) for x, y in zip(ra, rb)))


def fit_contrast(fg: str, bg: str, target: float, *, lighter: bool) -> str:
    """沿 OKLCH 的 L 轴把 fg 往 `lighter` 方向推，直到与 bg 的对比度 ≥ target。

    保色相与 chroma（色域外时由 `oklch_to_srgb` 自动降 chroma）。
    推到头仍不达标就返回端点值——**不假装成功**，由 `audit()` 去报错。
    """
    if contrast(fg, bg) >= target:
        return fg
    L, C, h = srgb_to_oklch(fg)
    lo, hi = (L, 1.0) if lighter else (0.0, L)
    best = oklch_to_srgb((1.0 if lighter else 0.0, C, h))
    for _ in range(30):
        mid = (lo + hi) / 2
        cand = oklch_to_srgb((mid, C, h))
        if contrast(cand, bg) >= target:
            best = cand
            if lighter:
                hi = mid
            else:
                lo = mid
        elif lighter:
            lo = mid
        else:
            hi = mid
    return best


# ------------------------------------------------------------------ 生成

# 语义色的**固定色相**：这三个是"约定俗成"，不是从种子色推的——
# 否则换个种子色就会得到"红色代表合格"这种荒谬结果。
LEVEL_HUE = {"success": 145.0, "warning": 85.0, "danger": 25.0, "info": 230.0}
LEVEL_CHROMA = {"success": 0.155, "warning": 0.155, "danger": 0.185, "info": 0.125}

# 灰/黑/白种子：OKLCH 的色相在这个 chroma 下没有意义（atan2(0,0) 恒为 0，
# 会得到"黑色种子 → 红色主色"这种荒谬结果）。退回项目本来的品牌色相。
DEFAULT_HUE = 264.0
ACHROMATIC_C = 0.012


# ------------------------------------------------------------------ 仪器灰（B2）

# **中性阶不再跟随种子色相。** 这是 B 块"配额制配色"最核心的一条改动：
# 原先整张界面（占 90% 面积的中性表面）都被种子色染一遍，于是换个种子色，
# 连背景灰都会变——"一枚 accent"事实上变成了"一枚 accent + 一整套染色"。
#
# 锚点 286°（OKLCH）的来历：取自用户 2026-10-13 提供的参考图，对图上
# 16.4 万像素做色相直方图，主峰簇落在 **280–291°**（HSL 口径写作 240–247），
# 次峰在 10°/340°（那是玫瑰 accent 本身）。取簇中位 286°。
# 用户原话描述这块是"冷一点灰一点紫"——286° 正是冷灰偏一丁点紫。
#
# ⚠ 提示词给的是 HSL 值（H 240–247 / S 0.05–0.08），而本模块全程用 OKLCH。
# **两种色彩空间不能直接搬数字**：HSL 的 243°/S 6% @ L 79% 换算过来是
# OKLCH 的 C≈0.0097、H≈286°。照字面实现会得到完全不同的颜色
# （HSL 的色相是"RGB 六角锥上的角度"，OKLCH 是"感知均匀空间的角度"，
# 同一个颜色在两个空间里的色相值可以差 40° 以上）。
#: 中性阶（背景/表面/描边/网格）的锚点色相。与种子色**解耦**。
NEUTRAL_HUE = 286.0

#: 中性阶彩度带上限（OKLCH C）。由 HSL 的 S≤0.08 @ L=0.79 换算得 0.0125。
#: calm 档两档取值都压在带内；pop 档**按设计豁免**——它存在的意义就是偏离
#: （"五颜六色"那一档），见 `VIBE_TINT` 与测试里的豁免登记。
NEUTRAL_CHROMA_MAX = 0.0125

#: 色温分层（`_warm_cool_hue`）的偏移上限，占"到目标色相的短弧"的比例。
#: **从 0.16 收到 0.08**：换锚点后重测发现，0.16 会把浅色主题的表面推到
#: 301°（品红侧），而参考图的中性簇实测只在 **280–291°** 这么窄的一段里。
#: 0.08 让两侧各偏 ≤7°：暗处 283°、亮处 293°——都还在仪器灰家族内。
WARM_COOL_MAX = 0.08

#: 判定结论条（slab）的明度与请求彩度。明度由**色域**反解：
#: 暗色 L0.38 处 danger 能到 C0.140、success C0.120（够用），
#: 亮色要到 L0.79 才让 danger 保住 C0.115（再亮就被夹到 0.076）。
#: 请求彩度 0.115 取锚点图高彩的 P50（0.123）略下方——**不超锚点**。
SLAB_L = {"dark": 0.38, "light": 0.79}
SLAB_C = 0.115

#: 双档阴影。**Qt 的 QSS 没有 box-shadow**（实测：写了不报错也不生效），
#: 所以阴影只能由效果对象或自绘实现，参数集中在这里。
#: 光的方向统一为**顶光**：dy 为正（往下投）、dx 略负（光从左上方来）。
#: 两档的差别是"离观察者多远"：near 给同层浮层（菜单 / Toast / 命令面板），
#: far 给跨层浮层（模态对话框）。
SHADOW_TIERS = {
    "near": {"blur": 12, "dx": -1, "dy": 2, "alpha": 0.45},
    "far": {"blur": 32, "dx": -2, "dy": 8, "alpha": 0.22},
}


# ------------------------------------------------------------------ 风格档（vibe）

#: 两档配色风格。**不是"明暗"的别名**（明暗由 `mode` 管，两者正交）：
#:   calm —— 表面近中性、装饰色阶收敛。默认档，仪表盘要"读数不抢眼"。
#:   pop  —— 表面带色相阶梯、装饰色阶饱和。想鲜艳时用它，判据一条不少。
VIBES = ("calm", "pop")

#: 表面（中性阶）的 chroma：(深色, 浅色)。
#: calm 两档都 ≤ `NEUTRAL_CHROMA_MAX`（仪器灰配额，0.012 < 0.0125 刚好压线）；
#: pop 的 0.030 **故意超出**——它是"五颜六色"那一档，超标的理由与豁免登记
#: 见 `NEUTRAL_HUE` 上方说明。**这两个值没有被本轮改动**：配额带是按
#: "现有参数里哪一档合规"选的，不是反过来把参数改成合规的样子。
VIBE_TINT = {"calm": (0.012, 0.008), "pop": (0.030, 0.020)}

#: pop 档的表面**色相阶梯**（相对种子色，单位度）。
#: 为什么要有：calm 的表面只靠明度分层，而人眼对"同一色相的不同明度"
#: 分辨力有限——层级读起来只是"深浅"。给它一点色相位移，层级就同时用
#: 明度和色相表达（绘画里叫"色彩推移"），界面一下子不再是一片灰。
#: bg 保持种子色相不动（那是"品牌底"），越往上（越靠前）偏得越多。
VIBE_SURFACE_HUE = {
    "plot_bg": -8.0, "bg": 0.0, "surface": 10.0, "surface_hi": 20.0,
    "surface_raised": 30.0, "grid": 6.0, "border": 14.0, "border_hi": 24.0,
    "hover": 12.0, "hover_hi": 22.0,
}

#: 装饰色阶：**六等分色环**（相对种子色 0/60/…/300°）。
#: 为什么是等分：这几个颜色是**并列**关系（多个文件、多个导航项、多块卡片），
#: 需要"彼此可分"且"等重"——等分保证任意两档至少隔 60°，
#: 同 L 保证它们亮度相同（谁都不比谁显眼，否则"第 3 条曲线天生比第 1 条弱"）。
#: 它**不承担语义**：语义只有 success/warning/danger 三个固定色相。
DECO_STEPS = (0.0, 60.0, 120.0, 180.0, 240.0, 300.0)
#: 装饰色阶的彩度：**calm 压在"高彩"口径（C>0.10）之下**，pop 仍可取饱和。
#: 理由（B7.1 做法 2 + 勘误 §5.5）：六色环是"ACG 那一轮"的遗产，它很细、
#: 面积判据抓不到，却把"一枚 accent"**稀释成六个色相**——实测主族占比只有
#: 64.1%，而判据要求 ≥85%。压到口径之下之后：六色**依然可分**（色相差 60°，
#: 在低彩下也能分辨），但界面只剩一条 accent 在"说彩度高的那句话"。
DECO_CHROMA = {"calm": 0.082, "pop": 0.18}
#: 装饰色阶的明度按模式镜像：深色主题上要亮、浅色主题上要深。
DECO_LIGHTNESS = {"dark": 0.70, "light": 0.55}
#: 装饰色阶要满足的非文本对比度（WCAG 1.4.11：图形对象 3:1）。
DECO_MIN_CONTRAST = 3.0

#: **"高彩"的口径**：OKLCH 彩度超过它才算高彩。面积与色相族两条统计判据
#: 都用这个阈值，所以它必须是**唯一真源**——测试（`tests/test_ui_b7_color.py`）
#: 与量尺（`packaging/hue_family_report.py`）都从这里取，各写一份迟早会对不上。
#: 取值 0.10：锚点图高彩的 P50 是 0.123，阈值取在它下方一档。
#: ⚠ 换空间就换数字：同一张图 OKLCH C>0.10 是 0.68% 时，
#: HSV S>0.35 是 1.83%、HSL S>0.35 是 21.9%（差 6 倍）。
HIGH_CHROMA_C = 0.10
#: 色相族的判据：单一 30° 桶在高彩像素里的占比下限。
HUE_FAMILY_MIN = 0.85


@dataclass(frozen=True)
class Finding:
    pair: str
    fg: str
    bg: str
    ratio: float
    required: float

    @property
    def ok(self) -> bool:
        return self.ratio >= self.required

    def line(self) -> str:
        return "  %-34s %-9s / %-9s  %5.2f : 1  （要求 %.1f）%s" % (
            self.pair, self.fg, self.bg, self.ratio, self.required,
            "" if self.ok else "   ← 不达标")


# 判据来自 WCAG 2.1：正文 AA 4.5 / AAA 7.0；非文本 UI 与大字 3.0。
# 「弱文本」这里也按 4.5 要求：它是 11px 的字段标签，属于**小字正文**，
# 按 3.0 放行等于给自己开后门。
CHECKS: tuple[tuple[str, str, str, float], ...] = (
    ("正文 text / surface", "text", "surface", 7.0),
    ("正文 text / bg", "text", "bg", 7.0),
    ("正文 text / surface_raised", "text", "surface_raised", 7.0),
    ("次要 text_dim / surface", "text_dim", "surface", 4.5),
    ("弱文本 text_faint / surface", "text_faint", "surface", 4.5),
    ("主按钮文字 on_primary / primary", "on_primary", "primary", 4.5),
    ("主按钮悬停 on_primary / primary_hi", "on_primary", "primary_hi", 4.5),
    ("激活导航 primary_text / primary_soft", "primary_text", "primary_soft", 4.5),
    ("主色自身 / surface（非文本 3:1）", "primary", "surface", 3.0),
    ("次级强调 / plot_bg（非文本 3:1）", "accent2", "plot_bg", 3.0),
    ("合格胶囊 success / success_bg", "success", "success_bg", 4.5),
    ("有疑胶囊 warning / warning_bg", "warning", "warning_bg", 4.5),
    ("不达标胶囊 danger / danger_bg", "danger", "danger_bg", 4.5),
    ("选中行文字 text / selection", "text", "selection", 4.5),
    # 判定结论条：它是**判定语义唯一的大面积着色**（见 `SLAB_L`），
    # 上面坐着正文（lbl_verdict 用 text）与次要行（lbl_file 用 slab_dim），
    # 两个角色都要按 AA/AAA 核。结论条同时要能读出"是一个独立物件"（3:1）。
    ("结论条正文 text / success_slab", "text", "success_slab", 7.0),
    ("结论条正文 text / warning_slab", "text", "warning_slab", 7.0),
    ("结论条正文 text / danger_slab", "text", "danger_slab", 7.0),
    ("结论条次文 slab_dim / success_slab", "slab_dim", "success_slab", 4.5),
    ("结论条次文 slab_dim / warning_slab", "slab_dim", "warning_slab", 4.5),
    ("结论条次文 slab_dim / danger_slab", "slab_dim", "danger_slab", 4.5),
    # ⚠ 这条**不是** 3:1，而是 1.5:1，理由必须写清楚（否则下一个人会以为漏了）：
    #   * WCAG 1.4.11 的 3:1 是给**小尺寸 UI 组件**的边界，用来"认出这个控件"。
    #     结论条是 880×140 的**大面积**色块，人眼对大面积的明度差不敏感得多
    #     （实测 1.57:1 已经清楚可辨，见 shots/b7/）；
    #   * 更要紧的是：**判定语义由文字承载**（胶囊上写着"不达标"），
    #     底色只是冗余强化。1.4.11 管的是"理解内容所必需的图形"，这里不缺它。
    #   阈值登记为 1.5：够看出"这里有一块面板"，又不逼着把结论条做成黑块。
    ("结论条与卡片底可分辨（大面积，登记阈 1.5）", "success_slab", "surface", 1.5),
    ("结论条与卡片底可分辨（大面积，登记阈 1.5）", "warning_slab", "surface", 1.5),
    ("结论条与卡片底可分辨（大面积，登记阈 1.5）", "danger_slab", "surface", 1.5),
    ("关闭按钮白字 / danger", "#ffffff", "danger", 4.5),
    ("图表刻度 text_faint / plot_bg", "text_faint", "plot_bg", 4.5),
    ("卡片描边 border_hi / surface（装饰性，仅记录）", "border_hi", "surface", 1.0),
)


def audit(palette, *, strict: bool = False) -> list[Finding]:
    """对一组配色跑对照度判据。`strict=True` 时不达标直接抛 ValueError。"""
    get = palette.get if isinstance(palette, dict) else lambda k, d=None: getattr(palette, k, d)
    out = []
    for name, fk, bk, need in CHECKS:
        fg = get(fk) if fk.startswith("#") else get(fk, "")
        bg = get(bk, "")
        if not fg or not bg:
            continue
        out.append(Finding(name, fg, bg, contrast(fg, bg), need))
    bad = [f for f in out if not f.ok]
    if strict and bad:
        raise ValueError("对比度不达标：\n" + "\n".join(f.line() for f in bad))
    return out


#: 装饰色阶要压在哪些底色上（每一条都对应一个真实用法）：
#:   surface      —— 侧边栏底：导航图标画在上面
#:   plot_bg      —— 图表底：第 N 条曲线画在上面
#:   primary_soft —— 激活导航项的胶囊底：图标此时压在这上面
DECO_BACKGROUNDS = ("surface", "plot_bg", "primary_soft")


def deco_checks(palette) -> list[Finding]:
    """装饰色阶 × 它真正会被画上去的那几个底色，逐条算对比度。

    为什么单列一个函数而不进 `CHECKS`：`CHECKS` 是"角色名 → 角色名"的平表，
    而 deco 是**一列**颜色，需要按序号展开。表结构与数据类型不一致时，
    硬塞进去只会让两边都失真。
    """
    get = palette.get if isinstance(palette, dict) else lambda k, d=None: getattr(palette, k, d)
    ramp = get("deco") or ()
    out = []
    for i, col in enumerate(ramp):
        for bk in DECO_BACKGROUNDS:
            bg = get(bk, "")
            if bg:
                out.append(Finding("deco[%d] / %s" % (i, bk), col, bg,
                                   contrast(col, bg), DECO_MIN_CONTRAST))
    return out


def audit_deco(palette, *, strict: bool = False) -> list[Finding]:
    """装饰色阶的可见性判据（非文本 3:1）。`strict=True` 时不达标直接抛。"""
    out = deco_checks(palette)
    bad = [f for f in out if not f.ok]
    if strict and bad:
        raise ValueError("装饰色阶不可见：\n" + "\n".join(f.line() for f in bad))
    return out


def conform(palette, checks=CHECKS, *, keep_hue: bool = True):
    """把不达标的前景角色沿 OKLCH 的 L 轴推到**刚好**达标，返回 (新配色, 改动清单)。

    只动前景（文字 / 胶囊文字），**不动背景**：背景一动，整个界面的明暗关系
    就得重排，那已经不是"修一个对比度"了。`keep_hue` 为真时色相与 chroma 都不变，
    所以"修完之后还是那个颜色，只是更清楚"。
    """
    get = palette.get if isinstance(palette, dict) else lambda k, d=None: getattr(palette, k, d)
    src = {k: get(k) for k in dir(palette) if not k.startswith("_")} \
        if not isinstance(palette, dict) else dict(palette)
    out, changes = dict(src), []
    for name, fk, bk, need in checks:
        fixed_fg = fk.startswith("#")
        fg = fk if fixed_fg else out.get(fk, "")
        bg = out.get(bk, "")
        if not fg or not bg:
            continue
        ratio = contrast(fg, bg)
        if ratio >= need:
            continue
        if fixed_fg:
            changes.append("%s：前景是写死的 %s，无法自动调整（%.2f < %.1f）"
                           % (name, fk, ratio, need))
            continue
        lighter = srgb_to_oklch(fg)[0] >= srgb_to_oklch(bg)[0]
        new = fit_contrast(fg, bg, need, lighter=lighter)
        out[fk] = new
        changes.append("%-13s %s → %s   %.2f → %.2f（要求 %.1f）"
                       % (fk, fg, new, ratio, contrast(new, bg), need))
    return out, changes


def report(palette, title: str = "") -> str:
    lines = ["== %s ==" % (title or "配色对照度")] if title else []
    lines += [f.line() for f in audit(palette)]
    return "\n".join(lines)


def _warm_cool_hue(hue: float, level: float) -> float:
    """把中性色的色相按明度往"冷 / 暖"各偏一点：**暗处偏冷、亮处偏暖**。

    这是自然光照的规律（阴影被天光染冷、高光偏暖），也是绘画里最基础的一招。
    整块界面统一偏一个色温会"发脏"，而**分层**能给中性色一点空气感——
    高光与阴影不在同一个色温上，正是人眼熟悉的样子。

    偏多少随 |L−0.5| 增长但封顶 `WARM_COOL_MAX`（沿**较短弧**转），所以：
    中间调几乎不动、越极端偏得越多，且绝不会绕到色环对面去。
    """
    target = 250.0 if level < 0.5 else 60.0
    delta = (target - hue + 180.0) % 360.0 - 180.0     # 沿短弧的有符号差
    return (hue + delta * min(WARM_COOL_MAX, abs(level - 0.5) * 0.22)) % 360.0


def build(seed: str, mode: str = "dark", vibe: str = "calm") -> dict:
    """从一个种子色生成整套语义色。返回 `theme.Palette` 的字段字典。

    深色与浅色**同一个种子**：色相不变，明度阶梯镜像——
    这就是"一个种子色一个品牌两套主题"的做法。

    `vibe` 是**与明暗正交**的第二根轴：`calm`（近中性表面、收敛的装饰色阶）
    与 `pop`（带色表面 + 色相阶梯 + 饱和装饰色阶）。两档走同一套判据，
    所以"缤纷"不会把对比度换掉。
    """
    if mode not in ("dark", "light"):
        raise ValueError("mode 必须是 dark 或 light")
    if vibe not in VIBES:
        raise ValueError("vibe 必须是 %s 之一，收到 %r" % (" / ".join(VIBES), vibe))
    dark = mode == "dark"
    _, seed_c, hue = srgb_to_oklch(seed)
    if seed_c < ACHROMATIC_C:
        hue = DEFAULT_HUE

    # ---- 1. 中性阶：固定 L 阶梯 + 低 chroma + 色温分层（+ pop 的色相阶梯）----
    # 基底色相是 `NEUTRAL_HUE`（仪器灰锚点），**不是种子色相**：见该常量上方
    # 的说明。种子色只决定 accent 一族（primary / accent2 / deco 的起点）。
    tint = VIBE_TINT[vibe][0 if dark else 1]

    def _shift(key: str) -> float:
        """pop 档的表面色相阶梯；calm 档恒为 0（同一色相）。"""
        return VIBE_SURFACE_HUE.get(key, 0.0) if vibe == "pop" else 0.0

    def _n(level: float, key: str, chroma: float | None = None) -> str:
        """中性色：按明度做色温分层（暗偏冷、亮偏暖）+ 按层级做色相推移。"""
        return oklch_to_srgb((level, tint if chroma is None else chroma,
                              _warm_cool_hue(NEUTRAL_HUE + _shift(key), level)))

    if dark:
        idx = {"bg": _n(0.165, "bg"), "plot_bg": _n(0.150, "plot_bg"),
               "surface": _n(0.196, "surface"),
               "surface_hi": _n(0.228, "surface_hi"),
               "surface_raised": _n(0.262, "surface_raised"),
               "grid": _n(0.245, "grid", tint * 1.6), "border": _n(0.275, "border", tint * 1.4),
               "border_hi": _n(0.330, "border_hi", tint * 1.4),
               "hover": _n(0.235, "hover", tint * 1.2),
               "hover_hi": _n(0.268, "hover_hi", tint * 1.4)}
    else:
        idx = {"bg": _n(0.955, "bg"), "plot_bg": _n(0.998, "plot_bg"),
               "surface": _n(0.995, "surface"),
               "surface_hi": _n(0.972, "surface_hi"),
               "surface_raised": _n(1.0, "surface_raised"),
               "grid": _n(0.905, "grid", tint * 1.6), "border": _n(0.900, "border", tint * 1.4),
               "border_hi": _n(0.850, "border_hi", tint * 1.4),
               "hover": _n(0.955, "hover", tint * 1.2),
               "hover_hi": _n(0.930, "hover_hi", tint * 1.4)}

    surf = idx["surface"]

    # ---- 2. 文本阶：从明度端点二分，保色相（文字略偏种子色，比纯灰耐看）----
    def _t(level: float, chroma: float) -> str:
        """文字色：同样吃色温分层（与中性阶同一族，界面才不会"两块色温打架"）。

        色相跟随它落脚的表面（pop 档表面有 +10° 的推移），否则文字与底
        会朝两个方向偏色。文字本身的 chroma 仍压得很低——带色的是面，不是字。
        """
        return oklch_to_srgb((level, chroma,
                              _warm_cool_hue(NEUTRAL_HUE + _shift("surface"), level)))

    base = _t(0.97 if dark else 0.18, 0.006)
    idx["text"] = fit_contrast(base, surf, 7.0, lighter=dark)
    idx["text_dim"] = fit_contrast(_t(0.72 if dark else 0.45, 0.010),
                                   surf, 4.5, lighter=dark)
    idx["text_faint"] = fit_contrast(_t(0.66 if dark else 0.55, 0.012),
                                     surf, 4.5, lighter=dark)
    # 禁用态**豁免**对比度要求（WCAG 1.4.3 明确排除禁用控件），
    # 但也别做到看不见：取弱文本与背景的中点。
    idx["text_disabled"] = mix(idx["text_faint"], surf, 0.45)

    # ---- 3. 主色：**尽量保留种子色本身**，让按钮文字去适配它 ----
    # 早先的做法是把主色压深到"白字达标"，代价是种子色的辨识度被改掉
    # （亮蓝 #3d7dff 会被压成 #316ff0 这种闷蓝）。既然这套系统的前提是
    # "种子色即品牌色"，那就该反过来：主色不动，选白字还是墨字由对比度决定。
    idx["primary_soft"] = oklch_to_srgb(
        (0.28 if dark else 0.94, min(0.055, seed_c * 0.5), hue))
    prim_c = min(0.18, max(0.10, seed_c))
    prim = oklch_to_srgb((0.62 if dark else 0.52, prim_c, hue))
    ink = "#0d0f13"
    if max(contrast("#ffffff", prim), contrast(ink, prim)) >= 4.5:
        idx["primary"] = prim
        idx["on_primary"] = ("#ffffff" if contrast("#ffffff", prim) >= contrast(ink, prim)
                             else ink)
    else:                       # 极亮/极暗的种子色：只能压到刚好达标（保色相）
        idx["primary"] = fit_contrast(prim, "#ffffff", 4.5, lighter=False)
        idx["on_primary"] = "#ffffff"
    # 悬停：往"更亮"（深色主题）/ "更暗"（浅色主题）挪一小步，
    # 但按钮文字的对比度仍要达标——方向由 fit_contrast 保证
    hi_l = srgb_to_oklch(idx["primary"])[0] + (0.06 if dark else -0.06)
    idx["primary_hi"] = fit_contrast(oklch_to_srgb((hi_l, prim_c, hue)),
                                     idx["on_primary"], 4.5, lighter=dark)
    # **激活导航的文字必须单独一个令牌**：`primary_hi` 是"按钮悬停底色"，
    # 它要够暗才能让白字达标（≥4.5）；而激活导航是"浅色文字压在 primary_soft 上"，
    # 要求够亮。两个方向互斥——用一个令牌必然有一处不达标（实测 3.2:1）。
    # 这不是配色品味问题，是两个角色被塞进了同一个名字。
    idx["primary_text"] = fit_contrast(idx["primary_hi"], idx["primary_soft"],
                                       4.5, lighter=dark)
    idx["border_focus"] = idx["primary"]

    # ---- 3b. 次级强调色：**冷 counterpart**（色相 = 中性阶锚点 286°）----
    # 同一明度、同一彩度，只转色相 → 两条曲线/两个高亮在视觉上"等重"。
    #
    # 为什么从"分裂互补 +150°"改成中性锚点：B 块的配额是"**一枚暖 + 一枚冷**"。
    # 早先 +150° 是从**种子色**出发转的，于是种子色一变，第二枚强调色也跟着跑
    # （玫瑰种子的 +150° 落在 164° 青绿，跟"冷紫"完全是两回事）。
    # 改成锚定在中性阶的冷色相 286°：暖的那枚是 accent（种子色），冷的那枚
    # 永远是同一支冷紫——**一对**，不是"跟着种子乱跑的第二枚"。
    # 它仍只用于数据可视化与装饰强调，**不承担语义**（语义色必须固定色相）。
    p_l, p_c, p_h = srgb_to_oklch(idx["primary"])
    # **色相分不开就换明度**。种子色本身落在冷区时（比如用户选了蓝紫），
    # accent2 与 primary 的色相只差二十几度——两条曲线并排放会认错。
    # 这时候把 accent2 压深一档：区分两个数据系列，色相与明度**至少给一个**
    # 是数据可视化的基本要求（色相不可分时的标准退路就是明度）。
    # 60° 是门槛：大于它靠色相就够分，硬压明度只会白白破坏"等重"。
    gap = abs((NEUTRAL_HUE - p_h + 180.0) % 360.0 - 180.0)
    a2_l = p_l - 0.18 if gap < 60.0 else p_l
    a2 = oklch_to_srgb((max(0.15, a2_l), p_c, NEUTRAL_HUE))
    if contrast(a2, idx["plot_bg"]) < 3.0:
        a2 = fit_contrast(a2, idx["plot_bg"], 3.0, lighter=dark)
    idx["accent2"] = a2

    # ---- 3c. 装饰色阶：六等分色环，**并列关系的多色** ----
    # 位置放在主色之后：它要压在 primary_soft（激活导航的胶囊底）上，
    # 而那个底色到这一步才存在。
    idx["deco"] = _deco_ramp(hue, mode, vibe,
                             (idx["surface"], idx["plot_bg"], idx["primary_soft"]))

    # ---- 4. 语义色：色相固定，明度按背景调 ----
    for key in ("success", "warning", "danger", "info"):
        h, c = LEVEL_HUE[key], LEVEL_CHROMA[key]
        bg_l = srgb_to_oklch(surf)[0] + (0.045 if dark else -0.035)
        idx[key + "_bg"] = oklch_to_srgb((bg_l, min(c * 0.35, 0.055), h))
        idx[key + "_bd"] = oklch_to_srgb((bg_l + (0.09 if dark else -0.09),
                                          min(c * 0.55, 0.085), h))
        start_l = 0.72 if dark else 0.52
        idx[key] = fit_contrast(oklch_to_srgb((start_l, c, h)), idx[key + "_bg"],
                                4.5, lighter=dark)

        # ---- 4a. 判定结论条（slab）：**判定语义唯一的大面积着色** ----
    # 来历：勘误 §5.6 给了处置顺序「① 先收 deco → ② 再量玫瑰够不够显眼 →
    # ③ 若仍不够，加大玫瑰的面积」，并点名"顶部结论条"就是玫瑰的面积该长的地方
    # （它承载信息，不是装饰）。这里就是 ③ 的落点。
    #
    # 为什么是"结论条"而不是别的：**它是唯一面积大、且不随状态飘移的载体**。
    # 判定胶囊只有 60×22，表格里的判定徽章随文件数变，曲线在绘图区（判据明确排除）。
    # 结论条在总览页恒在，尺寸由布局定 —— 这是"高彩面积"能成为判据的前提。
    #
    # ⚠ **warning 在暗色主题下做不到 C>0.10**：sRGB 色域里 H85 在 L0.38 处
    # 最大彩度只有 ~0.078（黄在低明度下可用彩度比红/绿小得多，实测见
    # `out/_slab_probe.py` 的输出）。这不是实现缺陷，是色域的物理边界——
    # 如实登记，并用它说明"高彩面积"判据**依赖当前选中的判定状态**。
    slab_l = SLAB_L[mode]
    step = -0.012 if dark else 0.012          # 背离文字的方向（暗底往下、亮底往上）
    idx["slab_dim"] = oklch_to_srgb((0.74 if dark else 0.40, 0.012, NEUTRAL_HUE))
    for key in ("success", "warning", "danger"):
        h = LEVEL_HUE[key]
        cand = ""
        for _ in range(30):
            cand = oklch_to_srgb((slab_l, SLAB_C, h))
            if (contrast(idx["text"], cand) >= 7.0
                    and contrast(idx["slab_dim"], cand) >= 4.5):
                break
            slab_l += step
        idx[key + "_slab"] = cand

    # ---- 4b. 关闭按钮的**按下**色：比 danger 再深一档 ----
    # 需要它的原因很具体：窗口关闭按钮的 `:hover` 与 `:pressed` 如果都是
    # `danger`，按下就没有任何额外反馈（`test_ui_motion` 的
    # "pressed 不许与 hover 同声明"一条就是这么发现的）。
    # 只给 danger 生成而不给四个语义色都生成：**只有这一处**需要"按下更深"，
    # 其余三色的按下态由更大的容器承担，多生成三枚就是三枚没人用的色号。
    # 更深而不是更浅：白字压在它上面只会更清楚，不需要新的审计条目。
    hi_l = srgb_to_oklch(idx["danger"])[0] - (0.10 if dark else 0.06)
    danger_lo = oklch_to_srgb((max(0.25, hi_l), LEVEL_CHROMA["danger"], LEVEL_HUE["danger"]))
    if dark:
        danger_lo = fit_contrast(danger_lo, idx["danger_bg"], 4.5, lighter=False)
    idx["danger_lo"] = danger_lo

    # ---- 5. 选中/阴影 ----
    idx["selection"] = mix(idx["primary_soft"], surf, 0.35 if dark else 0.45)
    # 双档阴影 + 顶光高亮。阴影色取**偏冷的中性**而不是纯黑：纯黑压在冷灰底上
    # 会发"脏"（黑是中性，底是冷调，交界处显灰雾）。近黑冷调（slate-950 一族）
    # 与中性阶同族，阴影才像"同一束光下的影子"。
    sh_rgb = (2, 6, 23) if dark else (15, 23, 42)
    for tier, cfg in SHADOW_TIERS.items():
        idx["shadow_" + tier] = "rgba(%d,%d,%d,%.2f)" % (
            sh_rgb[0], sh_rgb[1], sh_rgb[2], cfg["alpha"])
    # 顶光高亮：浮层上缘那 1px。比描边亮一档，仍是中性族——
    # 它模拟的是"光从上方来，上边缘被照亮"，不是一条装饰线。
    hl_l = srgb_to_oklch(idx["border_hi"])[0] + (0.07 if dark else -0.05)
    idx["top_light"] = oklch_to_srgb((hl_l, tint * 1.1, NEUTRAL_HUE))

    # 阈值门：不达标就报错，让问题在生成时就暴露
    audit(idx, strict=True)
    audit_deco(idx, strict=True)
    return idx


def _deco_ramp(seed_hue: float, mode: str, vibe: str, bgs) -> tuple[str, ...]:
    """六等分色环，明度整体平移直到**每一档都**对每个底色达标。

    为什么是"整体平移"而不是逐色 `fit_contrast`：逐色修正会破坏等 L，
    于是几档颜色的视觉重量不再相同——第 3 条曲线天生比第 1 条更抢眼，
    而它俩在数据上完全平权。宁可整列一起挪，也不让某一档单独变重。

    方向由模式定：深色主题上"更亮"才有对比度，浅色主题上反。
    """
    L = DECO_LIGHTNESS[mode]
    step = 0.02 if mode == "dark" else -0.02
    cols: tuple[str, ...] = ()
    for _ in range(12):
        cols = tuple(oklch_to_srgb((L, DECO_CHROMA[vibe], (seed_hue + s) % 360.0))
                     for s in DECO_STEPS)
        if min(contrast(c, bg) for c in cols for bg in bgs if bg) >= DECO_MIN_CONTRAST:
            return cols
        L = max(0.30, min(0.95, L + step))
    return cols        # 平移到头仍未达标：交给 `audit_deco(strict=True)` 报错


def theme_from_seed(name: str, seed: str, mode: str = "dark", vibe: str = "calm"):
    """生成一个 `theme.Theme`（延迟导入，避免 palette ↔ theme 循环依赖）。"""
    from .theme import Palette, Theme
    return Theme(name, Palette(name=name, **build(seed, mode, vibe)))


def self_check() -> list[str]:
    """对生成的配色做几条**与配色无关**的硬校验，返回问题列表。"""
    bad = []
    for vibe in VIBES:
        for mode in ("dark", "light"):
            for seed in ("#3d7dff", "#e0a92e", "#35c46a", "#f0524a", "#8b5cf6", "#000000"):
                try:
                    build(seed, mode, vibe)
                except ValueError as e:
                    bad.append("%s / %s / %s：%s"
                               % (vibe, mode, seed, str(e).splitlines()[0]))
    return bad
