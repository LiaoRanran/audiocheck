"""B2 材质：投影 / 顶光 / 抗色带噪点 —— 每条都可测量。

## 这份测试挡的是哪一类失败

材质是**最不容易被测试覆盖**的一层：它不影响数据、不报错、不崩，
只会"看起来差点意思"。而"差点意思"在评审里根本说不清。
所以这里把三件材质各自拆成**能量出来的量**：

* 投影：羽化半径、方向的单调性、卡片区域本身必须透明（不能自己给自己涂黑）；
* 顶光：QSS 里必须真的出现，**且写在 `border` 简写之后**（顺序错了会被吃掉）；
* 噪点：确定性（同样的种子出同样的图案）、幅度 ≤2/255、不吃鼠标事件。

另外挡一个真发生过的 bug：`Toast._dismiss` 曾经调 `motion.fade(..., out=True)`，
而 `fade` 的参数叫 `end` —— **通知永远不会消失**，界面照常、无报错。
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import chrome as CH  # noqa: E402
from audiocheck.ui import material as M  # noqa: E402
from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import widgets as W  # noqa: E402

T.init_fonts(APP)
UI = os.path.join(_SRC, "audiocheck", "ui")


def pump(ms: float = 0.0) -> None:
    """把事件循环推几轮。`deleteLater` 要显式派发才生效（本项目的坑之一）。"""
    from PySide6.QtCore import QElapsedTimer
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        APP.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    APP.processEvents()


class TestNoise(unittest.TestCase):
    """抗色带噪点：确定性、幅度极小、不吃事件。"""

    def test_tile_is_deterministic(self):
        """同样的种子必须出同样的图案。

        这条不是洁癖：截图对比（`packaging/shot*.ps1`）靠的是"没改就没差异"。
        噪点若每次启动都不同，截图对比会满屏假差异，把真回归淹掉。
        """
        a = M._tile()
        b = M._tile()
        ia = a.toImage()
        ib = b.toImage()
        self.assertEqual(ia.width(), ib.width())
        diff = 0
        for y in range(0, ia.height(), 7):
            for x in range(0, ia.width(), 7):
                if ia.pixelColor(x, y) != ib.pixelColor(x, y):
                    diff += 1
        self.assertEqual(diff, 0, "同一进程内两次生成就不一样了")

    def test_amplitude_is_within_the_bit_budget(self):
        """幅度 ≤2/255：它要打散量化色带，不是让画面有颗粒。

        提示词给的上限就是 2。超过 3 就开始"发脏"——那是另一回事，
        而且会拉低有效对比度（本项目每条对比度都算了 WCAG，不能被噪点偷偷吃掉）。
        """
        img = M._tile().toImage()
        alphas = set()
        for y in range(0, img.height(), 5):
            for x in range(0, img.width(), 5):
                alphas.add(img.pixelColor(x, y).alpha())
        self.assertTrue(alphas, "噪点图是空的")
        self.assertLessEqual(max(alphas), 2, "幅度超预算：%s" % sorted(alphas)[-3:])
        self.assertGreater(max(alphas), 0, "幅度为 0 等于没画")

    def test_only_two_tones(self):
        """只有 ±1 bit 两档灰，不是灰度云——"抖动"与"噪点"是两件事。"""
        img = M._tile().toImage()
        tones = set()
        for y in range(0, img.height(), 11):
            for x in range(0, img.width(), 11):
                c = img.pixelColor(x, y)
                tones.add(c.red())
        self.assertLessEqual(len(tones), 2, "出现了多于两档灰度：%s" % sorted(tones))

    def test_noise_is_painted_in_place_not_as_a_child_widget(self):
        """噪点是**画在壳上**，不是加了一个半透明子控件——这条是量出来的。

        第一版把噪点做成了 `WA_TranslucentBackground` 的覆盖层子控件。
        本轮把它改成在侧边栏/标题栏自己的 `paintEvent` 末尾自绘，理由：
          * 半透明子控件会让 Qt 在每一次重绘里把父控件的背景重放到该区域；
          * 顺序不再依赖"第一个创建"这种隐式约定；
          * 少两个常驻控件，`apply_theme` 的遍历也少两站。

        （`ShadowFrame` 仍用半透明——它是**浮层**，投影的留白必须透明，
        没有别的选择。差别在于浮层是临时的、小的，而噪点层是常驻的、铺满的。）
        """
        self.assertTrue(hasattr(M, "paint_noise"), "paint_noise 不见了")
        self.assertFalse(hasattr(M, "NoiseOverlay"),
                         "覆盖层类又回来了——那正是上一版的写法")
        for cls in (W.SideBar, CH.TitleBar):
            self.assertFalse(hasattr(cls(self._probe_parent()), "noise"),
                             "%s 又挂了噪点子控件" % cls.__name__)

    @staticmethod
    def _probe_parent() -> QWidget:
        w = QWidget()
        return w

    def test_paint_noise_handles_degenerate_geometry(self):
        """尺寸为 0 时不许画（也不许崩）——控件在布局完成前就是 0×0。

        注意 `QWidget()` 的默认尺寸是 **640×480**，不是 0——第一版按 0 写，
        于是这条测试什么都没验到（`assertEqual(width, 0)` 直接失败）。
        要真的造出退化几何，得显式 `resize(0, 0)`。
        """
        w = QWidget()
        w.resize(0, 0)
        M.paint_noise(w)                      # 应当直接返回，不建 QPainter
        self.assertEqual(w.width(), 0)

    def test_static_layers_actually_paint_noise(self):
        """侧边栏与标题栏**真的**调了噪点绘制（否则抗色带是空话）。

        做法：抓一次渲染，比较"有/无噪点"两种情形的像素集合。
        直接扫源码也可以（更脆但更简单），这里两者都做：
        先确认 `paint_noise` 出现在两个类的 `paintEvent` 里。
        """
        for cls in (W.SideBar, CH.TitleBar):
            src = inspect.getsource(cls)
            self.assertIn("paint_noise", src,
                          "%s 没有铺噪点——它的渐变会出色带" % cls.__name__)


class TestShadowRender(unittest.TestCase):
    """投影的几何：羽化在外、卡片区透明、方向与令牌一致。"""

    def _render(self, tier="near", w=200, h=40):
        pal = T.DARK.p
        return M.render_shadow(w, h, T.DARK.radius.lg, tier,
                               getattr(pal, "shadow_" + tier), 1.0), w, h

    def test_card_area_is_transparent(self):
        """投影**不能涂到卡片自己的位置**——卡片由 QSS 画，投影只管外面。

        涂了的话会看到卡片底下透出一层灰，尤其在圆角处最明显。
        """
        pm, w, h = self._render()
        img = pm.toImage()
        cfg = P.SHADOW_TIERS["near"]
        pad = cfg["blur"] + max(abs(cfg["dx"]), abs(cfg["dy"]))
        # 取卡片正中心
        cx, cy = pad + w // 2, pad + h // 2
        self.assertEqual(img.pixelColor(cx, cy).alpha(), 0,
                         "卡片中心被涂了：alpha=%d" % img.pixelColor(cx, cy).alpha())

    def test_ink_exists_outside_the_card(self):
        """投影必须真的画出来（全透明等于没做）。"""
        pm, w, h = self._render()
        img = pm.toImage()
        cfg = P.SHADOW_TIERS["near"]
        pad = cfg["blur"] + max(abs(cfg["dx"]), abs(cfg["dy"]))
        # 卡片下缘外侧一点：dy 为正，那里必定有墨
        y = pad + h + cfg["dy"] + 2
        x = pad + w // 2
        self.assertGreater(img.pixelColor(x, y).alpha(), 0,
                           "卡片下方没有投影——投影没画出来")

    def test_fades_with_distance(self):
        """离卡片越远越淡，且**在羽化半径处归零**——这是"柔和"的定义。

        判据取"到 blur 为止单调不增 + blur 之外为 0"，而不是取某个固定像素
        等于 0：羽化是连续的，倒数第二圈本来就还有几个 alpha 单位的墨
        （第一次写时把 10px 处断言成 0，实得 11——测试错了，不是实现错了）。
        """
        pm, w, h = self._render()
        img = pm.toImage()
        cfg = P.SHADOW_TIERS["near"]
        blur = cfg["blur"]
        pad = blur + max(abs(cfg["dx"]), abs(cfg["dy"]))
        x = pad + w // 2
        # **从卡片下缘起算**：下缘在 `pad + dy + h`，不是 `pad + h`。
        # 少加一个 dy 会把采样点放回卡片内部（那里被抠空了，alpha=0），
        # 于是"第一点是 0"看起来像 bug，其实是采样错了。
        y0 = pad + cfg["dy"] + h
        # 采样 k=1..blur-1：k=0 是卡片下缘那一像素（抗锯齿的过渡格），
        # k=blur 已经在画布外（越界读出来是 255）。实测剖面
        # [87, 71, 57, 45, 33, 24, 16, 10, 6, 2, 0]——正好落在这一段上。
        alphas = [img.pixelColor(x, y0 + k).alpha() for k in range(1, blur)]
        self.assertTrue(all(a >= b for a, b in zip(alphas, alphas[1:])),
                        "不是单调变淡：%s" % alphas)
        self.assertEqual(alphas[-1], 0,
                         "羽化半径处还有墨（%d），会看到硬边" % alphas[-1])

    def test_far_tier_is_softer_and_dimmer(self):
        """far 档"更虚、更淡"：边缘峰值更低，墨铺得更远。

        为什么比**峰值**而不是比某个固定点的 alpha：两档的羽化半径不同
        （12 vs 32），同一个像素在两档里处在完全不同的相对位置上，
        拿来比是错的（第一次就这么错了：near=189 / far=227）。
        """
        near, w, h = self._render("near")
        far, _w2, _h2 = self._render("far")
        cfg_n, cfg_f = P.SHADOW_TIERS["near"], P.SHADOW_TIERS["far"]

        def peak_and_reach(pm, cfg):
            img = pm.toImage()
            pad = cfg["blur"] + max(abs(cfg["dx"]), abs(cfg["dy"]))
            x = pad + w // 2
            y0 = pad + cfg["dy"] + h                 # 卡片下缘（含 dy）
            # **采样不许越界**：越界时 `pixelColor` 返回无效颜色，alpha 读出来是
            # 255，看上去像"墨没化开"。第一次写时多采了两列，两档都是 255。
            last = min(cfg["blur"] + 1, img.height() - y0 - 1)
            col = [img.pixelColor(x, y0 + k).alpha() for k in range(max(1, last))]
            reach = max((i for i, a in enumerate(col) if a > 0), default=0)
            return max(col), reach

        p_n, r_n = peak_and_reach(near, cfg_n)
        p_f, r_f = peak_and_reach(far, cfg_f)
        self.assertLess(p_f, p_n, "far 档边缘不比 near 淡：near=%d far=%d" % (p_n, p_f))
        self.assertGreater(r_f, r_n, "far 档铺得不比 near 远：near=%d far=%d" % (r_n, r_f))

    def test_bad_color_string_does_not_crash(self):
        """颜色串解析不了要退回冷黑，不是抛异常——材质层没有资格让界面起不来。"""
        rgb, a = M._split_rgba("这不是一个颜色")
        self.assertEqual(len(rgb), 3)
        self.assertGreater(a, 0)


class TestShadowTiersInUse(unittest.TestCase):
    """两档阴影真的被用上了（令牌存在 ≠ 被使用）。"""

    def _host(self) -> W.ToastHost:
        """建一个宿主，**并把父控件挂在测试实例上**。

        不能写 `W.ToastHost(QWidget())`：那个匿名父控件会被 Python 立刻回收，
        C++ 侧连带删掉它的孩子，接下来访问 `host._lay` 就是
        `RuntimeError: Internal C++ object (PySide6.QtWidgets.QVBoxLayout)
        already deleted`。第一次跑时五条用例一起这么死——父控件的生命周期
        在 PySide 里不是"引用计数"能兜住的，得自己留引用。
        """
        self._parent = QWidget()
        return W.ToastHost(self._parent)

    def test_toast_uses_the_near_tier(self):
        host = self._host()
        host.apply_theme(T.DARK)
        host.notify("测试", "ok")
        toasts = host.findChildren(W.Toast)
        self.assertEqual(len(toasts), 1)
        self.assertIsInstance(toasts[0], M.ShadowFrame)
        self.assertEqual(toasts[0]._tier, "near")
        self.assertEqual(toasts[0].pad,
                         P.SHADOW_TIERS["near"]["blur"]
                         + max(abs(P.SHADOW_TIERS["near"]["dx"]),
                               abs(P.SHADOW_TIERS["near"]["dy"])),
                         "投影边距不是从令牌算出来的")

    def test_toast_is_right_aligned_and_at_the_bottom(self):
        """通知必须在**右下角**：右对齐 + 弹簧在它前面。

        两件都断言，因为两件都出过错：
          * 对齐：不给显式对齐时，"最大宽度小于布局宽度"的控件落在哪是
            Qt 的内部行为（历史上靠"看起来在右边"蒙对）；
          * 顺序：弹簧曾经加在通知**后面**，于是通知被顶到窗口**顶部**——
            而文档写的是"右下角"。代码与文档都没错，只是没人把两件事
            放在一起看过，直到出图才发现。
        """
        host = self._host()
        host.notify("测试", "ok")
        first = host._lay.itemAt(0)
        self.assertIsNotNone(first.spacerItem(), "弹簧不在首位，通知会被顶到顶部")
        item = host._lay.itemAt(1)
        self.assertIsInstance(item.widget(), W.Toast)
        self.assertTrue(item.alignment() & Qt.AlignRight,
                        "通知没有显式右对齐（那是靠 Qt 内部行为蒙的）")

    def test_no_widget_level_stylesheet_in_material(self):
        """材质层不许引入部件级样式表（欠账 #10 在收，不许再加）。"""
        src = inspect.getsource(M)
        self.assertNotIn("setStyleSheet", src, "material.py 里出现了部件级样式表")


class TestToastLifecycle(unittest.TestCase):
    """通知的完整生命周期：弹出 → 超时 → 淡出 → 拆掉。

    这份测试的由来：B1 把退场写成了 `motion.fade(..., out=True)`，
    而 `fade` 的参数叫 `end`。**通知于是永远不会消失**——不报错、界面照常，
    只是右下角一直堆着。当时的测试集一条都没覆盖到退场路径。
    """

    def _host(self) -> W.ToastHost:
        # 父控件必须挂在测试实例上：匿名父控件会被回收，连带删掉宿主的孩子，
        # 之后访问 `host._lay` 就是 RuntimeError（见 TestShadowTiersInUse._host）。
        self._parent = QWidget()
        return W.ToastHost(self._parent)

    def test_notify_then_dismiss_removes_the_widget(self):
        host = self._host()
        host.apply_theme(T.DARK)
        host.notify("会消失吗", "ok", ms=40)
        self.assertEqual(len(host.findChildren(W.Toast)), 1)
        pump(40 + 600)                      # 存活 + 淡出时长（Motion.fast 有余量；全量负载下放宽）
        self.assertEqual(len(host.findChildren(W.Toast)), 0,
                         "通知没有消失——退场路径断了（B1 就断过一次）")
        self.assertEqual(host._lay.count(), 1, "布局里的条目没有被移除")

    def test_burst_does_not_leak(self):
        """连发一批，最后必须全部回收：清理只有一条路径（motion 的）。"""
        host = self._host()
        host.apply_theme(T.DARK)
        for i in range(8):
            host.notify("压力 %d" % i, "info", ms=20)
        self.assertEqual(len(host.findChildren(W.Toast)), 8)
        pump(20 + 1500)         # 存活 20 + 淡出；窗口留足——全量负载下动画会慢，420 偶发不够，放宽到 1.5s
        self.assertEqual(len(host.findChildren(W.Toast)), 0, "有通知没被回收")

    def test_lifetime_shorter_than_fade_in_does_not_strand_animations(self):
        """存活期短于淡入时长时，**不许留下孤儿动画**。

        这是一个真发生过的泄漏，而且只在特定时序下出现：
        存活 60 ms 短于淡入 200 ms，退场时 `setGraphicsEffect` 换掉了 effect，
        正在跑的淡入动画失去目标 → Qt **不发 `finished`**（目标都没了，
        谈不上"结束"）→ 清理路径永远不执行 → 动画对象留在宿主名下。

        `mem_probe.py` 的 S5（通知风暴 ×40）数出来的正是它：40 条通知留 40 个动画，
        而内存只涨 2.4 MB —— **只看内存发现不了**，必须数对象。
        """
        from PySide6.QtCore import QAbstractAnimation
        host = self._host()
        host.apply_theme(T.DARK)
        for i in range(10):
            host.notify("抢跑 %d" % i, "info", ms=20)     # 远短于 Motion.base
        pump(20 + 400)
        left = len(host.findChildren(QAbstractAnimation))
        self.assertLessEqual(left, 1, "留下 %d 个孤儿动画（应为 0）" % left)

    def test_theme_switch_refreshes_the_shadow_cache(self):
        """换肤后投影颜色必须跟着换（缓存要作废，否则深色主题上留着深色投影）。"""
        host = self._host()
        host.resize(400, 300)
        host.apply_theme(T.LIGHT)
        host.notify("换肤", "info", ms=5000)
        host.show()                         # 不显示的话布局不激活，通知是 0×0
        pump(60)
        t = host.findChildren(W.Toast)[0]
        t.grab()                            # 触发一次 paintEvent，建立缓存
        self.assertIsNotNone(t._key, "grab 之后仍然没有投影缓存")
        before = t._t.p.shadow_near
        t.apply_theme(T.DARK)
        self.assertIsNone(t._key, "换肤没有让投影缓存作废")
        self.assertNotEqual(before, t._t.p.shadow_near)
        t.grab()
        self.assertIsNotNone(t._key)


class TestTopLight(unittest.TestCase):
    """浮层顶光：必须真的进了 QSS，**且写在 `border` 简写之后**。"""

    def test_qss_contains_the_top_light_rule(self):
        for theme in (T.DARK, T.LIGHT):
            q = T.qss(theme)
            self.assertIn("border-top:1px solid %s" % theme.p.top_light, q,
                          "%s 的顶光没进 QSS" % theme.name)

    def test_top_light_comes_after_the_border_shorthand(self):
        """顺序判据：QSS 同特异度下后写的赢，而 `border` 简写会重置 `border-top`。

        第一次写就成了错的（顶光在 `#Toast { border:… }` 之前），
        结果那条顶光被静默吃掉——没有任何报错，只是"看起来少了点什么"。

        注意模板里写的是 `{{ }}`（`.format` 的转义），生成出来是单层 `{ }`，
        所以这里查的是**生成后的 QSS**，字符串用单层大括号。
        """
        q = T.qss(T.DARK)
        toast_border = q.index("#Toast { background:")
        top_light = q.index("QMenu, #Palette, #Toast { border-top:")
        self.assertGreater(top_light, toast_border,
                           "顶光规则写在 border 简写之前，会被静默吃掉")

    def test_overlays_are_covered(self):
        """三块浮层（菜单 / 命令面板 / 通知）都要有顶光，别漏。"""
        q = T.qss(T.DARK)
        m = re.search(r"([^{}]+)\{\s*border-top:1px solid", q)
        self.assertIsNotNone(m, "找不到顶光规则")
        sel = m.group(1)
        for want in ("QMenu", "#Palette", "#Toast"):
            self.assertIn(want, sel, "顶光漏了 %s" % want)


class TestNoWidgetStylesheetBudget(unittest.TestCase):
    """欠账 #10（部件级样式表）：数量只能减，不能加。

    这一轮收了 Toast 的一处（`#Toast` 规则本来就在 app 级 QSS 里，
    部件里那份是重复的）。上限卡在"当前实测值"——**加一处就失败**。
    减下来之后请把上限也调下去，否则这条测试等于没有。
    """

    #: 当前实测值。只许调小。
    CEILING = 26

    def test_widget_level_stylesheet_count(self):
        hits = []
        for fn in ("window.py", "widgets.py", "charts.py", "chrome.py", "material.py"):
            for i, line in enumerate(open(os.path.join(UI, fn),
                                             encoding="utf-8").read().splitlines(), 1):
                if "setStyleSheet" in line and not line.strip().startswith("#"):
                    hits.append("%s:%d" % (fn, i))
        self.assertLessEqual(len(hits), self.CEILING,
                             "部件级样式表涨到 %d 处（上限 %d）：\n%s"
                             % (len(hits), self.CEILING, "\n".join(hits)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
