"""B12 §4 质感样板屏（S1 基底 · 面板为主 = `default` 字号集）。

把《语法合并账》的决策逐条用上，每处在 `样板屏_制作说明.md` 引决策 ID：
  基底 S1 default 集 ｜ 氛围 G1 退至边衬/面板半透（D0/共性-4）｜ 数据区纯净
  深度 = 面板 α≈0.86 + shadow + detailsBg 第二底色（D-depth, gr-color-manager.js:491）
  圆角 5px（D1, gr-main-components.js:1432）｜ 边 = 1px 外暗 + 内高光（:1461-1462）
  光随底色为函数（D-light：veil α = f(底实测亮度)）｜ 强调单枚 + 判定语义色（红外线④）
  字号 = f(分辨率档,布局模式)（D4, HD_QHD_4K 思想）｜ 字体 IBM Plex + 思源（共性-5）

渲染：系统 python + offscreen + 字体捆绑 + 断言（B10/B10.5 零字体教训）。
调用铁律（防静默退出）：`python -u -c "exec(open(path).read())"` + `__file__` 注入。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _b12_fonts  # noqa: E402
import _kit as K  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from audiocheck.ui import palette as P  # noqa: E402

PAL = K.PAL
W, H = 1440, 900                       # 参考宽：HD 档（QHD/4K 见 fs() 递推）
KIND = os.environ.get("B12_KIND", "g1")                   # 氛围：g1 极光（已指认）/ g2 暮光纸（已指认）
LAYOUT = "default"                     # S1=default；S2=artwork 为另一套
SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shots", "b12")

# ---- 字号 = f(分辨率档, 布局模式)（D4，GR HD_QHD_4K 思想）----------------
TIER = "HD" if W < 2000 else ("QHD" if W < 3400 else "4K")
ROLES = {                             # role: (px_default, px_artwork)
    "title": (14, 12.5), "hero": (40, 34), "verdict": (22, 18),
    "body": (12, 11), "label": (10, 10), "num": (16, 13), "numbig": (15, 13),
}


def _fs(px_hd, px_qhd=None, px_4k=None):
    q = px_qhd if px_qhd is not None else round(px_hd * 1.17, 1)     # HD→QHD ~1.17×
    k = px_4k if px_4k is not None else round(px_hd * 1.33, 1)
    return {"HD": px_hd, "QHD": q, "4K": k}[TIER]


def tp(role):
    return _fs(ROLES[role][0 if LAYOUT == "default" else 1])


# ---- 字体（捆绑 + 断言后注入）-------------------------------------------
UI, MONO, CJK = _b12_fonts.UI, _b12_fonts.MONO, _b12_fonts.CJK


def _f(px, weight=400, num=False) -> QFont:
    f = QFont()
    f.setFamilies([MONO] if num else [UI, CJK])       # 缺字按字形回退（CJK→思源）
    f.setPointSizeF(px)
    f.setWeight(QFont.Weight.DemiBold if weight >= 600
                else QFont.Weight.Medium if weight >= 500 else QFont.Weight.Normal)
    return f


# 把 _kit 内部（顶栏/曲线注记）也切到捆绑字体——否则 GraphQL 图又用回降级字体
K.tune_font = lambda f, size, *, weight=400, num=False: _patch(f, size, weight, num)
K.num_font = lambda size: _patch(QFont(), size, 400, True)


def _patch(f, size, weight, num):
    return _f(size, weight=weight, num=num)


def L(text, role, *, weight=400, num=False, color=None) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(_f(tp(role), weight=weight, num=num))
    if color:
        lbl.setStyleSheet("color:%s;" % color)
    return lbl


# ---- 光随底色为函数：veil α = f(底实测亮度)（D-light, gr-color-styles.js:178）----
def veil_alpha(u: float, v: float) -> float:
    base_L = P.srgb_to_oklch(K._atmo_hex(KIND, u, v))[0]
    return min(0.93, max(0.74, 0.86 + 0.30 * (0.55 - base_L)))   # 暗处多抹白→面板稳定偏亮


def hair() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background:%s;border:0;" % PAL["border"])
    return f


def panel(u: float, v: float, a: float | None = None, *, radius=5,
          detail=False, shadow=True) -> str:
    """GR 深度：半透 veil + 5px 圆角 + 1px 外暗线 + 内高光线；可选加阴影。"""
    alpha = veil_alpha(u, v) if a is None else a
    col = K.veil(KIND, u, v, alpha, light=True)
    inner_hi = "border-top:1px solid %s;" % _rgba(PAL.get("text", "#111"), 0.10)
    return "%sborder-radius:%dpx;border:1px solid %s;%s" % (col, radius, PAL["border"], inner_hi)


def _rgba(hexc, a):
    h = hexc.lstrip("#")
    return "rgba(%d,%d,%d,%d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(a * 255))


def add_shadow(w, blur=16, dy=2, rgba=(0, 0, 0, 150)):
    fx = QGraphicsDropShadowEffect(w)
    fx.setBlurRadius(blur)
    fx.setOffset(0, dy)
    fx.setColor(QColor(*rgba))
    w.setGraphicsEffect(fx)


def mk_win(case):
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(30, 24, 30, 22)
    lay.setSpacing(16)

    # ---- 顶栏：文件名 + 判定态 + hero（恒定锚，捆绑字体 + 分辨率字号）----
    tb = QWidget()
    hb = QHBoxLayout(tb)
    hb.setContentsMargins(0, 0, 0, 0)
    hb.setSpacing(12)
    hb.addWidget(L(case["file"], "title", weight=600))
    hb.addWidget(L("● " + case["verdict"], "label", weight=600, color=K.band(case["level"])))
    hb.addStretch(1)
    hero = L("%.2f" % case["cutoff_khz"], "hero", weight=600, num=True)
    hb.addWidget(hero)
    hb.addWidget(L("kHz", "label", color=PAL["text_dim"]))
    tb.setStyleSheet(panel(0.5, 0.10, radius=5))
    add_shadow(tb)
    lay.addWidget(tb)

    lay.addWidget(K.verdict_band(case))

    vr = QHBoxLayout()
    vr.addWidget(L("● " + case["verdict"], "verdict", weight=600, color=K.on_tint(case["level"])))
    vr.addStretch(1)
    vr.addWidget(L("真实截止", "label", color=PAL["text_dim"]))
    vr.addWidget(L("%.2f kHz" % case["cutoff_khz"], "numbig", weight=600, num=True))
    lay.addLayout(vr)
    lay.addWidget(L(case["why"], "body", color=PAL["text_dim"]))

    # ---- 曲线面板：数据区纯净（D0）----
    pw = QWidget()
    pv = QVBoxLayout(pw)
    pv.setContentsMargins(4, 4, 4, 4)
    pw.setStyleSheet(panel(0.5, 0.55, radius=5))
    add_shadow(pw, blur=20, dy=3)
    pw.layout().addWidget(K.PlotWidget.make(case))
    lay.addWidget(pw, 5)

    lay.addWidget(hair())

    # ---- 详情区：detailsBg 第二底色（D-depth）----
    dw = QWidget()
    dv = QVBoxLayout(dw)
    dv.setContentsMargins(14, 12, 14, 12)
    dv.setSpacing(8)
    dw.setStyleSheet(panel(0.5, 0.80, a=0.78, radius=5, detail=True))   # 略不同 α → 分层
    nums = [("砖墙陡度", "67 dB/oct"), ("集成响度", "-22.9 LUFS"),
            ("真峰值", "+0.5 dBTP"), ("动态范围", "15.4 dB")]
    nr = QHBoxLayout()
    nr.setSpacing(0)
    for i, (k, v2) in enumerate(nums):
        cell = QVBoxLayout()
        cell.setSpacing(2)
        cell.addWidget(L(k, "label", color=PAL["text_faint"]))
        cell.addWidget(L(v2, "num", weight=600, num=True))
        nr.addLayout(cell, 1)
        if i < len(nums) - 1:
            sep = QFrame()
            sep.setFixedWidth(1)
            sep.setStyleSheet("background:%s;border:0;" % PAL["border"])
            nr.addWidget(sep)
    dv.addLayout(nr)
    lay.addWidget(dw)

    lay.addWidget(hair())
    btn = L("▸ 全部 18 项指标（另有 14 项）", "body")
    lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)

    root = K.AtmoRoot.wrap(KIND, inner)
    return root


def shot(win, name):
    import perf_probe
    win.resize(W, H)
    win.show()
    perf_probe.pump(K.app(), 0.5)
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, "%s.png" % name)
    win.grab().save(path)
    print("  出图 %-22s（%d KB）" % (os.path.basename(path), os.path.getsize(path) // 1024))
    return path


if __name__ == "__main__":
    K.app()                                  # QApplication 先建（addApplicationFont 需要它）
    _b12_fonts.load()                        # 字体捆绑 + 断言（缺族 fail loudly）
    for case in K.cases():
        tag = "b12s1_%s%s" % (case["level"], "" if KIND == "g1" else "_" + KIND)
        shot(mk_win(case), tag)
    print("B12 样板屏 S1 完成：TIER=%s LAYOUT=%s KIND=%s" % (TIER, LAYOUT, KIND))
