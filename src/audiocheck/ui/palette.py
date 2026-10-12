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
   极小 chroma（深色 0.012 / 浅色 0.008），得到"带一点种子色温度"的灰
3. 文本阶（text → text_dim → text_faint）：沿 L 轴二分，直到与该背景的
   WCAG 对比度达到目标（正文 7.0 = AAA，次要/弱文本 4.5 = AA）
4. 语义色（success/warning/danger/info）**色相固定在常用区间**
   （绿 145° / 黄 85° / 红 25° / 青 230°），只按背景明度调 L、C；
   悬停与胶囊底由同一色相派生
5. 生成完跑一遍 `audit()`：**不达标直接抛异常**，不是打个警告放过去

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
    ("合格胶囊 success / success_bg", "success", "success_bg", 4.5),
    ("有疑胶囊 warning / warning_bg", "warning", "warning_bg", 4.5),
    ("不达标胶囊 danger / danger_bg", "danger", "danger_bg", 4.5),
    ("选中行文字 text / selection", "text", "selection", 4.5),
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


def build(seed: str, mode: str = "dark") -> dict:
    """从一个种子色生成整套语义色。返回 `theme.Palette` 的字段字典。

    深色与浅色**同一个种子**：色相不变，明度阶梯镜像——
    这就是"一个种子色一个品牌两套主题"的做法。
    """
    if mode not in ("dark", "light"):
        raise ValueError("mode 必须是 dark 或 light")
    dark = mode == "dark"
    _, seed_c, hue = srgb_to_oklch(seed)
    if seed_c < ACHROMATIC_C:
        hue = DEFAULT_HUE

    # ---- 1. 中性阶：固定 L 阶梯 + 极低 chroma（带一点种子色的温度）----
    tint = 0.012 if dark else 0.008
    if dark:
        ladder = {"bg": 0.165, "plot_bg": 0.150, "surface": 0.196,
                  "surface_hi": 0.228, "surface_raised": 0.262}
        border = {"border": 0.275, "border_hi": 0.330}
        idx = {k: oklch_to_srgb((v, tint, hue)) for k, v in ladder.items()}
        idx["grid"] = oklch_to_srgb((0.245, tint * 1.6, hue))
        idx["border"], idx["border_hi"] = (oklch_to_srgb((v, tint * 1.4, hue))
                                           for v in (border["border"], border["border_hi"]))
        idx["hover"] = oklch_to_srgb((0.235, tint * 1.2, hue))
        idx["hover_hi"] = oklch_to_srgb((0.268, tint * 1.4, hue))
    else:
        ladder = {"bg": 0.955, "plot_bg": 0.998, "surface": 0.995,
                  "surface_hi": 0.972, "surface_raised": 1.0}
        idx = {k: oklch_to_srgb((v, tint, hue)) for k, v in ladder.items()}
        idx["grid"] = oklch_to_srgb((0.905, tint * 1.6, hue))
        idx["border"] = oklch_to_srgb((0.900, tint * 1.4, hue))
        idx["border_hi"] = oklch_to_srgb((0.850, tint * 1.4, hue))
        idx["hover"] = oklch_to_srgb((0.955, tint * 1.2, hue))
        idx["hover_hi"] = oklch_to_srgb((0.930, tint * 1.4, hue))

    surf = idx["surface"]

    # ---- 2. 文本阶：从明度端点二分，保色相（文字略偏种子色，比纯灰耐看）----
    text_h = hue
    base = oklch_to_srgb((0.97 if dark else 0.18, 0.006, text_h))
    idx["text"] = fit_contrast(base, surf, 7.0, lighter=dark)
    idx["text_dim"] = fit_contrast(oklch_to_srgb((0.72 if dark else 0.45, 0.010, text_h)),
                                   surf, 4.5, lighter=dark)
    idx["text_faint"] = fit_contrast(oklch_to_srgb((0.66 if dark else 0.55, 0.012, text_h)),
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

    # ---- 5. 选中/阴影 ----
    idx["selection"] = mix(idx["primary_soft"], surf, 0.35 if dark else 0.45)
    idx["shadow"] = "rgba(0,0,0,.45)" if dark else "rgba(15,23,42,.12)"

    # 阈值门：不达标就报错，让问题在生成时就暴露
    audit(idx, strict=True)
    return idx


def theme_from_seed(name: str, seed: str, mode: str = "dark"):
    """生成一个 `theme.Theme`（延迟导入，避免 palette ↔ theme 循环依赖）。"""
    from .theme import Palette, Theme
    return Theme(name, Palette(name=name, **build(seed, mode)))


def self_check() -> list[str]:
    """对生成的配色做几条**与配色无关**的硬校验，返回问题列表。"""
    bad = []
    for mode in ("dark", "light"):
        for seed in ("#3d7dff", "#e0a92e", "#35c46a", "#f0524a", "#8b5cf6", "#000000"):
            try:
                build(seed, mode)
            except ValueError as e:
                bad.append("%s / %s：%s" % (mode, seed, str(e).splitlines()[0]))
    return bad
