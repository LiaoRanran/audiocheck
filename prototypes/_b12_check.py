"""B12 样板屏机器判据复算（不靠眼睛）：面板文字对比、曲线对比、图像未坏 + 彩度预算。

面板像素 = veil 的 rgba 合成到氛围底上的**实际**颜色（Qt 在 sRGB 空间做 alpha 合成，
所以逐通道 a*fg+(1-a)*bg 与渲染一致）；K.veil 存的 rgb = P.mix(base,#fff,alpha)。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _kit as K  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from audiocheck.ui import palette as P  # noqa: E402


def _ints(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def composite(fg_hex, alpha, bg_hex):
    fg, bg = _ints(fg_hex), _ints(bg_hex)
    return "#%02x%02x%02x" % tuple(round(alpha * fg[i] + (1 - alpha) * bg[i]) for i in range(3))


def lum(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def veil_alpha(u, v):
    base_L = P.srgb_to_oklch(K._atmo_hex("g1", u, v))[0]
    return min(0.93, max(0.74, 0.86 + 0.30 * (0.55 - base_L)))


def panel_px(u, v, a):
    a = veil_alpha(u, v) if a is None else a
    base = K._atmo_hex("g1", u, v)
    mixed = P.mix(base, "#ffffff", a)
    return composite(mixed, a, base)


regions = {"topbar": (0.5, 0.10, None), "plot": (0.5, 0.55, None), "details": (0.5, 0.80, 0.78)}
print("=== 判据 3：文字 vs 面板底（需 ≥4.5:1）===")
for name, (u, v, a) in regions.items():
    px = panel_px(u, v, a)
    print("  %-8s panel=%s L=%.3f 文字对比=%.2f:1"
          % (name, px, P.srgb_to_oklch(px)[0], contrast(K.PAL["text"], px)))
print("=== 判据 4：曲线 vs 面板底（需 ≥3:1）===")
for lv in ("ok", "bad"):
    for name, (u, v, a) in regions.items():
        px = panel_px(u, v, a)
        print("  %s/%-8s 曲线对比=%.2f:1" % (lv, name, contrast(K.band(lv), px)))
print("=== 图像未坏 + 彩度预算 ===")
for name in ("ok", "bad"):
    p = os.path.join(ROOT, "shots", "b12", "b12s1_%s.png" % name)
    im = Image.open(p).convert("RGB")
    w, h = im.size
    flat = np.asarray(im.resize((160, 100))).reshape(-1, 3)
    Ls = np.empty(len(flat))
    Cs = np.empty(len(flat))
    for i, px in enumerate(flat):
        Lv, c, _ = P.srgb_to_oklch("#%02x%02x%02x" % tuple(px))
        Ls[i] = Lv
        Cs[i] = c
    uniq = len(np.unique(flat, axis=0))
    print("  %s %s uniq=%d Lmed=%.3f C>=0.05=%.1f%% C>0.10=%.2f%%"
          % (name, (w, h), uniq, float(np.median(Ls)),
             100 * (Cs >= 0.05).mean(), 100 * (Cs > 0.10).mean()))
