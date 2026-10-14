"""B1 排版：字距 / 行高 / 混排 / 字体族 —— 每条都是**可测量**的判据。

## 为什么这些测试存在

B 块把"排版"从审美变成参数。但参数化只有配上判据才有意义：
否则令牌表会变成一份没人遵守的装饰文档（本项目在 4px 栅格上已经演过一遍——
令牌写着 4px，代码里 9 个边距有 5 个不合规）。

所以这里逐条钉的是"令牌存在、单调合理、**并且真的被 QSS / 控件用上了**"。
最后一条最关键：`test_qss_uses_only_declared_tracking` 扫的是生成出来的
QSS 文本，不是令牌表本身——令牌表自证清白没有任何意义。

## 三条 Qt 实测结论（改代码前先知道，都验证过）

1. QSS 的 `letter-spacing` **有效**（2px 让 12 个字符宽 24px）→ 字距走 QSS；
2. QSS 的 `line-height` **被静默忽略**（写 60px 控件高度一点不变，且不告警）
   → 行高走 `theme.rich_text()` 的富文本路径；
3. QSS 的 `font-variant-numeric` **被静默忽略** → 不写它；"数字等宽"靠字体
   本身保证并**量推进宽度**验证（见 `test_aligned_numbers_are_actually_mono`）。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtGui import QFontMetricsF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])   # 必须先有 app：见 test_ui_fonts 的坑
from audiocheck.ui import theme as T  # noqa: E402

T.init_fonts(APP)        # 把运行时解析出的中文字体族写进令牌，否则量到的是回退字体的尺寸

UI = os.path.join(_SRC, "audiocheck", "ui")
#: 参与扫描的文件。与 test_ui_grid.py 同一份名单，加 theme.py（令牌与 QSS 都在那）
FILES = ["window.py", "widgets.py", "charts.py", "chrome.py", "theme.py"]

#: 字阶的 8 个等级名（与 `Type` / `Tracking` / `Leading` 的字段一一对应）
LEVELS = ("micro", "caption", "body_sm", "body", "body_md", "title", "heading", "display")


def _code_lines(fn: str) -> list[str]:
    """去掉注释与 docstring 的行。**扫可见字符串必须去这两样**。

    第一次写这个扫描器时没去 docstring，结果测试把 `theme.py` 的说明文字
    （"全角空格是 1 em"）当成违规报出来——测试咬了自己的注释。
    与 `test_ui_grid.py::_lines_of` 同一个坑，同一个解法。
    """
    txt = open(os.path.join(UI, fn), encoding="utf-8").read()
    txt = re.sub(r'"""(?:.|\n)*?"""', '""', txt)          # 三引号 docstring
    txt = re.sub(r"'''(?:.|\n)*?'''", "''", txt)
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)       # CSS 块注释
    out = []
    for line in txt.splitlines():
        i = line.find("#")
        out.append(line if i < 0 else line[:i])
    return out


def _literals(fn: str) -> list[str]:
    """文件里所有**字符串字面量**的内容（去注释与 docstring 之后）。

    为什么只扫字面量：混排规则管的是"用户会看到的字"。
    注释里写"中文 + 空格 + 英文"是文档，不是界面——混进去会造出几百个假阳性
    （实测：不筛字面量时 window/widgets/charts 三文件报 233 处，筛完 0 处）。
    """
    lits: list[str] = []
    pat = re.compile(r'"((?:[^"\\\n]|\\.)*)"' r"|'((?:[^'\\\n]|\\.)*)'")
    for line in _code_lines(fn):
        for m in pat.finditer(line):
            lits.append(m.group(1) if m.group(1) is not None else m.group(2))
    return lits


class TestTrackingTokens(unittest.TestCase):
    """字距令牌：存在、随字号收紧、并且**只有表内的值能进 QSS**。"""

    def test_tracking_tightens_as_size_grows(self):
        """光学尺寸规律：字号越大，字距越紧（单调不增）。

        为什么允许相等：14/15px 两级的字距都是 0——那个区间里再收紧
        会影响中文识读（汉字没有词间空格可依托）。
        """
        tk = T.Tracking()
        vals = [getattr(tk, n) for n in LEVELS]
        for a, b in zip(vals, vals[1:]):
            self.assertGreaterEqual(a, b, "字距没有随字号收紧：%s" % list(zip(LEVELS, vals)))
        self.assertLess(vals[0], 1.0, "最小字号也不该放到 1px 以上")
        self.assertGreaterEqual(vals[-1], -0.5, "最大字号收得太紧会并字")

    def test_caps_tracking_is_in_buttericks_band(self):
        """全大写微标签的字距须落在 Butterick 的 5–12% 区间内（取 8%）。"""
        tk = T.Tracking()
        self.assertGreaterEqual(tk.caps_pct, 0.05)
        self.assertLessEqual(tk.caps_pct, 0.12)
        for size in (10.0, 11.0, 12.0):
            got = tk.caps(size)
            self.assertAlmostEqual(got, round(size * tk.caps_pct, 2), places=2)
            self.assertGreater(got, 0, "全大写不该收紧字距")

    def test_qss_uses_only_declared_tracking(self):
        """QSS 里的 letter-spacing 必须来自令牌表（或 caps 计算结果）。

        扫的是**生成出来的 QSS 文本**——令牌表自己说自己被用了不算数。
        """
        tk = T.Tracking()
        allowed = {float(getattr(tk, n)) for n in LEVELS}
        allowed |= {tk.caps(s) for s in (T.Type().micro, T.Type().caption)}
        for theme in (T.DARK, T.LIGHT):
            q = T.qss(theme)
            used = {float(v) for v in re.findall(r"letter-spacing:([-\d.]+)px", q)}
            self.assertTrue(used, "QSS 里一个 letter-spacing 都没有——令牌没接上")
            extra = used - allowed
            self.assertEqual(extra, set(),
                             "QSS 出现了令牌表之外的字距：%s（允许 %s）"
                             % (sorted(extra), sorted(allowed)))

    def test_no_handwritten_letter_spacing_in_widgets(self):
        """控件里不许出现手写字距常数（上一轮的那种 `.6px`）。"""
        bad = []
        for fn in FILES:
            for i, line in enumerate(_code_lines(fn), 1):
                for m in re.finditer(r"letter-spacing:\s*([-\d.]+)", line):
                    bad.append("%s:%d  letter-spacing:%s" % (fn, i, m.group(1)))
        self.assertEqual(bad, [], "部件里出现了手写字距：\n" + "\n".join(bad))


class TestLeadingTokens(unittest.TestCase):
    """行高令牌：QSS 到不了那里，所以判据是"表合理 + rich_text 真的用它"。 """

    def test_leading_is_organized_by_purpose_not_by_size(self):
        """行高按**用途**分工，不是字号的单调函数。

        为什么不断言单调：正文（body 14px）是唯一需要长时间连续阅读的层级，
        它的行高必须最松；而 micro/caption 是**单行标签**，行高松紧看不出差别，
        给 1.4 就够。硬凑成"字号越大行高越紧"的单调表，会把正文的行高压下去——
        那是拿测试的整齐换阅读体验。

        所以这里断言的是三条真实的契约：
          1. 所有值落在合理区间（1.10–1.70）；
          2. 正文级（body / body_sm）是**最松**的一档；
          3. display 级最紧（它是一个大数字，不是一段文字）。
        """
        ld = T.Leading()
        vals = {n: getattr(ld, n) for n in LEVELS}
        for n, v in vals.items():
            self.assertGreaterEqual(v, 1.10, "%s 行高 %.2f 太紧" % (n, v))
            self.assertLessEqual(v, 1.70, "%s 行高 %.2f 太松" % (n, v))
        reading = max(vals["body"], vals["body_sm"])
        for n, v in vals.items():
            if n not in ("body", "body_sm"):
                self.assertLessEqual(v, reading, "%s 比正文还松" % n)
        self.assertEqual(min(vals.values()), vals["display"], "display 不是最紧的一档")

    def test_body_leading_matches_the_cjk_judgment(self):
        """正文行高落在 1.45–1.6（Butterwick 120–145% + CJK 放宽的注明判断）。"""
        ld = T.Leading()
        self.assertGreaterEqual(ld.body, 1.45)
        self.assertLessEqual(ld.body, 1.60)
        self.assertGreaterEqual(ld.body_sm, 1.45)

    def test_rich_text_emits_the_leading_token(self):
        """`rich_text` 必须把表里的值写出去（否则表还是没有落点）。"""
        for level in LEVELS:
            want = round(getattr(T.Leading(), level) * 100)
            out = T.rich_text("两行<br>文本", level)
            self.assertIn("line-height:%d%%" % want, out,
                          "%s 级的行高没进 rich_text：%s" % (level, out))

    def test_rich_text_rejects_unknown_level(self):
        """等级名写错必须立刻炸，不能静默用默认值（那会让"用了令牌"变成假话）。"""
        with self.assertRaises(KeyError):
            T.rich_text("x", "display_large")


class TestCjkLatinMix(unittest.TestCase):
    """中西文混排：间隙 ≤1/4 汉字宽（clreq），且不许手打全角空格。"""

    THIN = T.THIN_SPACE

    def test_helper_inserts_thin_space_only_at_the_boundary(self):
        self.assertEqual(T.mix("真峰值", "ITU-R"), "真峰值%sITU-R" % self.THIN)
        self.assertEqual(T.mix("采样率", "44.1 kHz"), "采样率%s44.1 kHz" % self.THIN)
        self.assertEqual(T.mix("中文", "继续"), "中文继续")       # 两段都中文：不插
        self.assertEqual(T.mix("kHz", "真实截止"), "kHz%s真实截止" % self.THIN)
        self.assertEqual(T.mix("", "x"), "x")                     # 空段不插

    def test_thin_space_is_within_the_quarter_em_band(self):
        """间隙字符 ≤1/4 汉字宽；**机制是 U+00A0，不是 U+2009**（B14 §4-② 重标定）。

        ## 为什么改（报数，不是静默调阈值）

        B1 定这条时的事实：YaHei 下普通空格 **0.316 em（超 1/4）**、thin space
        约 1/5 em（达标）⇒ 旧断言写成"普通空格必须超标，否则 thin space 没有存在理由"。

        B14 换成 IBM Plex + 思源后**逐字符 × 逐族实测**（`out/_b14_spaces.py`）：

        | 字符 | Plex | Noto |
        |---|---|---|
        | U+0020 | 0.211 ✓ | 0.211 ✓ |
        | U+00A0 | 0.211 ✓ | 0.211 ✓ |
        | U+2009 | 0.105 ✓ | **0.579 ✗** |
        | U+3000 | 1.000 ✗ | 1.000 ✗ |

        ⇒ 两条同时变：① **普通空格达标了**（0.211 ≤ 0.25），旧断言的前提消失；
        ② **U+2009 反而不可用**——混排处的字形由 CJK 族（Noto）提供，而 Noto 把
        thin space 映射到 0.579 em（超上限 2.3 倍）。
        ⇒ 新机制 **U+00A0**：两个族都 0.211 em，且**不断行**（"44.1 kHz" 不被折行
        拆开）。它的存在理由从"更窄"变成"**源码里可辨 + 不断行**"——普通空格在
        源码里不可见，那正是 B1 踩过的坑（`"44.1 kHz"` 与 `"44.1<U+00A0>kHz"`
        长得一样，折行行为不同）。

        **阈值（1/4 汉字）没有动**，动的是字符——这就是"按字体重标定"。
        """
        f = T.pick_text_font("音", 14.0, T.ui_family())
        m = QFontMetricsF(f)
        han = m.horizontalAdvance("音")
        thin = m.horizontalAdvance(self.THIN)
        plain = m.horizontalAdvance(" ")
        self.assertGreater(han, 0)
        self.assertEqual(self.THIN, "\u00a0",
                         "间隙字符必须是 U+00A0（B14 §4-② 按 Plex/Noto 重标定）")
        self.assertLessEqual(thin, han / 4.0 + 1e-6,
                             "间隙字符 %.3f em 超出 1/4 em" % (thin / han))
        # 新凭据：U+00A0 与 U+0020 **等宽**（实测都 0.211 em）——所以"更窄"
        # 不再是它的存在理由；理由换成"源码可辨 + 不断行"（见 docstring）。
        self.assertAlmostEqual(thin / han, plain / han, places=2,
                               msg="U+00A0 与普通空格不再等宽了？重跑 "
                                   "out/_b14_spaces.py 再定机制")

    def test_no_fullwidth_space_or_double_space_in_visible_strings(self):
        """可见字符串里不许有全角空格，或**紧邻中文**的连续半角空格。

        为什么给"连续空格"加中文邻接条件：`"%s    %s"` 这类格式串是刻意的
        视觉对齐（图表读数、表格列），不是混排间隙——不加条件会误伤
        （实测 4 处全是正当用法）。全角空格则一律不许，没有正当用途。
        """
        bad = []
        for fn in FILES:
            for s in _literals(fn):
                if "\u3000" in s:
                    bad.append("%s 全角空格：%r" % (fn, s[:40]))
                if re.search(r"(?<=[\u4e00-\u9fff])  |  (?=[\u4e00-\u9fff])", s):
                    bad.append("%s 中文旁连续空格：%r" % (fn, s[:40]))
        self.assertEqual(bad, [], "手打了混排间隙：\n" + "\n".join(bad[:10]))

    def test_visible_labels_have_no_plain_space_across_the_boundary(self):
        """可见字符串里不许有「中文 + 普通空格 + 西文/数字」（该用 `mix()`）。"""
        pat = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef] [A-Za-z0-9]")
        bad = []
        for fn in FILES:
            for s in _literals(fn):
                for m in pat.finditer(s):
                    bad.append("%s  %r" % (fn, s[max(0, m.start() - 6):m.end() + 8]))
        self.assertEqual(bad, [], "中文与西文之间用了普通空格（0.316 em > 1/4 em）：\n"
                         + "\n".join(bad[:10]))


class TestNoFullwidthAscii(unittest.TestCase):
    """全角 ASCII 清零：全角字母数字与全角空格一律不许出现。

    口径必须收窄到**字母数字 + 全角空格**：中文标点（，。：；！？（）《》）
    是 clreq 允许甚至要求的，一把全会造出两千个假阳性
    （实测含标点的宽口径：cli/charts/palette 等 30 个文件共 2000+ 处）。
    """

    PATTERN = re.compile(r"[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a\u3000]")
    #: 显式登记白名单。当前为空——现存代码本来就是干净的（实测 0 处）。
    WHITELIST: tuple[str, ...] = ()

    def _scan(self):
        hits = []
        for base, _dirs, files in os.walk(os.path.join(_SRC, "audiocheck")):
            if "__pycache__" in base:
                continue
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(base, fn)
                txt = open(p, encoding="utf-8").read()
                # **必须去注释与 docstring**：`theme.py` 的说明文字里就写着
                # "全角空格（U+3000）是 1 em"——不筛的话测试会把自己的讲解
                # 当成违规报出来（第一次跑就这么死的）。
                txt = re.sub(r'"""(?:.|\n)*?"""', '""', txt)
                txt = re.sub(r"'''(?:.|\n)*?'''", "''", txt)
                txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
                for i, line in enumerate(txt.splitlines(), 1):
                    j = line.find("#")
                    if j >= 0:
                        line = line[:j]
                    if self.PATTERN.search(line):
                        hits.append("%s:%d  %r" % (os.path.relpath(p, _SRC), i,
                                                   line.strip()[:60]))
        return hits

    def test_no_fullwidth_ascii(self):
        bad = [h for h in self._scan() if not any(w in h for w in self.WHITELIST)]
        self.assertEqual(bad, [], "出现了全角 ASCII：\n" + "\n".join(bad[:10]))


class TestNoDecorativeFonts(unittest.TestCase):
    """装饰字体永久关闭（用户 2026-10-13 判决："一股老登味道"）。

    白名单为空：一个都不许有。扫的是**代码里出现的族名**，不是系统装了没有。
    """

    FORBIDDEN = (
        "STXingkai", "STKaiti", "STLiti", "STCaiyun", "STHupo", "STZhongsong",
        " brush", "Brush Script", "Freestyle Script", "Mistral", "Kristen ITC",
        "Segoe Script", "Lucida Handwriting", "Ink Free", "Comic Sans",
    )
    WHITELIST: tuple[str, ...] = ()

    def _scan(self):
        hits = []
        for base, _dirs, files in os.walk(os.path.join(_SRC, "audiocheck")):
            if "__pycache__" in base:
                continue
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(base, fn)
                txt = open(p, encoding="utf-8").read()
                low = txt.lower()
                for name in self.FORBIDDEN:
                    if name.lower() in low:
                        hits.append("%s 出现 %s" % (os.path.relpath(p, _SRC), name))
        return hits

    def test_no_decorative_font_families(self):
        bad = [h for h in self._scan() if not any(w in h for w in self.WHITELIST)]
        self.assertEqual(bad, [], "出现了装饰字体：\n" + "\n".join(bad))


class TestFigureFamily(unittest.TestCase):
    """DIN 系西文字体：解析得到、**并且不与等宽职责混淆**。"""

    def test_figure_family_resolves_or_is_empty(self):
        fam = T.figure_family()
        if fam:
            self.assertIn(fam, T.FIGURE_STACK, "解析出栈外的族：%r" % fam)
        # 空串也是合法结果（机器没装 DIN 系）——调用方会退回默认族

    def test_aligned_numbers_are_actually_mono(self):
        """要对齐的数字必须走等宽：**量推进宽度**，不是看族名。

        这条挡的是一个真实的误判：排版依据文档写着"DIN 系 tabular 等宽、
        上下对齐成列"，但本机实测 Bahnschrift 的数字是**比例**的
        （24pt 下 `111`=33px 而 `888`=54px）。照那句话把 DIN 系用在数字列上，
        "沿竖线对齐"就没了——而界面看起来完全正常。
        """
        f = T.mono_qfont(14.0)
        m = QFontMetricsF(f)
        widths = {s: m.horizontalAdvance(s) for s in ("111", "888", "000", "444")}
        self.assertEqual(len(set(widths.values())), 1,
                         "等宽族的数字推进不一致：%s" % widths)

    def test_figure_font_is_not_used_for_aligned_values(self):
        """`pick_text_font` 对纯 ASCII 给的必须是等宽（对齐职责不放给 DIN）。"""
        for text in ("44.1 kHz", "-14.5 dB", "0.000%"):
            f = T.pick_text_font(text, 12.0)
            self.assertTrue(f.fixedPitch() or f.styleHint() == T.QFont.Monospace,
                            "%r 没走等宽" % text)

    def test_theme_figure_is_a_single_family(self):
        """`Theme.figure` 只写一族名（QSS 不支持多族回退链）。

        挡的是 `'""Microsoft YaHei UI",sans-serif",sans-serif'` 那种双层包裹——
        QSS 遇到解析不了的族名会**整条规则静默失效**（本项目踩过）。
        """
        for theme in (T.DARK, T.LIGHT):
            val = theme.figure
            self.assertEqual(val.count('"'), 2, "不是单族名：%r" % val)
            self.assertNotIn('","', val, "出现了多族链（假回退）：%r" % val)


class TestTypeScaleContract(unittest.TestCase):
    """字阶与 Butterick / "不要大字"两条判决的落点。"""

    def test_body_is_at_the_cjk_floor(self):
        """正文 14px：Butterick 网页原文是 15–25px（西文），CJK 折取下限 14。"""
        self.assertEqual(T.Type().body, 14.0)
        self.assertEqual(T.Type().body_md, 15.0)

    def test_display_level_is_used_in_exactly_two_places(self):
        """"不要大字"：display 级（27px）只允许**两处**确有其事的读数。

        用户判决"左上角原来那个项目英文名就行了"——chrome 与报告头都不许升级。

        B9.1 从一处变两处：判定区的**判词**（`#VerdictText`）也升到了 display。
        理由是它和指标数值是同一类东西——**一屏最重要的读数**，不是标题。
        小样里判词写的是 26px，与 display 的 27 同量级。
        再往下加第三处就该有人问"它凭什么是大字"了，所以这里把数字钉死。
        """
        for theme in (T.DARK, T.LIGHT):
            q = T.qss(theme)
            q = re.sub(r"/\*.*?\*/", "", q, flags=re.S)     # CSS 注释不算选择器
            users = [sel.strip() for sel, body in
                     re.findall(r"([^{}]+)\{([^{}]*)\}", q)
                     if "font-size:%dpx" % int(T.Type().display) in body]
            self.assertEqual(users, ["QLabel#VerdictText", "QLabel#Hero"],
                             "display 级的用法变了：%s" % users)


if __name__ == "__main__":
    unittest.main(verbosity=2)
