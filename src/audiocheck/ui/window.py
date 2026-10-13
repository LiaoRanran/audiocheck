"""主窗口：无边框外壳 + 侧边栏导航 + 内容栈 + 命令面板。

信息架构
--------
侧边栏负责"去哪"，内容栈负责"看什么"，文件表常驻在总览/各视图左侧，
保证任何时刻都能切换对象而不丢失上下文。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import BAND_CN, BANDS, Thresholds
from ..core.analyzers.metadata import brief as meta_brief
from ..core.analyzers.metadata import tag_rows as meta_rows
from ..core.engine import analyze as analyze_file
from ..core.engine import collect_files
from ..core.views import spectrogram as view_spectrogram
from ..core.views import waterfall as view_waterfall
from . import motion
from . import theme as T
from .charts import (
    BandCanvas,
    CompareCanvas,
    OverviewCanvas,
    SpectrogramCanvas,
    SpectrumCanvas,
    VerdictDelegate,
    WaterfallCanvas,
)
from .chrome import FramelessWindow
from .theme import Motion
from .widgets import (
    CommandPalette,
    EmptyState,
    KVTable,
    SeedDialog,
    SideBar,
    StatCard,
    ToastHost,
    VerdictPanel,
    fmt_num,
    human_khz,
    ratio_pct,
)

TH = Thresholds()
LEVEL_CN = T.LEVEL_CN


# ------------------------------------------------------------------ 后台线程

class AnalyzeWorker(QThread):
    one = Signal(dict)
    fail = Signal(str, str)
    tick = Signal(int, int, str)
    done = Signal(int)

    def __init__(self, files, deep=True, thresholds=None):
        super().__init__()
        self.files, self.deep, self.th = list(files), deep, thresholds or TH
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        n, ok = len(self.files), 0
        for i, p in enumerate(self.files, 1):
            if self._stop:
                break
            self.tick.emit(i, n, os.path.basename(p))
            try:
                self.one.emit(analyze_file(p, deep=self.deep, thresholds=self.th))
                ok += 1
            except Exception as e:                                # noqa: BLE001
                self.fail.emit(p, str(e)[:120])
        self.done.emit(ok)


class ViewWorker(QThread):
    """频谱图 / 瀑布图的数据生产者。kind = "spec" | "wfall"。"""

    ready = Signal(str, str, object, object, object)

    def __init__(self, path, kind="spec"):
        super().__init__()
        self.path, self.kind = path, kind

    def run(self):
        try:
            fn = view_spectrogram if self.kind == "spec" else view_waterfall
            t, f, S = fn(self.path)
            self.ready.emit(self.path, self.kind, t, f, S)
        except Exception:                                          # noqa: BLE001
            self.ready.emit(self.path, self.kind, None, None, None)


class SettingsDialog(QDialog):
    def __init__(self, parent, theme: T.Theme):
        super().__init__(parent)
        self.setWindowTitle("判定阈值")
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        v.setSpacing(12)
        self.cb_deep = QCheckBox("深度分析（计算频谱 / 响度 / 音乐特性）")
        self.cb_deep.setChecked(True)
        v.addWidget(self.cb_deep)
        tip = QLabel("阈值放宽会把更多文件判为「合格」。改动只影响之后的判定。")
        tip.setWordWrap(True)
        tip.setObjectName("Caption")
        v.addWidget(tip)

        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)
        self.sp: dict[str, QSpinBox] = {}
        rows = [
            ("cd_full", "44.1k 满带宽 ≥", 1.0, 22.0), ("ltd", "带限 / 有损分界", 10.0, 21.0),
            ("lossy", "有损 / 强有损分界", 8.0, 19.0), ("full_48", "48k 真带宽 ≥", 21.0, 24.0),
            ("up_44", "48k 疑似升频 ≥", 20.0, 23.5), ("hi_ok", "伪 Hi-Res 上限 ≤", 20.0, 28.0),
            ("min_drop_db", "判定为截止的最小跌幅", 3.0, 60.0),
            ("brick_db", "砖墙判定陡度 >", 10.0, 120.0), ("clip_pct", "削波告警 >", 0.0, 1.0),
            ("loud_war_lufs", "响度战门槛 >", -20.0, 0.0), ("lra_flat", "动态压平门槛 <", 0.0, 12.0),
        ]
        for i, (k, lab, lo, hi) in enumerate(rows):
            g.addWidget(QLabel(lab), i, 0)
            sp = QSpinBox()
            sp.setRange(int(lo * 1000), int(hi * 1000))
            sp.setSingleStep(50)
            sp.setValue(int(getattr(TH, k, 0) * 1000))
            sp.setFixedWidth(120)
            self.sp[k] = sp
            g.addWidget(sp, i, 1)
        v.addLayout(g)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def values(self) -> dict:
        return {k: s.value() / 1000.0 for k, s in self.sp.items()}


# ------------------------------------------------------------------ 主窗口

class MainWindow(FramelessWindow):
    COLS = ["文件名", "规格", "截止", "DR", "削波", "判定"]
    COL_W = {1: 74, 2: 62, 3: 46, 4: 62, 5: 78}
    # 视图数据缓存的**字节**上限：一份频谱图 (t,f,S) 约 0.7–3 MB、瀑布图约 0.2 MB。
    # 96 MB 够留住几十次点选，又不会把常驻内存顶上去（实测连点 40 个文件稳定在 +60 MB）。
    CACHE_MAX_BYTES = 96 << 20
    # 表格刷新的合并窗口（见 _schedule_refresh）：120 ms ≈ 8 行/秒地长出来，
    # 肉眼与"每来一个文件刷一次"没有区别，但把 N 次全表重建压成与文件数无关的常数级。
    REFRESH_COALESCE_MS = 120
    # 视图 key → QStackedWidget 的索引。**顺序必须与 _stack() 里 addWidget 的
    # 真实顺序一致**：overview, spectrum, spectro, waterfall, bands,
    # compare(cv_cmp), batch(cv_ovw), meta(两个 KVTable)。
    # 曾把 meta/compare/batch 三者顺序写错，症状是侧边栏点「元数据」显示的是
    # 对比画布、而真正的元数据页永远到不了（tests/test_ui_views.py 钉住此映射）。
    VIEW_KEYS = ["overview", "spectrum", "spectro", "waterfall", "bands",
                 "compare", "batch", "meta"]

    def __init__(self):
        # 高 DPI：必须在创建 QApplication 之后、创建窗口之前设置
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
            app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        super().__init__(title="audiocheck", subtitle="音源质量检测")

        self.theme: T.Theme = T.DARK
        # 由种子色生成的主题（`ui/palette.py`）。None = 用内置 DARK/LIGHT。
        self.seed: str | None = None
        self.seed_mode: str | None = None
        # 风格档（`palette.VIBES`）：与明暗正交的第二根轴。calm = 近中性表面，
        # pop = 带色表面 + 高彩度装饰色阶（"五颜六色"那一档）。
        self.seed_vibe: str = "calm"
        self.items: list[dict] = []
        self._idx: dict[str, int] = {}
        self.queue: list[str] = []
        self.worker: AnalyzeWorker | None = None
        # 按 (路径, 视图) 索引在跑的线程：既能持有引用防 GC，也顺手当"去重表"
        # 用（快速来回点选时同一个 STFT 不该算两遍）。
        self._view_workers: dict[tuple[str, str], ViewWorker] = {}
        self._cache: dict[tuple[str, str], tuple] = {}
        self._last_sig: tuple | None = None
        # 表格刷新的合并节流（见 _schedule_refresh）
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(self.REFRESH_COALESCE_MS)
        # 合并刷新的目标是**轻量刷新**：行本身由 `_update_row` 增量改掉了，
        # 这里只需把计数/状态/概览这些"与行数无关"的东西更新一次。
        self._refresh_timer.timeout.connect(self._refresh_light)

        self._build()
        self._commands()
        self.apply_theme(self.theme)
        self._restore()
        self.setAcceptDrops(True)

    # ============================================================ 构建

    def _build(self):
        host = self.content()
        root = QHBoxLayout(host)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = SideBar(self.theme)
        self.sidebar.add_section("分析")
        for key, label, ico in (("overview", "总览", "chart"), ("spectrum", "频谱曲线", "wave"),
                                ("spectro", "频谱图", "grid"), ("waterfall", "瀑布图", "layers"),
                                ("bands", "频段能量", "chart")):
            self.sidebar.add_item(key, label, ico)
        self.sidebar.add_section("信息")
        for key, label, ico in (("meta", "元数据", "doc"), ("batch", "批次概览", "chart")):
            self.sidebar.add_item(key, label, ico)
        self.sidebar.add_footer_item("settings", "阈值设置", "sliders")
        self.sidebar.add_footer_item("theme", "切换主题", "moon")
        self.sidebar.navigate.connect(self._navigate)
        root.addWidget(self.sidebar)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._toolbar())

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left_box = QWidget()
        lv = QVBoxLayout(left_box)
        lv.setContentsMargins(16, 12, 8, 12)
        lv.setSpacing(8)
        lv.addWidget(self._file_panel())
        split.addWidget(left_box)
        split.addWidget(self._stack())
        split.setSizes([560, 900])
        split.setStretchFactor(1, 1)
        self.split = split
        rv.addWidget(split, 1)
        root.addWidget(right, 1)

        sb = QStatusBar()
        sb.setContentsMargins(16, 0, 16, 0)
        self.pb = QProgressBar()
        self.pb.setMaximumWidth(200)
        self.pb.setVisible(False)
        self.lbl_stat = QLabel("就绪")
        sb.addWidget(self.lbl_stat, 1)
        sb.addPermanentWidget(self.pb)
        self.setStatusBar(sb)

        self.toasts = ToastHost(self)
        self.toasts.sync_geometry()

    def _toolbar(self):
        bar = QWidget()
        bar.setFixedHeight(52)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 8, 16, 8)
        h.setSpacing(8)

        self.act: dict[str, QPushButton] = {}
        defs = [("add", "添加文件", "folder"), ("dir", "添加文件夹", "add"),
                ("run", "开始分析", "play"), ("stop", "停止", "stop"),
                ("export", "导出", "export"), ("clear", "清空", "trash")]
        for key, text, ico in defs:
            b = QPushButton(text)
            if key == "run":
                b.setObjectName("Primary")
            else:
                b.setObjectName("Ghost")
            b.setIcon(T.icon(ico, "#e8ecf3", 16))
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(32)
            self.act[key] = b
            h.addWidget(b)
        self.act["add"].clicked.connect(self.add_files)
        self.act["dir"].clicked.connect(self.add_dir)
        self.act["run"].clicked.connect(self.start)
        self.act["stop"].clicked.connect(self.stop)
        self.act["export"].clicked.connect(self.export)
        self.act["clear"].clicked.connect(self.clear)
        self.act["stop"].setEnabled(False)

        h.addStretch(1)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("搜索文件名")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setMaximumWidth(180)
        self.ed_search.textChanged.connect(self.refresh_table)
        h.addWidget(self.ed_search)
        self.cb_filter = QComboBox()
        self.cb_filter.addItems(["全部", "仅不达标", "仅有疑", "仅合格", "仅未分析"])
        # 112 = 4×28，上栅格。原 110 是随手写的，与筛选框内的文字宽度也无关系。
        self.cb_filter.setMaximumWidth(112)
        self.cb_filter.currentIndexChanged.connect(self.refresh_table)
        h.addWidget(self.cb_filter)
        return bar

    def _file_panel(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("文件")
        t.setObjectName("SectionTitle")
        head.addWidget(t)
        head.addStretch(1)
        self.lbl_count = QLabel("0 / 0")
        self.lbl_count.setObjectName("Caption")
        head.addWidget(self.lbl_count)
        v.addLayout(head)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.delegate = VerdictDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        hh = self.table.horizontalHeader()
        # **默认按文件名升序**。`setSortingEnabled(True)` 会让 Qt 立刻套用当前
        # 排序指示，而新表的默认指示是「第 0 列、降序」——于是刚加进来的一批文件
        # 是**倒序**显示的（整张专辑最后一首排在最上面）。显式定下默认方向。
        self.table.sortItems(0, Qt.AscendingOrder)
        hh.setSortIndicator(0, Qt.AscendingOrder)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setMinimumSectionSize(36)
        for i, px in self.COL_W.items():
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, px)
        hh.setHighlightSections(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        # 400 会把整窗的最小宽度顶到 **1166 逻辑像素**，而 150% 缩放下的 1920 屏
        # 只有 1138——差这 28px 就是"右侧被切掉一条"（用户实测报的）。
        # 320 仍够放四列数字 + 判定胶囊；表格本来就有横向滚动条。
        self.table.setMinimumWidth(320)
        v.addWidget(self.table, 1)

        self.drop_hint = QLabel("把文件或文件夹拖到这里")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setObjectName("Caption")
        self.drop_hint.setFixedHeight(32)
        v.addWidget(self.drop_hint)
        return w

    def _stack(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 12, 16, 12)
        v.setSpacing(0)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)

        # --- 总览 ---
        ov = QWidget()
        ovl = QVBoxLayout(ov)
        ovl.setContentsMargins(8, 0, 0, 0)
        ovl.setSpacing(12)
        self.banner = self._banner()
        ovl.addWidget(self.banner)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        ovl.addLayout(self.grid)
        ovl.addWidget(self._section("能不能用它来评耳机"))
        self.lbl_advice = QLabel("—")
        self.lbl_advice.setWordWrap(True)
        self.lbl_advice.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.lbl_advice.setMinimumHeight(64)
        ovl.addWidget(self.lbl_advice)
        ovl.addStretch(1)
        self.stack.addWidget(ov)

        # --- 画布视图 ---
        self.cv_spec = SpectrumCanvas()
        self.cv_spectro = SpectrogramCanvas()
        self.cv_wfall = WaterfallCanvas()
        self.cv_band = BandCanvas()
        self.cv_cmp = CompareCanvas()
        self.cv_ovw = OverviewCanvas()
        for cv in (self.cv_spec, self.cv_spectro, self.cv_wfall, self.cv_band,
                   self.cv_cmp, self.cv_ovw):
            self.stack.addWidget(cv)

        # --- 元数据 ---
        mp = QWidget()
        ml = QSplitter(Qt.Horizontal)
        ml.setChildrenCollapsible(False)
        lb = QWidget()
        l1 = QVBoxLayout(lb)
        l1.setContentsMargins(8, 0, 4, 0)
        l1.addWidget(self._section("容器标签"))
        self.tb_meta = KVTable(self.theme)
        l1.addWidget(self.tb_meta, 1)
        rb = QWidget()
        l2 = QVBoxLayout(rb)
        l2.setContentsMargins(4, 0, 0, 0)
        l2.addWidget(self._section("全部测量指标"))
        self.tb_metrics = KVTable(self.theme)
        l2.addWidget(self.tb_metrics, 1)
        ml.addWidget(lb)
        ml.addWidget(rb)
        ml.setSizes([420, 460])
        m = QVBoxLayout(mp)
        m.setContentsMargins(0, 0, 0, 0)
        m.addWidget(ml)
        self.stack.addWidget(mp)

        # --- 空状态（无选中时覆盖） ---
        self.empty = EmptyState(self.theme, "wave", "尚未选择文件",
                                "添加音频文件并开始分析后，这里会显示频谱与判定结论。",
                                "添加文件", self.add_files)
        self.stack.addWidget(self.empty)
        self._empty_idx = self.stack.count() - 1
        return w

    def _banner(self):
        # B9.1：判定区 = **淡底（tint）+ 4px 判定带（band）+ 判词 + hero**。
        # 它替代了 B7 的"通栏高彩块 + 270° 环形仪表"——诊断实测那套的
        # 三行字排版带重叠 2.5px，且"占比用转盘"是车载仪表语汇。
        # 整块的结构在 `widgets.VerdictPanel` 里（这里只负责建与接线）。
        panel = VerdictPanel(self.theme)
        # 老属性名保留成**别名**：`_banner_update` / `apply_theme` /
        # `_refresh_selection_theme` 与既有测试都按这两个名字取值，
        # 改名只会让 diff 里混进与重构无关的改动。
        self.verdict_panel = panel
        self.lbl_verdict = panel.lbl_verdict
        self.lbl_file = panel.lbl_file
        return panel

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("SectionTitle")
        return lab

    # ============================================================ 主题

    def apply_theme(self, t: T.Theme):
        self.theme = t
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(T.qss(t))
        self.sidebar.apply_theme(t)
        self.delegate.apply_theme(T.plot_palette(t.p, t), t)   # 委托仍按旧字典键取值
        pp = T.plot_palette(t.p, t)
        for cv in (self.cv_spec, self.cv_spectro, self.cv_wfall, self.cv_band,
                   self.cv_cmp, self.cv_ovw):
            cv.apply_theme(pp)
        # B9.1：判定胶囊（Badge）不再出现在判定区——小样的形态是
        # **方点 + 疏排判词**（`● 不达标`），不是一个填充胶囊。
        # `Badge` 类保留在 widgets 里（它是通用徽标，别处可能用得上），
        # 但判定区不再引用它。
        self.verdict_panel.apply_theme(t)
        self.tb_meta.apply_theme(t)
        self.tb_metrics.apply_theme(t)
        self.empty.apply_theme(t, "wave")
        self.toasts.apply_theme(t)
        self._apply_icons()
        self._refresh_selection_theme()
        self.refresh_table()

    def _refresh_selection_theme(self):
        """换肤后重刷**用内联样式写死颜色**的那几个控件。

        QSS 管不到它们：指标卡（`StatCard` 自己 `setStyleSheet`）与判定条
        （`_banner_update` 里拼的 inline style）。换肤时若不重刷，
        浅色主题下会留着深色主题的文字色 —— 实测指标卡数值几乎看不见，
        而"切换主题"这个动作看起来是成功的。这类"换了一半"最难查。
        """
        if self._last_sig is None:
            return
        p = self._last_sig[0]
        i = self._idx.get(p)
        r = self.items[i]["result"] if i is not None else None
        if r:
            self._banner_update(r)
            self._cards(r)

    def _apply_icons(self):
        p = self.theme.p
        for key, ico in (("add", "folder"), ("dir", "add"), ("run", "play"),
                         ("stop", "stop"), ("export", "export"), ("clear", "trash")):
            self.act[key].setIcon(T.icon(ico, p.text, 16))

    # ============================================================ 导航 / 命令

    def _navigate(self, key: str):
        if key == "settings":
            self.open_settings()
            return
        if key == "theme":
            self.toggle_theme()
            return
        if key in self.VIEW_KEYS:
            self.stack.setCurrentIndex(self.VIEW_KEYS.index(key))
            self.sidebar.set_active(key)
            motion.fade(self.stack.currentWidget(), self)
            self._load_visible_view()          # 切到频谱图/瀑布图时才补算数据

    def _commands(self):
        self._cmds = [
            ("添加文件", "Ctrl+O", self.add_files),
            ("添加文件夹", "Ctrl+Shift+O", self.add_dir),
            ("开始分析", "F5", self.start),
            ("停止", "Esc", self.stop),
            ("导出报告", "Ctrl+E", self.export),
            ("清空列表", "Ctrl+L", self.clear),
            ("切换主题", "Ctrl+D", self.toggle_theme),
            ("切换配色风格（克制 / 缤纷）", "Ctrl+M", self.toggle_vibe),
            ("换个种子色（生成整套配色）", "Ctrl+G", self.pick_seed),
            ("阈值设置", "Ctrl+T", self.open_settings),
            ("跳转到 总览", "", lambda: self._navigate("overview")),
            ("跳转到 频谱曲线", "", lambda: self._navigate("spectrum")),
            ("跳转到 频谱图", "", lambda: self._navigate("spectro")),
            ("跳转到 瀑布图", "", lambda: self._navigate("waterfall")),
            ("跳转到 频段能量", "", lambda: self._navigate("bands")),
            ("跳转到 元数据", "", lambda: self._navigate("meta")),
            ("跳转到 批次概览", "", lambda: self._navigate("batch")),
        ]
        for seq, fn in (("Ctrl+O", self.add_files), ("Ctrl+Shift+O", self.add_dir),
                        ("F5", self.start), ("Ctrl+R", self.start), ("Esc", self.stop),
                        ("Ctrl+E", self.export), ("Ctrl+L", self.clear),
                        ("Ctrl+D", self.toggle_theme), ("Ctrl+T", self.open_settings),
                        ("Ctrl+G", self.pick_seed), ("Ctrl+M", self.toggle_vibe),
                        ("Ctrl+K", self._palette), ("Delete", self._remove_selected),
                        ("Ctrl+F", lambda: self.ed_search.setFocus())):
            QShortcut(QKeySequence(seq), self).activated.connect(fn)

    def _palette(self):
        p = CommandPalette(self, self._cmds, self.theme)
        p.move(self.geometry().center() - p.rect().center())
        p.exec()

    def toggle_theme(self):
        # 用着种子色主题时按 Ctrl+D 就换该种子的另一套（深↔浅），
        # 而不是跳回内置的 DARK/LIGHT——否则"换了个种子色"会被一次换肤冲掉。
        if self.seed:
            self.pick_seed(mode=("light" if self.seed_mode == "dark" else "dark"))
            return
        self.apply_theme(T.LIGHT if self.theme.name == "dark" else T.DARK)

    def toggle_vibe(self):
        """在克制 / 缤纷两档之间切换（不改变当前种子色与深浅）。

        为什么做成一个独立开关而不是塞进深浅切换：它是**另一根轴**。
        "深/浅"回答"现在几点用"，"克制/缤纷"回答"要不要带色的界面"，
        两个问题独立，混在一个键上就只能二选一。
        """
        self.seed_vibe = "pop" if self.seed_vibe != "pop" else "calm"
        seed = self.seed or T.BUILTIN_SEED
        mode = self.seed_mode or ("dark" if self.theme.name == "dark" else "light")
        from .palette import theme_from_seed
        try:
            th = theme_from_seed("seed", seed, mode, self.seed_vibe)
        except ValueError as e:                                # noqa: BLE001
            self.toasts.notify(str(e).splitlines()[0][:60], "bad")
            return
        self.seed, self.seed_mode = seed, mode
        self.apply_theme(th)
        self.toasts.notify("配色风格：%s" % ("缤纷（彩色表面 + 多色装饰阶）" if self.seed_vibe
                                       == "pop" else "克制（近中性表面）"), "ok")

    def pick_seed(self, mode: str | None = None, vibe: str | None = None):
        """从种子色生成整套主题（`ui/palette.py`：OKLCH + WCAG 自审）。"""
        d = SeedDialog(self, self.theme, self.seed or "#3d7dff",
                       mode or self.seed_mode or self.theme.name,
                       vibe or self.seed_vibe)
        if d.exec() != QDialog.Accepted:
            return
        from .palette import theme_from_seed
        try:
            th = theme_from_seed("seed", d.seed, d.mode, d.vibe)
        except ValueError as e:                                # noqa: BLE001
            self.toasts.notify(str(e).splitlines()[0][:60], "bad")
            return
        self.seed, self.seed_mode, self.seed_vibe = d.seed, d.mode, d.vibe
        self.apply_theme(th)
        self.toasts.notify("已按种子色 %s 生成%s%s主题"
                           % (d.seed, "深色" if d.mode == "dark" else "浅色",
                              "缤纷" if d.vibe == "pop" else ""), "ok")

    def open_settings(self):
        d = SettingsDialog(self, self.theme)
        if d.exec() == QDialog.Accepted:
            for k, v in d.values().items():
                setattr(TH, k, v)
            self.toasts.notify("阈值已更新，重新分析后生效", "ok")

    # ============================================================ 数据

    def add_files(self):
        fs, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "",
            "音频 (*.flac *.wav *.mp3 *.m4a *.aac *.ogg *.opus *.aif *.aiff);;所有文件 (*)")
        if fs:
            self.enqueue(fs)

    def add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.enqueue(collect_files([d]))

    def enqueue(self, files):
        add = [f for f in files if f not in self._idx]
        if not add:
            self.toasts.notify("没有新文件（重复的已跳过）", "warn")
            return
        for f in add:
            self._idx[f] = len(self.items)
            self.items.append({"path": f, "file": os.path.basename(f), "result": None})
        self.queue.extend(add)
        self.refresh_table()
        self.toasts.notify("已加入 %d 个文件" % len(add), "ok")

    def start(self):
        if not self.queue:
            self.toasts.notify("队列为空", "warn")
            return
        # **分析途中不能再起一个 worker**：`self.worker = AnalyzeWorker(...)`
        # 会把正在跑的 QThread 的最后引用覆盖掉，PySide 可能在线程仍在运行时
        # 销毁 C++ 对象 —— 进程硬崩（不是异常，是直接没）。
        # 正确行为是把文件留在队列里，等本批结束后自动接着分析。
        if self.worker and self.worker.isRunning():
            self.toasts.notify("正在分析，已加入队列（本批结束后自动继续）", "info")
            return
        todo, self.queue = self.queue, []
        self.act["run"].setEnabled(False)
        self.act["stop"].setEnabled(True)
        self.pb.setVisible(True)
        self.pb.setMaximum(len(todo))
        self.pb.setValue(0)
        self.worker = AnalyzeWorker(todo)
        self.worker.tick.connect(self._tick)
        self.worker.one.connect(self._add_result)
        self.worker.fail.connect(lambda p, e: self.toasts.notify(
            "读取失败：%s" % os.path.basename(p), "bad"))
        self.worker.done.connect(self._finish)
        self.worker.start()

    def stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.toasts.notify("已请求停止", "warn")

    def _tick(self, i, n, name):
        self.pb.setMaximum(n)
        self.pb.setValue(i)
        self.status("分析中 %d/%d · %s" % (i, n, name))

    def _add_result(self, r):
        i = self._idx.get(r["path"])
        if i is None:
            return
        self.items[i]["result"] = r
        # 增量：只改这一行（O(行数) 的指针比较），不重建整张表。
        # 计数/状态/概览这些与行数无关的部分合并到 120 ms 一次（见 _schedule_refresh）。
        self._update_row(r["path"])
        self._schedule_refresh()

    def _schedule_refresh(self):
        """把短时间内的多次刷新合并成一次。

        每次 `refresh_table()` 的代价与**行数**成正比（200 行实测 214 ms），
        而分析时每回来一个文件都会触发一次：100 个文件就是 100 次全表重建，
        且每次要重建的行越来越多 —— 界面在分析过程中越拖越卡。
        合并到 120 ms 一次后，总代价与文件数无关，观感不变（照样一行行长出来）。
        """
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _finish(self, n):
        self._refresh_timer.stop()          # 收尾立刻刷，不等节流窗口
        self.act["run"].setEnabled(True)
        self.act["stop"].setEnabled(False)
        self.pb.setVisible(False)
        self.refresh_table()
        self.toasts.notify("完成：本次 %d 个文件" % n, "ok")
        # 分析途中又加了文件（`start()` 把它们留在队列里）：接着分析，
        # 用户不必守在机器旁边按第二次。
        if self.queue:
            self.start()

    def clear(self):
        self.items, self._idx, self.queue = [], {}, []
        self._cache.clear()
        self.table.setRowCount(0)
        self.cv_spec.set_data(None)
        self.cv_band.set_data(None)
        self.cv_spectro.set_data(None, None, "选择条目后自动生成")
        self.cv_wfall.set_data(None, None, "选择条目后自动生成")
        self.cv_cmp.set_data([])
        self.cv_ovw.set_data([])
        self.tb_meta.set_rows([])
        self.tb_metrics.set_rows([])
        self.lbl_advice.setText("—")
        self.status("已清空")

    # ============================================================ 表格

    def _visible(self):
        mode = self.cb_filter.currentText()
        key = self.ed_search.text().strip().lower()
        out = []
        for it in self.items:
            r = it["result"]
            if mode == "仅未分析" and r:
                continue
            if mode == "仅不达标" and (not r or r.get("level") != "bad"):
                continue
            if mode == "仅有疑" and (not r or r.get("level") != "warn"):
                continue
            if mode == "仅合格" and (not r or r.get("level") != "ok"):
                continue
            if key and key not in it["file"].lower() and key not in it["path"].lower():
                continue
            out.append(it)
        return out

    def refresh_table(self):
        """**全量重建**。只在"表格结构会变"的时候用：筛选、搜索、排序、
        增删文件、换肤。分析过程中每回来一个文件走的是 `_update_row`（增量）。"""
        items = self._visible()
        hh = self.table.horizontalHeader()
        col, order = hh.sortIndicatorSection(), hh.sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for it in items:
            self._row(it)
        self.table.setSortingEnabled(True)
        if self.table.rowCount() and col >= 0:
            self.table.sortItems(col, order)
        self._refresh_light()

    def _refresh_light(self):
        """只更新**与行数无关**的部分：计数 / 状态 / 批次概览 / 自动选中。

        为什么要把这一层单独拆出来：分析过程中每回来一个文件都要刷新这些
        （用户要看到"已分析 37 / 200"在动），但**重建整张表是 O(行数)**，
        200 行实测 25–95 ms，1000 个文件就是 O(N²) 的全表重建 ——
        界面会在分析途中越拖越卡。拆开之后，这段代价与行数无关。
        """
        items = self._visible()
        done = sum(1 for i in self.items if i["result"])
        self.lbl_count.setText("%d / %d" % (len(items), len(self.items)))
        self.cv_ovw.set_data([i["result"] for i in self.items if i["result"]])
        self.status("已分析 %d / %d ｜ 队列 %d" % (done, len(self.items), len(self.queue)))
        self._select_first_analyzed()

    def _select_first_analyzed(self):
        """没有选中任何行时，自动选中第一行**已有结果**的。

        否则详情面板（结论条 / 环形仪表 / 曲线）全是「—」——用户按下
        「开始分析」看到的是一片空白，还得自己想起来去点某一行。
        - 只在**没有选中**时才选，不覆盖用户已经做出的选择；
        - 只选**已有结果**的行：选到「待分析」行会让 on_select 把 _last_sig
          记下来，等结果回来再选同一行时会被早退优化挡住，详情面板从此不更新（实测踩过）。
        """
        if self.table.selectedItems():
            return
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            if cell is None:
                continue
            p = cell.data(Qt.UserRole)
            k = self._idx.get(p) if p else None
            if k is not None and self.items[k]["result"]:
                self.table.setCurrentItem(cell)
                return

    def _update_row(self, path) -> bool:
        """**增量**刷新某一行。返回是否找到并更新了它。

        扫描找行是 O(行数) 的指针比较（千行量级 ~0.1 ms），比重建整行便宜
        两个数量级；真到万行再考虑加 path→row 索引——那时再说，现在不预支复杂度。
        """
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            if cell is None:
                continue
            if cell.data(Qt.UserRole) == path:
                k = self._idx.get(path)
                if k is not None:
                    self._fill_row(r, self.items[k])
                return True
        return False

    def _row(self, it):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._fill_row(row, it)

    def _fill_row(self, row: int, it):
        """把一行 6 个单元格写好。**全量重建与增量更新共用这一处**——
        两套口径迟早会走岔（例如只改了一边的对齐或颜色）。"""
        r = it["result"]
        lv = (r or {}).get("level")
        if r:
            vals = [it["file"], "%.1fk/%s" % (r["sr"] / 1000, str(r["bits"]).replace("PCM_", "")),
                    ("%.2fk" % r["cutoff_khz"]) if r.get("cutoff_khz") else "—",
                    fmt_num(r.get("dr_db"), "", 1), fmt_num(r.get("clip_pct"), "%", 2),
                    LEVEL_CN.get(lv, "?")]
        else:
            vals = [it["file"], "…", "—", "—", "—", "待分析"]
        for c, txt in enumerate(vals):
            cell = QTableWidgetItem(txt)
            cell.setData(Qt.UserRole, it["path"])
            cell.setData(Qt.UserRole + 1, lv or "")
            cell.setToolTip((r or {}).get("verdict", "尚未分析"))
            if c in (2, 3, 4):
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if not r:
                cell.setForeground(QColor(self.theme.p.text_faint))
            self.table.setItem(row, c, cell)

    def _path_at(self, row):
        it = self.table.item(row, 0)
        return it.data(Qt.UserRole) if it else None

    # ============================================================ 选中联动

    def on_select(self):
        mine = [i for i in self.table.selectedItems() if i.column() == 0]
        if not mine:
            return
        if len(mine) > 1:
            rs = self._results_of([i.data(Qt.UserRole) for i in mine])
            if len(rs) > 1:
                self.cv_cmp.set_data([(r, r.get("curve")) for r in rs if r.get("curve")])
                self._navigate("compare")
            return
        p = mine[0].data(Qt.UserRole)
        i = self._idx.get(p)
        r = self.items[i]["result"] if i is not None else None
        # 早退优化：同一个文件且**同一份结果**就不重画。必须把结果对象的
        # 身份也纳入判断——只比路径的话，用户在结果回来前点过这一行，
        # _last_path 已经记上，等分析完成再选中同一行就会永远停在旧画面
        # （实测：详情面板一片「—」，且此后任何重选都刷不过来）。
        sig = (p, id(r) if r else None)
        if self._last_sig == sig:
            return
        self._last_sig = sig
        if not r:
            self.stack.setCurrentIndex(self._empty_idx)
            return
        if self.stack.currentIndex() == self._empty_idx:
            self._navigate("overview")
        self._banner_update(r)
        self._cards(r)
        self.cv_spec.set_data(r)
        self.cv_band.set_data(r)
        self._meta(r)
        self.lbl_advice.setText(r.get("advice", "—"))
        # 只给**当前真的在看**的视图起线程：频谱图与瀑布图的 S 矩阵各要读盘 +
        # 12 s 的 4096 点 STFT，选一次文件算两份是白算（多数人只看频谱图）。
        # 切到另一个视图时 _navigate 会补上（见 _load_visible_view）。
        self._load_visible_view()

    def _load_visible_view(self):
        """按当前页决定要生成哪一份视图数据（不在视图页就什么都不做）。"""
        i = self.stack.currentIndex()
        if not 0 <= i < len(self.VIEW_KEYS) or self._last_sig is None:
            return
        kind = {"spectro": "spec", "waterfall": "wfall"}.get(self.VIEW_KEYS[i])
        p = self._last_sig[0]
        j = self._idx.get(p)
        if kind and j is not None and self.items[j]["result"]:
            self._load_view(p, kind)

    def _results_of(self, paths):
        out = []
        for p in paths:
            i = self._idx.get(p)
            if i is not None and self.items[i]["result"]:
                out.append(self.items[i]["result"])
        return out

    def _banner_update(self, r):
        lv = r.get("level", "warn")
        # B9.1：判词 / 文件名 / hero / 占比一起交给面板。判定带与方点的颜色
        # 由 `VerdictPanel.set_data` 统一设到**容器 + 判定带 + 方点**三处
        # （它们各有一条 QSS 属性选择器，各一次 repolish，
        # **不给任何一处 setStyleSheet**）。
        #
        # 判词字号与颜色来自 QSS（`QLabel#VerdictText`）——原来这里是逐次
        # setStyleSheet 刷字号与颜色，那是欠账 #10（部件级样式表）的一处，
        # 顺手收掉：规则集中在主题里，换肤只算一次。
        cut, ratio = r.get("cutoff_khz"), r.get("cutoff_ratio")
        self.verdict_panel.set_data(
            lv, r.get("verdict", "—"),
            "%s · %s · %s" % (r["file"], meta_brief(r.get("meta") or {}),
                              os.path.dirname(r["path"])),
            ("%.2f" % cut) if cut else "—", "kHz", "真实截止", ratio)
        # 信息驱动的动效：只有"有疑 / 不达标"才脉一下——结论值得看一眼。
        # 常驻装饰会持续吃 CPU，也会被习惯性忽略，那样动效就失去意义了。
        # （B9.1 前脉的是判定胶囊，改成脉**判词**：胶囊随重构去掉了方点+文字，
        #  判词现在是判定语义的文字载体。）
        if lv in ("warn", "bad"):
            motion.pulse(self.lbl_verdict, self, loops=2)

    def _cards(self, r):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        tp = r.get("true_peak_dbfs")
        pairs = [
            ("采样率", "%.1f kHz" % (r["sr"] / 1000)),
            ("编码 / 位深", "%s · %s" % (r["format"], str(r["bits"]).replace("PCM_", ""))),
            ("时长", "%d:%02d" % (r["seconds"] // 60, r["seconds"] % 60)),
            ("体积 / 码率", "%.1f MB%s" % (r["size_mb"],
                                        " · %dk" % r["kbps"] if r.get("kbps") else "")),
            ("真实截止", ("%.2f kHz" % r["cutoff_khz"]) if r.get("cutoff_khz") else "—"),
            ("占奈奎斯特", ratio_pct(r.get("cutoff_ratio"))),
            ("砖墙陡度", ("%.0f dB/oct" % r["brickwall_db_per_oct"])
             if r.get("brickwall_db_per_oct") else "—"),
            ("峰值 / RMS", "%.2f / %.2f" % (r.get("peak_dbfs") or 0, r.get("rms_dbfs") or 0)),
            ("真峰值 ITU-R", ("%+.2f dBTP" % tp) if tp is not None else "—"),
            ("集成响度", fmt_num(r.get("lufs"), " LUFS", 1)),
            ("动态范围 DR", fmt_num(r.get("dr_db"), " dB", 1)),
            ("响度范围 LRA", fmt_num(r.get("lra"), " LU", 1)),
            ("PLR", fmt_num(r.get("plr"), " dB", 1)),
            ("削波", fmt_num(r.get("clip_pct"), "%", 3)),
            ("相位相关", fmt_num(r.get("corr"), "", 2)),
            ("声道宽度", fmt_num(r.get("width_db"), " dB", 1)),
            ("频段重心", human_khz(r.get("centroid_hz"))),
            ("编码器串", (r.get("meta") or {}).get("vendor") or "—"),
        ]
        cards = []
        # 卡片的顶条**不再是装饰色阶**（B7.1 做法 2）：18 张卡的色条会连成
        # 6 条横贯全屏的彩带，实测贡献了全图高彩的 42.4% 却只把"一枚 accent"
        # 稀释成六个色相。现在它是中性顶光，按 QSS 取色，不再需要槽位参数。
        for i, (k, v) in enumerate(pairs):
            c = StatCard(self.theme)
            c.set(k, str(v))
            self.grid.addWidget(c, i // 6, i % 6)
            cards.append(c)
        motion.stagger(cards, self)

    def _metric_rows(self, r):
        g = r.get
        rows = [
            ("容器 / 编码", "%s ｜ %s ｜ %d 声道" % (r["format"], r["bits"], r["channels"])),
            ("采样率 / 位深", "%.0f Hz ｜ %s" % (r["sr"], r["bits"])),
            ("时长 / 体积 / 码率", "%d:%02d ｜ %.1f MB ｜ 约 %s kbps"
             % (r["seconds"] // 60, r["seconds"] % 60, r["size_mb"], g("kbps") or "—")),
            ("编码器串 vendor", (r.get("meta") or {}).get("vendor") or "—"),
            ("", ""),
            ("真实截止", human_khz(g("cutoff_hz"))),
            ("占奈奎斯特", ratio_pct(g("cutoff_ratio"))),
            ("砖墙陡度", fmt_num(g("brickwall_db_per_oct"), " dB/oct", 1)),
            ("频谱重心", human_khz(g("centroid_hz"))),
            ("85% 滚降", human_khz(g("rolloff85_hz"))),
            ("95% 滚降", human_khz(g("rolloff95_hz"))),
            ("谱平坦度", fmt_num(g("flatness"), "", 5)),
            ("噪声底", fmt_num(g("noise_floor_db"), " dB", 1)),
            ("", ""),
            ("峰值 / RMS", "%.2f / %.2f dBFS" % (g("peak_dbfs") or 0, g("rms_dbfs") or 0)),
            ("真峰值 BS.1770", fmt_num(g("true_peak_dbfs"), " dBTP", 2)),
            ("集成响度 R128", fmt_num(g("lufs"), " LUFS", 1)),
            ("响度范围 LRA", fmt_num(g("lra"), " LU", 1)),
            ("短时最大 / 最小", "%s / %s LUFS" % (fmt_num(g("st_max")), fmt_num(g("st_min")))),
            ("PLR", fmt_num(g("plr"), " dB", 1)),
            ("动态范围 DR", fmt_num(g("dr_db"), " dB", 1)),
            ("波峰因数", fmt_num(g("crest_db"), " dB", 1)),
            ("削波样本", fmt_num(g("clip_pct"), " %", 4)),
            ("直流偏移", fmt_num(g("dc_max_dbfs"), " dBFS", 1)),
            ("", ""),
            ("相位相关", fmt_num(g("corr"), "", 3)),
            ("声道宽度 M/S", fmt_num(g("width_db"), " dB", 1)),
            ("单声道兼容", g("mono_compat") or "—"),
            ("L/R 电平差", fmt_num(g("lr_level_diff_db"), " dB", 2)),
        ]
        if r.get("bands_pct"):
            rows.append(("", ""))
            for k, _, _ in BANDS:
                rows.append(("频段 " + BAND_CN[k], fmt_num(r["bands_pct"].get(k), " %", 1)))
        return [(a, b) for a, b in rows if a or b]

    def _meta(self, r):
        self.tb_meta.set_rows(meta_rows(r.get("meta") or {}))
        self.tb_metrics.set_rows(self._metric_rows(r))

    def _load_view(self, path, kind):
        cv = self.cv_spectro if kind == "spec" else self.cv_wfall
        key = (path, kind)
        if key in self._cache:
            cv.set_data(self.items[self._idx[path]]["result"] if path in self._idx else None,
                        self._cache[key])
            return
        if key in self._view_workers:
            return          # 同一份数据已在算：再来一次就是白读一遍盘 + 白做一遍 STFT
        cv.set_data(None, None, "生成中…")
        # 必须持有引用：QThread 被 GC 而线程仍在跑会让进程硬崩。用字典按 key 存，
        # 线程结束后自己摘掉——既不残留线程对象，也天然去重。
        w = ViewWorker(path, kind)
        self._view_workers[key] = w
        w.ready.connect(self._view_ready)
        w.finished.connect(lambda k=key: self._view_workers.pop(k, None))
        w.start()

    def _cache_bytes(self) -> int:
        """缓存当前压着多少字节（按数组真实 nbytes 算）。"""
        tot = 0
        for v in self._cache.values():
            for part in v:
                n = getattr(part, "nbytes", None)
                tot += int(n) if n is not None else 0
        return tot

    def _evict_cache(self):
        """按**字节**淘汰，而不是按条数。

        按条数限制是错的：一条 (t, f, S) 有多大取决于 nperseg / 时长 / max_cols，
        改一个常量就能让「12 条」变成 GB 级。字节预算是唯一与实现无关的界。
        """
        while len(self._cache) > 1 and self._cache_bytes() > self.CACHE_MAX_BYTES:
            self._cache.pop(next(iter(self._cache)))

    def _view_ready(self, path, kind, t, f, S):
        if S is None:
            return
        # 条目可能已经被删掉（或整个列表被清空）而 worker 还在跑：这时结果
        # 属于"没人要的东西"，写进缓存只会占着字节预算，还可能被后续
        # 同名路径误命中。**先确认这个路径还在列表里**。
        if path not in self._idx:
            return
        self._cache[(path, kind)] = (t, f, S)
        self._evict_cache()
        mine = [i for i in self.table.selectedItems() if i.column() == 0]
        if len(mine) == 1 and mine[0].data(Qt.UserRole) == path:
            cv = self.cv_spectro if kind == "spec" else self.cv_wfall
            i = self._idx.get(path)
            cv.set_data(self.items[i]["result"] if i is not None else None, (t, f, S))
            # 数据是异步来的（一秒上下），让它淡入而不是"啪"地出现——
            # 这是**信息驱动**的动效：用户由此知道"新内容刚刚到"。
            # 时长走令牌（原先手写 260 ms，不在任何一张表里）。取 `base`
            # 而不是 `slow`：`slow` 是"大容器展开"的量级，而这里是一块画布
            # 的内容到达，200 ms 已经足够让人看见"它是新来的"。
            motion.fade(cv, self, ms=Motion.base)

    # ============================================================ 其它

    def _menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        self.toasts.notify("右键菜单待接入", "info")

    def _remove_selected(self):
        paths = [i.data(Qt.UserRole) for i in self.table.selectedItems() if i.column() == 0]
        if not paths:
            return
        drop = set(paths)
        self.items = [i for i in self.items if i["path"] not in drop]
        self.queue = [q for q in self.queue if q not in drop]
        self._idx = {it["path"]: k for k, it in enumerate(self.items)}
        # 缓存里属于被删条目的条目也要跟着走：否则它们会一直占着字节预算
        # （最多 96 MB），把还能用得上的条目挤出去。
        self._cache = {k: v for k, v in self._cache.items() if k[0] not in drop}
        self._last_sig = None
        self.refresh_table()

    def export(self):
        rs = [i["result"] for i in self.items if i["result"]]
        if not rs:
            self.toasts.notify("先分析一些文件再导出", "warn")
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "导出", "audiocheck_report",
            "HTML 报告 (*.html);;JSON (*.json);;CSV (*.csv);;当前视图 PNG (*.png)")
        if not p:
            return
        try:
            if p.endswith(".png"):
                w = self.stack.currentWidget()
                if hasattr(w, "fig"):
                    w.fig.savefig(p, dpi=160, facecolor=self.theme.p.surface)
            elif p.endswith(".json"):
                import json
                json.dump([{k: v for k, v in r.items() if k != "curve"} for r in rs],
                          open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            elif p.endswith(".csv"):
                self._csv(p, rs)
            else:
                self._html(p, rs)
            self.toasts.notify("已导出 %s" % os.path.basename(p), "ok")
        except Exception as e:                                      # noqa: BLE001
            self.toasts.notify("导出失败：%s" % e, "bad")

    def _csv(self, path, rs):
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["文件", "路径", "格式", "采样率", "位深", "声道", "秒", "MB", "截止kHz",
                        "占奈奎斯特", "砖墙", "峰值", "RMS", "DR", "削波%", "LUFS", "LRA",
                        "真峰值", "PLR", "相位", "宽度", "厂牌", "ISRC", "编码器串", "判定"]
                       + [BAND_CN[k[0]] + "%" for k in BANDS])
            for r in rs:
                md = r.get("meta") or {}
                fld = md.get("fields") or {}
                w.writerow([r["file"], r["path"], r["format"], r["sr"], r["bits"], r["channels"],
                            r["seconds"], r["size_mb"], r.get("cutoff_khz"), r.get("cutoff_ratio"),
                            r.get("brickwall_db_per_oct"), r.get("peak_dbfs"), r.get("rms_dbfs"),
                            r.get("dr_db"), r.get("clip_pct"), r.get("lufs"), r.get("lra"),
                            r.get("true_peak_dbfs"), r.get("plr"), r.get("corr"), r.get("width_db"),
                            fld.get("label", ""), fld.get("isrc", ""), md.get("vendor", ""),
                            r.get("verdict")]
                           + [(r.get("bands_pct") or {}).get(k[0], "") for k in BANDS])

    def _html(self, path, rs):
        import html as H
        p = self.theme.p
        cnt = {lv: sum(1 for r in rs if r.get("level") == lv) for lv in ("ok", "warn", "bad")}
        out = ["<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
               "<title>audiocheck 报告</title><style>",
               "body{background:%s;color:%s;font:14px/1.7 'Microsoft YaHei',system-ui;"
               "margin:0;padding:34px 40px}" % (p.bg, p.text),
               ".cards{display:flex;gap:12px;margin:22px 0 26px}",
               ".card{flex:1;background:%s;border:1px solid %s;border-radius:12px;padding:14px 18px}"
               % (p.surface, p.border),
               ".k{color:%s;font-size:12px}.v{font-size:23px;font-weight:700;margin-top:3px}"
               % p.text_faint,
               # 注意 CSS 里的字面量 % 必须写成 %%：这里用了 % 格式化，否则运行时会抛
               # "unsupported format character"。这是 ruff F509 抓出来的真 bug。
               "table{width:100%%;border-collapse:collapse;font-size:12.5px;background:%s;"
               "border:1px solid %s;border-radius:12px;overflow:hidden}" % (p.surface, p.border),
               "th,td{padding:9px 11px;text-align:left;border-bottom:1px solid %s}" % p.border,
               "th{color:%s;font-weight:700;font-size:11.5px;background:%s}"
               % (p.text_dim, p.surface_hi),
               ".ok{color:%s;font-weight:700}.warn{color:%s;font-weight:700}"
               ".bad{color:%s;font-weight:700}" % (p.success, p.warning, p.danger),
               "</style></head><body>", "<h1>audiocheck · 音源质量报告</h1>",
               "<div class='cards'>"]
        for lv, lab in (("ok", "合格"), ("warn", "有疑"), ("bad", "不达标")):
            out.append("<div class='card'><div class='k'>%s</div><div class='v %s'>%d</div></div>"
                       % (lab, lv, cnt[lv]))
        out += ["</div>", "<table><tr><th>文件</th><th>规格</th><th>真实截止</th><th>DR</th>"
                "<th>削波</th><th>判定</th><th>能不能用来评耳机</th></tr>"]
        for r in sorted(rs, key=lambda x: {"bad": 0, "warn": 1, "ok": 2}.get(x.get("level"), 3)):
            out.append("<tr><td>%s</td><td>%.1fk %s %dch</td><td>%s</td><td>%s</td><td>%s</td>"
                       "<td class='%s'>%s</td><td>%s</td></tr>" % (
                           H.escape(r["file"]), r["sr"] / 1000, r["bits"], r["channels"],
                           ("%.2f kHz" % r["cutoff_khz"]) if r.get("cutoff_khz") else "—",
                           fmt_num(r.get("dr_db")), fmt_num(r.get("clip_pct"), "%"),
                           r.get("level", "warn"), H.escape(r.get("verdict", "")),
                           H.escape(r.get("advice", ""))))
        out.append("</table></body></html>")
        open(path, "w", encoding="utf-8").write("\n".join(out))

    def status(self, text):
        self.lbl_stat.setText(text)

    # ============================================================ 拖拽 / 关闭

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        files = collect_files(paths)
        if files:
            self.enqueue(files)
            self.start()
        elif paths:
            self.toasts.notify("拖入的内容里没有可识别的音频文件", "warn")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toasts.sync_geometry()

    def _clamp_to_screen(self):
        """把窗口几何**夹回当前桌面**（位置与尺寸都夹）。

        为什么必须有：`restoreGeometry` 存的是**逻辑像素**，而它忠实还原你存的
        东西——换了 DPI 缩放或换了显示器之后，那个尺寸可能比现在的屏幕还大。
        实测：存的是 2538 逻辑像素宽，而当前屏幕的逻辑宽度只有 1138
        （1707 物理像素 / 150% 缩放），于是窗口右侧 60% 直接跑到屏幕外，
        用户看到的是"界面缺一块"，而**没有任何报错**。

        位置同样要夹：换显示器后旧坐标可能整个落在屏幕外（窗口"打开却看不见"）。
        """
        from PySide6.QtWidgets import QApplication
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        av = scr.availableGeometry()
        f = self.frameGeometry()
        if f.width() > av.width() or f.height() > av.height():
            self.resize(min(f.width(), av.width()), min(f.height(), av.height()))
            f = self.frameGeometry()
        x = min(max(f.x(), av.x()), av.x() + av.width() - f.width())
        y = min(max(f.y(), av.y()), av.y() + av.height() - f.height())
        self.move(max(av.x(), x), max(av.y(), y))

    def _restore(self):
        from PySide6.QtCore import QSettings
        s = QSettings("audiocheck", "gui")
        g = s.value("geometry")
        if g:
            self.restoreGeometry(g)
            self._clamp_to_screen()
        n = s.value("theme")
        if n in T.THEMES:
            # **必须走 apply_theme，不能只给 self.theme 赋值。**
            # 只赋值会让 QSS 停留在上一个主题、而 self.theme 已换成新主题：
            # __init__ 的顺序是 apply_theme(DARK) → _restore()，若这里直接赋值，
            # 全局 QSS 仍是深色，但之后新建的组件（StatCard 等）会按新主题取色，
            # 于是浅色配色被画在深色背景上。
            # 实测症状：深色底上画出 #141a26 的数值，几乎不可见——
            # 指标卡的数字是仪表盘的重点，结果它最不显眼。
            self.apply_theme(T.THEMES[n])
        # 种子色主题：记住上一次用过的种子，否则"换了个配色"关掉程序就没了。
        seed = s.value("seed")
        if seed:
            from .palette import theme_from_seed
            try:
                self.seed = seed
                self.seed_mode = s.value("seed_mode", "dark")
                self.seed_vibe = s.value("seed_vibe", "calm")
                self.apply_theme(theme_from_seed("seed", seed, self.seed_mode,
                                                 self.seed_vibe))
            except ValueError:                                 # noqa: BLE001
                # 生成不达标（理论上不会发生：`build()` 内部就 strict 自审）
                self.seed = self.seed_mode = None
                self.seed_vibe = "calm"

    def closeEvent(self, e):
        from PySide6.QtCore import QSettings
        s = QSettings("audiocheck", "gui")
        s.setValue("geometry", self.saveGeometry())
        s.setValue("theme", self.theme.name)
        s.setValue("seed", self.seed or "")
        s.setValue("seed_mode", self.seed_mode or "")
        s.setValue("seed_vibe", self.seed_vibe)
        for w in ([self.worker] if self.worker else []) + list(self._view_workers.values()):
            if w and w.isRunning():
                try:
                    w.stop()
                except AttributeError:
                    pass
                w.wait(1500)
        super().closeEvent(e)


def main(argv=None):
    import sys

    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("audiocheck")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # 字体三件事必须一起做（注册字体文件 → 设应用字体 → 写进主题令牌）：
    # 只做前两件而 QSS 里仍写着对不上的族名，中文照样是方框。见 `T.init_fonts`。
    fams = T.load_cjk_fonts()
    T.init_fonts(app)
    import matplotlib
    mf = T.mpl_font(fams)
    if mf:
        matplotlib.rcParams["font.sans-serif"] = [mf]
    matplotlib.rcParams["axes.unicode_minus"] = False

    w = MainWindow()
    w.setWindowIcon(T.app_icon(w.theme.p))
    w.resize(1460, 900)

    # --seed / --mode / --vibe：启动时就按种子色生成主题（`ui/palette.py`）。
    # 这几个参数与"把后面的参数当文件"不能混，先挑出来再处理文件。
    raw = list(argv or sys.argv[1:])
    seed = mode = vibe = None
    rest, i = [], 0
    while i < len(raw):
        a = raw[i]
        if a in ("--seed", "--mode", "--vibe") and i + 1 < len(raw):
            if a == "--seed":
                seed = raw[i + 1]
            elif a == "--mode":
                mode = raw[i + 1]
            else:
                vibe = raw[i + 1]
            i += 2
            continue
        rest.append(a)
        i += 1
    if seed:
        from .palette import theme_from_seed
        try:
            w.seed = seed
            w.seed_mode = mode or "dark"
            w.seed_vibe = vibe or w.seed_vibe
            w.apply_theme(theme_from_seed("seed", seed, w.seed_mode, w.seed_vibe))
        except ValueError as e:                                # noqa: BLE001
            print("种子色 %s 生成不达标：%s" % (seed, e))

    w.show()
    args = [a for a in rest if os.path.exists(a)]
    if args:
        files = collect_files(args)
        if files:
            w.enqueue(files)
            w.start()
    return app.exec()
