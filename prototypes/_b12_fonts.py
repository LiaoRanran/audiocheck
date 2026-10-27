"""B12 §4 字体捆绑装载 + 断言（B10/B10.5「零字体环境」教训的可执行修复）。

fonts/ 里装的是：IBM Plex Sans / IBM Plex Mono / Noto Sans SC（全部 OFL）。
GR 自带的 Helvetica Neue LT Pro .otf **不进来**（商业字体，仅本机量测）。
offscreen 下 `QFontDatabase.families()` 本为 0 → 必须 addApplicationFont 后再断言，
否则样板屏又在降级字体（YaHei/Consolas）上验收。
"""
import glob
import os

from PySide6.QtGui import QFontDatabase

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

UI = "IBM Plex Sans"      # 西文/界面
MONO = "IBM Plex Mono"    # 数字/等宽
CJK = "Noto Sans SC"       # 中文


def load(app=None) -> dict:
    files = sorted(set(glob.glob(os.path.join(FONTS_DIR, "*.ttf"))
                       + glob.glob(os.path.join(FONTS_DIR, "*.otf"))))
    assert files, "prototypes/fonts/ 为空——先获取 IBM Plex + Noto Sans SC（OFL）再跑样板屏"
    for f in files:
        if QFontDatabase.addApplicationFont(f) == -1:
            print("  [warn] 装载失败：%s" % os.path.basename(f))
    fams = set(QFontDatabase.families())
    want = [UI, MONO, CJK]
    missing = [w for w in want if w not in fams]
    if missing:                          # B10/B10.5 教训：缺族要 fail loudly
        print("QFontDatabase.families() =", sorted(fams))
        raise AssertionError("字体缺族（包装病例）：%s" % missing)
    print("  字体装载 OK：%s ｜ 族数=%d" % (want, len(fams)))
    return {"ui": UI, "mono": MONO, "cjk": CJK}
