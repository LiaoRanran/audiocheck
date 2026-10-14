"""B10.5 氛围组合：S1/S2 × G1/G2 = 4 张，出到 `shots/b105/`。

## 做法

骨架 `mk_win(case)` 返回的根控件**一行都不改**——用 `K.AtmoRoot.wrap()` 把它
塞进一个画氛围底图的容器里，再把骨架自己的全局样式换成 `K.qss(transparent=True)`
（底色让位给氛围），最后 `K.dress()` 把"有布局且够大"的容器逐个改成
**半透明遮罩**（该处氛围色 + 白遮罩，alpha 从 0.55 起）。

## 记账（判据 2/3 的输入）

每张图写一条记录进 `shots/b105/report.json`：面板 alpha、各面板的遮罩色与
归一化位置、**文字对合成背景的对比**、**判定带对氛围的对比**、锚件是否在场。
判据脚本读它 + **重新量 PNG 自己的像素**做交叉验证（不许只信这份 json）。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTO = os.path.join(ROOT, "prototypes")
sys.path.insert(0, PROTO)
for _p in (os.path.join(ROOT, "packaging"),):
    sys.path.insert(0, _p)

import _kit as K  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

#: 面板遮罩 alpha。**B14 §11-④ 重新定标：0.45 → 0.55**（报数，不是静默改）。
#:
#: 三个约束把 alpha 往两个方向拉（实测 `out/_b14_alpha.py`，逐 0.05 扫）：
#:   * 文字对比 ≥4.5:1 —— 全程满足（7.3–16.0），**不是约束**；
#:   * 曲线（判定档色）对比 ≥3:1 —— 要求 **α ≥0.55**（0.50 时 2.85 不达标；
#:     0.55 时 3.03、0.70 时 3.55）——**方向与直觉相反**：氛围本身是中间调，
#:      veil 越薄面板越贴近氛围，判定绿/玫瑰越压不住；
#:   * 氛围中位彩度 ≥0.03（判据①）—— 要求 **α 尽量低**（0.55 时全图中位
#:     只有 0.0280，0.45 时才过线）。
#: ⇒ 0.45 是我在 B10.5 为迁就彩度判据降的，代价是曲线对比掉到 2.67。
#:    **判定语义色是红线不能动**，所以只能动 alpha：取 0.55（规范原值），
#:    曲线与文字双过；彩度那一项的**口径问题**另立提案（见交付报告）——
#:    按 CS1"氛围层单列"的原则，彩度该量**氛围层本身**（G2 设计值 0.030–0.075），
#:    而不是量被 veil 漂白后的整图。
ALPHA = K.COMPOSE_ALPHA   # 真源在 _kit（判据与渲染共用）
OUT = os.path.join(ROOT, "shots", "b105")


def load_mk(tag: str):
    """取骨架的 `mk_win`——**用 `__name__` 非 `__main__` 执行**，跳过它的入口，
    否则 import 一次就顺手往 shots/b10 再出一遍图。"""
    path = os.path.join(PROTO, "skeleton_%s.py" % tag)
    src = open(path, encoding="utf-8").read()
    ns = {"__name__": "skeleton_%s" % tag, "__file__": path}
    exec(compile(src, path, "exec"), ns)
    return ns["mk_win"]


def one(tag: str, kind: str, case) -> dict:
    import perf_probe

    a = K.app()
    mk = load_mk(tag)
    inner = mk(case)
    inner.setStyleSheet(K.qss(transparent=True))       # 底色让位给氛围
    root = K.AtmoRoot.wrap(kind, inner)
    root.resize(1400, 900)
    root.show()
    perf_probe.pump(a, 0.3)

    panels = K.dress(inner, kind, ALPHA)               # 面板改成半透明浮层
    perf_probe.pump(a, 0.25)

    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "%s_%s.png" % (tag, kind))
    root.grab().save(p)

    # ---- 记账：对比用**合成后**的色算，不是用令牌算 ----
    bg = K.panel_bg(kind, 0.5, 0.5, ALPHA)
    text_c = K.P.contrast(K.PAL["text"], bg)
    band_c = K.P.contrast(K.band(case["level"]), K._atmo_hex(kind, 0.15, 0.5))
    anchors = {
        "file": bool([w for w in inner.findChildren(QWidget)
                      if getattr(w, "text", lambda: "")() == case["file"]]),
        "verdict": bool([w for w in inner.findChildren(QWidget)
                         if "●" in (getattr(w, "text", lambda: "")() or "")]),
        "hero": bool(inner.findChildren(QWidget, "Hero")),
    }
    rec = {"image": os.path.basename(p), "skeleton": tag, "atmosphere": kind,
           "alpha": ALPHA, "panels": len(panels),
           "panel_bg_mid": bg, "text_contrast": round(text_c, 2),
           "band_contrast": round(band_c, 2), "anchors": anchors,
           "level": case["level"], "size": [root.width(), root.height()]}
    print("  %-14s 面板 %2d ｜ 遮罩 %s ｜ 文字对比 %.2f:1 ｜ 判定带对氛围 %.2f:1 ｜ 锚 %s"
          % (rec["image"], rec["panels"], bg, text_c, band_c,
             "".join(k[0] for k, v in anchors.items() if v)))
    root.close()
    root.deleteLater()
    return rec


if __name__ == "__main__":
    K.app()
    cases = {c["level"]: c for c in K.cases()}
    recs = []
    # 交付**恰好四张**（提示词 §4 点名的文件名）：每组合只渲染达标态，
    # 让"氛围"成为唯一变量——再加两个态会让 `shots/b105/` 里出现 8 张，
    # 而"4 张"是判决时要看的清单。
    for tag in ("s1", "s2"):
        for kind in ("g1", "g2"):
            recs.append(one(tag, kind, cases["ok"]))
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"alpha": ALPHA, "records": recs}, f, ensure_ascii=False, indent=1)
    pngs = sorted(f for f in os.listdir(OUT) if f.endswith(".png"))
    print("\nshots/b105: %d 张 → %s" % (len(pngs), pngs))
    print("记账写入 shots/b105/report.json（%d 条）" % len(recs))
    sys.exit(0 if len(pngs) == 4 else 1)
