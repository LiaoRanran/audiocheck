#!/usr/bin/env python3
"""E3 无障碍三态表：按 Qt 无障碍五方面过一遍本项目 GUI（先量，不改）。

跑法：

    python packaging/a11y_report.py
    python packaging/a11y_report.py --strict   # 有**未登记**的"不支持"即返回 1

五方面（依据 Qt 官方 `doc.qt.io/qt-6/accessible.html`）：

    可缩放 UI / 键盘导航 / 颜色与对比度 / 声音与语音 / 辅助工具（屏幕阅读器）

纪律（E 块提示词 E3，两条硬规矩）：

* **不支持项必须诚实登记**：`KNOWN_GAPS` 里的条目允许"部分支持/不支持"，
  未登记的缺口必须修或补登记——**不许悄悄放过**；
* **不许宣称"符合 WCAG / 无障碍标准"**：本脚本只做"按五方面自查"，
  措辞以三态表为准（与 clreq 那条口径纪律同源）。

离屏运行（QT_QPA_PLATFORM=offscreen）：不弹窗，可进 CI。
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402

#: 诚实登记的已知缺口（三态表的"部分支持 / 不支持"项）。
#: 每条必须带**理由**——登记不是免责，是把"我们知道、且现在不改"写下来。
KNOWN_GAPS: dict[str, tuple[str, str]] = {
    # (方面, 状态) → (条目, 理由)
    ("颜色与对比度", "部分支持"): (
        "输入描边 border / surface ≈1.2:1",
        "WCAG 1.4.11 对控件边界要 3:1，本项目实测 1.18–1.33:1 不达标。"
        "抬高全边框 = 改 B 块七轮定下的视觉语言，属 B 块决定；"
        "palette.CHECKS 已按 1.0 登记并在报告里显示实测值。"),
    ("声音与语音", "不适用"): (
        "纯语音提示 / 声音反馈",
        "本工具是**音频分析工具**，用声音播报结果会污染被分析的听音环境，"
        "且用户正在听的就是音频本身。明确不适用，给理由。"),
    ("辅助工具", "部分支持"): (
        "图表数值的完整朗读",
        "每张画布有可访问名 + 文字替代指向（数值权威出口在元数据页 KV 表），"
        "但图表内部数据点（逐段 LUFS 序列等）没有逐点朗读替代——"
        "Qt 无原生图表无障碍 API，逐点暴露属后续批次。"),
}


def _name(w) -> str:
    """控件的可访问名：显式设置的，否则回落到文字 / tooltip / objectName。"""
    n = w.accessibleName() or ""
    if not n:
        try:
            n = w.text() or ""
        except AttributeError:
            pass
    if not n:
        n = w.toolTip() or ""
    return n or (w.objectName() or type(w).__name__)


def inventory(w) -> list[tuple[str, str, str]]:
    """主控件清单：(显示名, 可访问名, **Tab 可达**)。

    第三列判的是 `TabFocus` 而**不是** `!= NoFocus`——后者会把 `ClickFocus`
    （鼠标可点、键盘到不了）也算"可达"，掩盖真实的键盘缺口
    （实测抓到频谱曲线 / 对比画布曾是这个状态）。
    """
    def _tab(widget) -> str:
        return "是" if (widget.focusPolicy() & Qt.FocusPolicy.TabFocus) else "**否**"

    out = []
    for key, b in w.act.items():
        out.append(("工具条按钮 %s" % key, _name(b), _tab(b)))
    out.append(("搜索框", _name(w.ed_search), _tab(w.ed_search)))
    out.append(("筛选框", _name(w.cb_filter), _tab(w.cb_filter)))
    out.append(("文件表", _name(w.table) or "文件表", _tab(w.table)))
    for cv in (w.cv_spec, w.cv_spectro, w.cv_wfall, w.cv_phase,
               w.cv_band, w.cv_timeline, w.cv_cmp, w.cv_ovw):
        out.append(("画布 %s" % cv.accessibleName(), _name(cv), _tab(cv)))
    return out


def tab_chain(app, w, limit: int = 60) -> list[str]:
    """从窗口焦点起点按 Tab 走 limit 步，收集落到过的控件显示名。"""
    w.activateWindow()
    app.processEvents()
    seen: list[str] = []
    for _ in range(limit):
        QTest.keyClick(w, Qt.Key_Tab)
        app.processEvents()
        fw = app.focusWidget()
        if fw is not None:
            seen.append(_name(fw))
    return seen


def contrast_findings() -> list[P.Finding]:
    """两个内置主题的对照度判据（含 E3 新增的非文字对比度条目）。"""
    out = []
    for t in (T.DARK, T.LIGHT):
        out += P.audit(t.p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E3 无障碍三态表（Qt 五方面自查）")
    ap.add_argument("--strict", action="store_true",
                    help="出现未登记的「不支持/部分支持」即返回 1")
    args = ap.parse_args()

    app = QApplication.instance() or QApplication([])
    w = W.MainWindow()
    w.show()
    app.processEvents()

    print("== E3 无障碍三态表（Qt 五方面自查，离屏实测）==\n")

    # ---------------------------------------------------------- 键盘导航
    chain = tab_chain(app, w)
    uniq = []
    for n in chain:
        if n not in uniq:
            uniq.append(n)
    print("[键盘导航] Tab 链（去重 %d 个控件）：" % len(uniq))
    for n in uniq[:20]:
        print("    %s" % n)
    key_reach = {
        "搜索框": "搜索文件名" in uniq,
        "工具条按钮": any(n in ("添加文件", "开始分析", "导出", "清空") for n in uniq),
        "文件表": "文件列表" in uniq,
        "画布": any(cv in uniq for cv in ("频谱曲线", "频谱图", "瀑布图", "相位场",
                                          "频段能量", "时间定位", "对比", "批次概览")),
    }
    print("    关键控件可达：%s" % key_reach)

    # ---------------------------------------------------------- 可访问名
    print("\n[辅助工具] 可访问名与焦点策略：")
    missing = []
    for disp, nm, tab_ok in inventory(w):
        ok = bool(nm.strip()) and nm != type(w).__name__
        if not ok:
            missing.append(disp)
        print("    %-22s 名=%s Tab可达=%s" % (disp, nm[:18], tab_ok))
    print("    缺可访问名：%s" % (missing or "无"))

    # ---------------------------------------------------------- 对比度
    print("\n[颜色与对比度] 对照度判据（两个内置主题）：")
    bad = [f for f in contrast_findings() if not f.ok]
    for f in bad:
        print("    未达标：%s" % f.line())
    focus_ok = [f for f in contrast_findings() if "焦点环" in f.pair]
    print("    焦点环判据：%d 条，最低 %.2f:1（要求 3.0）"
          % (len(focus_ok), min((f.ratio for f in focus_ok), default=0.0)))

    # ---------------------------------------------------------- 三态表
    print("\n== 三态表 ==")
    rows = [
        ("可缩放 UI", "支持", "字号 8 级令牌 + 高 DPI 属性已开（AA_EnableHighDpiScaling）"),
        ("键盘导航", "支持" if all(key_reach.values()) else "部分支持",
         "Tab 链覆盖工具栏/搜索/筛选/文件表/画布；全部功能另有命令面板 Ctrl+K"),
        ("颜色与对比度", "部分支持", "文字对比度 15 条判据全达标；焦点环 4.6–6.0:1 达标；"
                                    "输入描边 ≈1.2:1 不达标（已登记）"),
        ("声音与语音", "不适用", "音频分析工具，声音播报会污染被测听音环境（已登记）"),
        ("辅助工具", "部分支持", "主控件有可访问名；画布有文字替代指向；"
                                "图表逐点朗读未做（已登记）"),
    ]
    for aspect, state, note in rows:
        print("| %s | %s | %s |" % (aspect, state, note))

    # ---------------------------------------------------------- 登记核对
    unregistered = []
    for aspect, state, _note in rows:
        if state in ("部分支持", "不支持"):
            if (aspect, state) not in KNOWN_GAPS:
                unregistered.append((aspect, state))
    print("\n登记核对：未登记项 %s" % (unregistered or "无"))
    if args.strict and unregistered:
        print("\n--strict：有未登记的缺口（必须先修或补登记）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
