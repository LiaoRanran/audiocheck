#!/usr/bin/env python3
"""生成应用图标（PNG 多尺寸 + Windows ICO + macOS ICNS）。

图形语意：一条对数频率轴上的频谱包络 —— 直接对应"看频谱判音质"这个产品动作，
不用通用齿轮/音符那种跟功能无关的图标。
"""
from __future__ import annotations

import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))

BG = (18, 20, 26, 255)          # #12141A
ACCENT = (94, 224, 192, 255)    # #5EE0C0
ACCENT2 = (122, 162, 255, 255)  # #7AA2FF


def _envelope(x: float) -> float:
    """x ∈ [0,1] → 包络高度 ∈ [0,1]。中频隆起、两端收敛，像条音乐频谱。"""
    return (0.15 + 0.85 * math.exp(-((x - 0.42) ** 2) / 0.035)
            * (0.55 + 0.45 * math.sin(math.pi * x) ** 0.5))


def draw(size: int):
    from PIL import Image, ImageDraw
    S = size * 4                                   # 4× 超采样抗锯齿
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方底
    r = int(S * 0.22)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=BG)

    # 频谱柱：沿对数频率轴铺开
    n = 26
    pad = S * 0.16
    w = (S - 2 * pad) / n
    for i in range(n):
        x = i / (n - 1)
        h = _envelope(x)
        bh = max(S * 0.045, h * S * 0.56)
        x0 = pad + i * w
        y1 = S * 0.5 + bh / 2
        y0 = S * 0.5 - bh / 2
        col = ACCENT if i % 3 else ACCENT2
        d.rounded_rectangle([x0 + w * 0.12, y0, x0 + w * 0.88, y1],
                            radius=w * 0.30, fill=col)

    # 频率轴刻度线
    d.line([pad, S * 0.86, S - pad, S * 0.86], fill=(139, 147, 165, 200),
           width=max(2, S // 160))
    for i in range(5):
        x = pad + (S - 2 * pad) * i / 4
        d.line([x, S * 0.84, x, S * 0.88], fill=(139, 147, 165, 200),
               width=max(2, S // 160))

    return img.resize((size, size), Image.LANCZOS)


def main():
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("需要 Pillow：pip install Pillow")
        return 1

    png = os.path.join(HERE, "icon.png")
    draw(512).save(png)
    print("已生成", png)

    # Windows ICO：内嵌多尺寸，任务栏/标题栏/Alt-Tab 各取所需
    ico = os.path.join(HERE, "icon.ico")
    draw(256).save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                               (64, 64), (128, 128), (256, 256)])
    print("已生成", ico)

    # macOS ICNS（有 Pillow 就能写；无 mac 机型也可生成，供 CI 用）
    try:
        icns = os.path.join(HERE, "icon.icns")
        draw(512).save(icns, format="ICNS")
        print("已生成", icns)
    except Exception as e:                                   # noqa: BLE001
        print("ICNS 跳过（需要 Pillow 的 ICNS 支持）:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
