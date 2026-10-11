#!/usr/bin/env python3
"""audiocheck 桌面版（Qt / PySide6）

分层结构：ui_theme（配色/图标）→ ui_charts（5 个画布 + 表格委托）→ 本文件（主窗口）。
比市面工具多做的事：
  · 判定「可解释」：同时给出真实截止点、砖墙陡度、容器奈奎斯特，三者对照下结论
  · 顺手做音乐特性：七频段能量、DR、削波、立体声相关与左右差
  · 独有的「能不能用它来评耳机」结论（band_def 与推荐引擎一致）
  · 多文件叠图对比 + 批次分布概览 + 可导出 HTML/JSON/CSV/PNG

运行： python audiocheck_gui.py [文件或目录 ...]
"""
import json
import os
import sys

from PySide6.QtCore import QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QSizePolicy, QSpinBox, QSplitter,
                               QStatusBar, QTabWidget, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from ..config import BAND_CN, BANDS, Thresholds
from ..core.analyzers.metadata import brief as meta_brief
from ..core.analyzers.metadata import tag_rows as meta_rows
from ..core.engine import analyze as analyze_file
from ..core.engine import collect_files
from ..core.views import spectrogram as view_spectrogram
from ..core.views import waterfall as view_waterfall
from . import theme as T
from .charts import (BandCanvas, CompareCanvas, OverviewCanvas, SpectrogramCanvas,
                     SpectrumCanvas, VerdictDelegate, WaterfallCanvas)
from .widgets import Gauge, KVTable, fmt_num, human_khz

#: 运行期阈值（设置面板可改，随后续分析生效）
TH = Thresholds()

LEVEL_CN = T.LEVEL_CN


# ------------------------------------------------------------------ 线程
class Worker(QThread):
    one = Signal(dict)
    fail = Signal(str, str)
    tick = Signal(int, int, str)
    done = Signal(int)

    def __init__(self, files, deep=True, thresholds=None):
        super().__init__()
        self.files, self.deep, self._stop = list(files), deep, False
        self.th = thresholds or Thresholds()

    def stop(self):
        self._stop = True

    def run(self):
        n, ok = len(self.files), 0
        for i, p in enumerate(self.files, 1):
            if self._stop:
                break
            self.tick.emit(i, n, os.path.basename(p))
            try:
                r = analyze_file(p, deep=self.deep, thresholds=self.th)
                self.one.emit(r)
                ok += 1
            except Exception as e:                                  # noqa: BLE001
                self.fail.emit(p, str(e))
        self.done.emit(ok)


class SpectroWorker(QThread):
    """频谱图 / 瀑布图共用：kind = "spec" | "wfall"。"""
    ready = Signal(str, str, object, object, object)

    def __init__(self, path, kind="spec"):
        super().__init__()
        self.path, self.kind = path, kind

    def run(self):
        try:
            fn = view_spectrogram if self.kind == "spec" else view_waterfall
            t, f, S = fn(self.path)
            self.ready.emit(self.path, self.kind, t, f, S)
        except Exception:                                           # noqa: BLE001
            self.ready.emit(self.path, self.kind, None, None, None)


# ------------------------------------------------------------------ 设置
class SettingsDialog(QDialog):
    def __init__(self, parent, deep=True):
        super().__init__(parent)
        self.setWindowTitle("设置 · 判定阈值")
        self.setMinimumWidth(430)
        v = QVBoxLayout(self)
        v.setSpacing(12)

        self.cb_deep = QCheckBox("深度分析（计算频谱 / 音乐特性）")
        self.cb_deep.setChecked(deep)
        v.addWidget(self.cb_deep)

        tip = QLabel("阈值放宽会把更多文件判为「合格」。改动只影响本机判定，不影响已存结果。")
        tip.setWordWrap(True)
        tip.setObjectName("AppSub")
        v.addWidget(tip)

        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(9)
        self.sp = {}
        rows = [
            ("cd_full", "44.1k 满带宽门槛 ≥", 1.0, 22.0, .1, " kHz"),
            ("ltd", "带限 / 有损分界", 10.0, 21.0, .1, " kHz"),
            ("lossy", "有损 / 强有损分界", 8.0, 19.0, .1, " kHz"),
            ("full_48", "48k 真带宽门槛 ≥", 21.0, 24.0, .1, " kHz"),
            ("up_44", "48k 疑似升频门槛 ≥", 20.0, 23.5, .1, " kHz"),
            ("hi_ok", "伪 Hi-Res 上限 ≤", 20.0, 28.0, .1, " kHz"),
            ("min_drop_db", "判定为截止的最小跌幅", 3.0, 60.0, 1.0, " dB/⅓oct"),
            ("brick_db", "砖墙判定陡度 >", 10.0, 120.0, 1.0, " dB/oct"),
            ("clip_pct", "削波告警 >", 0.0, 1.0, .005, " %"),
            ("loud_war_lufs", "响度战门槛 >", -20.0, 0.0, .5, " LUFS"),
            ("lra_flat", "动态压平门槛 <", 0.0, 12.0, .5, " LU"),
        ]
        for i, (k, lab, lo, hi, step, suf) in enumerate(rows):
            g.addWidget(QLabel(lab), i, 0)
            sp = QSpinBox()
            sp.setRange(int(lo * 1000), int(hi * 1000))
            sp.setSingleStep(int(step * 1000))
            sp.setValue(int(getattr(TH, k, 0) * 1000))
            sp.setSuffix(suf)
            sp.setFixedWidth(130)
            self.sp[k] = sp
            g.addWidget(sp, i, 1)
        v.addLayout(g)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def values(self):
        return {k: s.value() / 1000.0 for k, s in self.sp.items()}


# ------------------------------------------------------------------ 主窗口
class MainWindow(QMainWindow):
    COLS = ["文件名", "规格", "截止", "DR", "削波", "判定"]
    COL_W = {1: 74, 2: 62, 3: 46, 4: 62, 5: 78}

    def __init__(self):
        super().__init__()
        self.setWindowTitle("audiocheck · 音源质量检测")
        self.resize(1500, 920)
        self.setMinimumSize(1120, 700)
        self.setAcceptDrops(True)

        self.settings = QSettings("audiocheck", "gui")
        self.theme_name = self.settings.value("theme", "dark")
        self.p = T.THEMES.get(self.theme_name, T.DARK)

        self.items = []                 # [{path, file, result|None}]
        self._idx = {}
        self.queue = []
        self.worker = None
        self._view_workers = []
        self._spectro_cache = {}
        self._columns_done = False

        self._build()
        self._shortcuts()
        self.apply_theme(self.theme_name)
        self._restore_state()

    # ================================================= 构建
    def _build(self):
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self._header())
        cv.addWidget(self._build_toolbar())

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left, right = self._build_left(), self._build_right()
        left.setMinimumWidth(420)
        right.setMinimumWidth(520)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([600, 900])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        cv.addWidget(split, 1)
        self.split = split
        self.setCentralWidget(central)

        sb = QStatusBar()
        self.pb = QProgressBar()
        self.pb.setFixedWidth(220)
        self.pb.setVisible(False)
        self.lbl_stat = QLabel("就绪")
        sb.addWidget(self.lbl_stat, 1)
        sb.addPermanentWidget(self.pb)
        self.setStatusBar(sb)

    def _sep(self):
        f = QFrame()
        f.setObjectName("Sep")
        f.setFixedWidth(1)
        f.setFixedHeight(20)
        return f

    def _build_toolbar(self):
        bar = QWidget()
        bar.setObjectName("Toolbar")
        bar.setFixedHeight(50)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(5)

        self.act = {}
        defs = [
            ("add", "添加文件", "add", "Ctrl+O"), ("dir", "添加文件夹", "folder", "Ctrl+Shift+O"),
            (None, None, None, None),
            ("run", "开始分析", "play", "F5"), ("stop", "停止", "stop", "Esc"),
            (None, None, None, None),
            ("export", "导出报告", "export", "Ctrl+E"), ("clear", "清空列表", "trash", "Ctrl+L"),
        ]
        for key, tip, ico, sc in defs:
            if key is None:
                h.addWidget(self._sep())
                continue
            b = QToolButton()
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setText(tip)
            b.setToolTip("%s%s" % (tip, ("  (%s)" % sc) if sc else ""))
            b.setIconSize(QSize(17, 17))
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("Primary" if key == "run" else "Tool")
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
        self.cb_filter = QComboBox()
        self.cb_filter.addItems(["全部", "仅不达标", "仅有疑", "仅合格", "仅未分析"])
        self.cb_filter.setFixedWidth(112)
        self.cb_filter.currentIndexChanged.connect(self.refresh_table)
        h.addWidget(self.cb_filter)

        self.btn_theme = QToolButton()
        self.btn_theme.setToolTip("切换主题  (Ctrl+D)")
        self.btn_theme.setIconSize(QSize(17, 17))
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        self.btn_theme.clicked.connect(self.toggle_theme)
        h.addWidget(self.btn_theme)

        self.btn_set = QToolButton()
        self.btn_set.setToolTip("设置判定阈值")
        self.btn_set.setIconSize(QSize(17, 17))
        self.btn_set.setCursor(Qt.PointingHandCursor)
        self.btn_set.clicked.connect(self.open_settings)
        h.addWidget(self.btn_set)

        return bar

    def _header(self):
        head = QWidget()
        head.setObjectName("HeaderBar")
        head.setFixedHeight(38)
        h = QHBoxLayout(head)
        h.setContentsMargins(14, 4, 14, 4)
        h.setSpacing(10)
        self.lbl_icon = QLabel()
        h.addWidget(self.lbl_icon)
        t = QLabel("audiocheck")
        t.setObjectName("AppTitle")
        h.addWidget(t)
        s = QLabel("音源质量检测 · 看内容，不看标签")
        s.setObjectName("AppSub")
        h.addWidget(s)
        h.addStretch(1)
        self.lbl_meta = QLabel("")
        self.lbl_meta.setObjectName("HeaderMeta")
        h.addWidget(self.lbl_meta)
        return head

    def _build_left(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 6, 12)
        v.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(7)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("搜索文件名…   (Ctrl+F)")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self.refresh_table)
        row.addWidget(self.ed_search, 1)
        self.lbl_count = QLabel("0")
        self.lbl_count.setObjectName("AppSub")
        row.addWidget(self.lbl_count)
        v.addLayout(row)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.ctx_menu)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.table.doubleClicked.connect(lambda i: os.startfile(
            self.items[self._idx[self._path_at(i.row())]]["path"] if self._path_at(i.row()) in self._idx else "."))
        self.delegate = VerdictDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)          # 文件名吃掉剩余宽度
        hh.setMinimumSectionSize(36)
        for i, px in self.COL_W.items():          # 别用 w 当循环变量：外层 w 是 QWidget
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, px)
        hh.setHighlightSections(False)
        self.table.verticalHeader().setDefaultSectionSize(31)
        self.table.setMinimumWidth(390)
        self.table.horizontalHeaderItem(2).setToolTip("真实截止频率（内容层实测，非标称）")
        v.addWidget(self.table, 1)

        self.lbl_drop = QLabel("把文件或整个文件夹拖到这里")
        self.lbl_drop.setObjectName("Drop")
        self.lbl_drop.setAlignment(Qt.AlignCenter)
        v.addWidget(self.lbl_drop)
        return w

    def _build_right(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 10, 12, 12)
        v.setSpacing(10)

        # ---- 结论条（左：结论与文件；右：Hero 数字）----
        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        bh = QHBoxLayout(self.banner)
        bh.setContentsMargins(16, 12, 16, 12)
        bh.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(3)
        self.lbl_verdict = QLabel("—— 选中左侧条目查看详情 ——")
        self.lbl_verdict.setStyleSheet("font-size:14.5px;font-weight:700;")
        self.lbl_file = QLabel("")
        self.lbl_file.setObjectName("AppSub")
        left.addWidget(self.lbl_verdict)
        left.addWidget(self.lbl_file)
        bh.addLayout(left, 1)
        self.gauge = Gauge(126)
        bh.addWidget(self.gauge, 0, Qt.AlignVCenter)
        v.addWidget(self.banner)

        # ---- 总览页：指标卡 + 建议（放进标签页，标签区才有足够高度）----
        ov_page = QWidget()
        ov = QVBoxLayout(ov_page)
        ov.setContentsMargins(14, 14, 14, 14)
        ov.setSpacing(12)
        self.grid = QGridLayout()
        self.grid.setSpacing(7)
        ov.addLayout(self.grid)
        self.lbl_adv_title = QLabel("能不能用它来评耳机")
        self.lbl_adv_title.setObjectName("SectionTitle")
        ov.addWidget(self.lbl_adv_title)
        self.lbl_advice = QLabel("—")
        self.lbl_advice.setObjectName("Advice")
        self.lbl_advice.setWordWrap(True)
        self.lbl_advice.setAlignment(Qt.AlignTop)
        ov.addWidget(self.lbl_advice, 1)

        # ---- 视图标签页 ----
        self.tabs = QTabWidget()
        self.cv_spec = SpectrumCanvas()
        self.cv_spectro = SpectrogramCanvas()
        self.cv_wfall = WaterfallCanvas()
        self.cv_band = BandCanvas()
        self.cv_cmp = CompareCanvas()
        self.cv_ovw = OverviewCanvas()

        # 元数据 / 全指标：左右并排
        meta_page = QWidget()
        mh = QSplitter(Qt.Horizontal)
        mh.setChildrenCollapsible(False)
        left_box = QWidget()
        lv2 = QVBoxLayout(left_box)
        lv2.setContentsMargins(10, 10, 5, 10)
        t1 = QLabel("容器内附带的标签信息")
        t1.setObjectName("SectionTitle")
        lv2.addWidget(t1)
        self.tb_meta = KVTable()
        lv2.addWidget(self.tb_meta, 1)
        right_box = QWidget()
        rv2 = QVBoxLayout(right_box)
        rv2.setContentsMargins(5, 10, 10, 10)
        t2 = QLabel("全部测量指标")
        t2.setObjectName("SectionTitle")
        rv2.addWidget(t2)
        self.tb_metrics = KVTable()
        rv2.addWidget(self.tb_metrics, 1)
        mh.addWidget(left_box)
        mh.addWidget(right_box)
        mh.setSizes([420, 460])
        mv = QVBoxLayout(meta_page)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.addWidget(mh)

        for cv, name, tip in ((ov_page, "总览", "结论、18 项指标与「能不能用来评耳机」建议"),
                              (self.cv_spec, "频谱曲线", "频响曲线 + 真实截止点，可悬停读数"),
                              (self.cv_spectro, "频谱图", "时间 × 频率 × 能量"),
                              (self.cv_wfall, "瀑布图", "3D 瀑布频谱，可拖拽旋转"),
                              (self.cv_band, "频段能量", "七频段能量占比"),
                              (meta_page, "元数据", "容器标签 + 全部测量指标"),
                              (self.cv_cmp, "对比", "多选后叠加频谱"),
                              (self.cv_ovw, "批次", "整批队列的分布")):
            self.tabs.addTab(cv, name)
            self.tabs.setTabToolTip(self.tabs.count() - 1, tip)
        v.addWidget(self.tabs, 1)
        return w

    # ================================================= 主题
    def apply_theme(self, name):
        self.theme_name = name
        self.p = T.THEMES.get(name, T.DARK)
        p = self.p
        app = QApplication.instance()
        app.setStyleSheet(T.qss(p))
        self.lbl_icon.setPixmap(T.make_pixmap("wave", p["accent"], 20, 2.0))
        self.setWindowIcon(T.app_icon(p))
        for k, b in self.act.items():
            col = "#ffffff" if k == "run" else p["text"]
            b.setIcon(T.icon({"add": "add", "dir": "folder", "run": "play", "stop": "stop",
                              "export": "export", "clear": "trash"}[k], col, 17))
        self.btn_theme.setIcon(T.icon("moon" if name == "dark" else "sun", p["text_dim"], 17))
        self.btn_set.setIcon(T.icon("gear", p["text_dim"], 17))
        self.delegate.apply_theme(p)
        for cv in (self.cv_spec, self.cv_spectro, self.cv_wfall, self.cv_band,
                   self.cv_cmp, self.cv_ovw):
            cv.apply_theme(p)
        for wdg in (self.gauge, self.tb_meta, self.tb_metrics):
            wdg.set_theme(p)
        self.cv_ovw.set_data([i["result"] for i in self.items if i["result"]])
        self._apply_banner()
        self.refresh_table()
        self.settings.setValue("theme", name)

    def toggle_theme(self):
        self.apply_theme("light" if self.theme_name == "dark" else "dark")

    def open_settings(self):
        d = SettingsDialog(self, self.cb_deep_value())
        if d.exec() == QDialog.Accepted:
            for k, v in d.values().items():
                setattr(TH, k, v)
            self.cb_deep_override = d.cb_deep.isChecked()
            self.settings.setValue("deep", self.cb_deep_override)
            self.status("阈值已更新——重新分析后生效")

    def cb_deep_value(self):
        if getattr(self, "cb_deep_override", None) is None:
            self.cb_deep_override = self.settings.value("deep", "true") in (True, "true")
        return self.cb_deep_override

    # ================================================= 数据
    def add_files(self):
        fs, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "", "音频 (*.flac *.wav *.mp3 *.m4a *.aac *.ogg *.opus *.aif *.aiff);;所有文件 (*)")
        if fs:
            self.enqueue(fs)

    def add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.enqueue(collect_files([d]))

    def enqueue(self, files):
        add = [f for f in files if f not in self._idx]
        if not add:
            self.status("没有新文件（重复项已跳过）")
            return
        for f in add:
            self._idx[f] = len(self.items)
            self.items.append({"path": f, "file": os.path.basename(f), "result": None})
        self.queue.extend(add)
        self.refresh_table()
        self.status("已加入 %d 个文件，按 F5 开始分析" % len(add))

    def start(self):
        if not self.queue:
            self.status("队列为空")
            return
        todo, self.queue = self.queue, []
        self.act["run"].setEnabled(False)
        self.act["stop"].setEnabled(True)
        self.pb.setVisible(True)
        self.pb.setMaximum(len(todo))
        self.pb.setValue(0)
        self.worker = Worker(todo, deep=self.cb_deep_value())
        self.worker.tick.connect(self._on_tick)
        self.worker.one.connect(self.add_result)
        self.worker.fail.connect(lambda p, e: (self._mark_failed(p),
                                               self.status("读取失败 %s：%s" % (os.path.basename(p), e[:60]))))
        self.worker.done.connect(self.finish)
        self.worker.start()

    def stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.status("已请求停止…")

    def _on_tick(self, i, n, name):
        self.pb.setMaximum(n)
        self.pb.setValue(i)
        self.status("分析中 %d/%d ｜ %s" % (i, n, name))

    def _mark_failed(self, path):
        i = self._idx.get(path)
        if i is not None:
            self.items[i]["result"] = {"path": path, "file": os.path.basename(path),
                                       "level": "bad", "verdict": "读取失败", "error": True}

    def add_result(self, r):
        i = self._idx.get(r["path"])
        if i is None:
            return
        self.items[i]["result"] = r
        self.refresh_table()

    def finish(self, n):
        self.act["run"].setEnabled(True)
        self.act["stop"].setEnabled(False)
        self.pb.setVisible(False)
        self.refresh_table()
        self.cv_ovw.set_data([i["result"] for i in self.items if i["result"]])
        rs = [i["result"] for i in self.items if i["result"]]
        cnt = {lv: sum(1 for r in rs if r.get("level") == lv) for lv in ("ok", "warn", "bad")}
        self.status("完成：本次 %d ｜ 共 %d 个文件 ｜ 合格 %d ｜ 有疑 %d ｜ 不达标 %d" % (
            n, len(rs), cnt["ok"], cnt["warn"], cnt["bad"]))

    def clear(self):
        self.items, self._idx, self.queue = [], {}, []
        self._spectro_cache.clear()
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
        self._apply_banner()
        self.status("已清空")

    def _remove_selected(self):
        paths = self.selected_paths()
        if not paths:
            return
        drop = set(paths)
        self.items = [i for i in self.items if i["path"] not in drop]
        self.queue = [q for q in self.queue if q not in drop]
        self._rebuild_index()
        self.refresh_table()
        self.status("已移除 %d 个条目" % len(paths))

    def _rebuild_index(self):
        self._idx = {it["path"]: k for k, it in enumerate(self.items)}

    # ================================================= 表格
    def visible_items(self):
        mode = self.cb_filter.currentText()
        key = self.ed_search.text().strip().lower()
        out = []
        for it in self.items:
            r = it["result"]
            if mode == "仅未分析":
                if r:
                    continue
            elif mode == "仅不达标":
                if not r or r.get("level") != "bad":
                    continue
            elif mode == "仅有疑":
                if not r or r.get("level") != "warn":
                    continue
            elif mode == "仅合格":
                if not r or r.get("level") != "ok":
                    continue
            if key and key not in it["file"].lower() and key not in it["path"].lower():
                continue
            out.append(it)
        return out

    def refresh_table(self):
        items = self.visible_items()
        hh = self.table.horizontalHeader()
        sort_col, order = hh.sortIndicatorSection(), hh.sortIndicatorOrder()

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for it in items:
            self._append_row(it)
        self.table.setSortingEnabled(True)
        if self.table.rowCount() and sort_col >= 0:
            self.table.sortItems(sort_col, order)

        n = len(self.items)
        done = sum(1 for i in self.items if i["result"])
        self.lbl_count.setText("%d / %d" % (len(items), n))
        self.cv_ovw.set_data([i["result"] for i in self.items if i["result"]])
        self.lbl_meta.setText("已分析 %d / %d ｜ 队列 %d" % (done, n, len(self.queue)))
        if not self._columns_done and self.table.rowCount():
            self._columns_done = True

    def _append_row(self, it):
        r = it["result"]
        row = self.table.rowCount()
        self.table.insertRow(row)
        lv = (r or {}).get("level")
        if r:
            spec = "%.1fk/%s" % (r["sr"] / 1000, str(r["bits"]).replace("PCM_", ""))
            cut = "%.2fk" % r["cutoff_khz"] if r.get("cutoff_khz") else "—"
            vals = [it["file"], spec, cut, "%.1f" % (r.get("dr_db") or 0),
                    "%.2f%%" % (r.get("clip_pct") or 0), LEVEL_CN.get(lv, "?")]
        else:
            vals = [it["file"], "…", "—", "—", "—", "待分析"]
        for c, txt in enumerate(vals):
            cell = QTableWidgetItem(txt)
            cell.setData(Qt.UserRole, it["path"])
            cell.setData(Qt.UserRole + 1, lv or "")
            tip = (r or {}).get("verdict", "尚未分析")
            if c == 1 and r:
                tip = "%s · %d 声道 · %.1f MB · 约 %d kbps" % (
                    r["bits"], r["channels"], r["size_mb"], r.get("kbps") or 0)
            cell.setToolTip(tip)
            if c in (2, 3, 4):
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if not r:
                cell.setForeground(QColor(self.p["text_faint"]))
            self.table.setItem(row, c, cell)

    def _path_at(self, row):
        it = self.table.item(row, 0)
        return it.data(Qt.UserRole) if it else None

    def _open_row(self, index):
        p = self._path_at(index.row())
        if p and os.path.exists(p):
            os.startfile(os.path.dirname(p))

    def selected_paths(self):
        return [i.data(Qt.UserRole) for i in self.table.selectedItems() if i.column() == 0]

    def selected_results(self):
        out = []
        for p in self.selected_paths():
            i = self._idx.get(p)
            if i is not None and self.items[i]["result"]:
                out.append(self.items[i]["result"])
        return out

    # ================================================= 选中联动
    def on_select(self):
        mine = [i for i in self.table.selectedItems() if i.column() == 0]
        if not mine:
            return
        if len(mine) > 1:                                   # 多选 → 自动切对比视图
            rs = self.selected_results()
            if len(rs) > 1:
                self.cv_cmp.set_data([(r, r.get("curve")) for r in rs if r.get("curve")])
                self.tabs.setCurrentWidget(self.cv_cmp)
            return
        p = mine[0].data(Qt.UserRole)
        i = self._idx.get(p)
        r = self.items[i]["result"] if i is not None else None
        if not r:
            self._last_path = None
            self._apply_banner(None)
            self.cv_spec.set_data(None)
            self.cv_band.set_data(None)
            return
        if getattr(self, "_last_path", None) == p:      # 选中未变则不重算（表格重建会狂发此信号）
            return
        self._last_path = p
        self._apply_banner(r)
        self._cards(r)
        self.cv_spec.set_data(r)
        self.cv_band.set_data(r)
        self._fill_meta(r)
        self.lbl_advice.setText(r.get("advice", "—"))
        self._load_view(r["path"], "spec")
        self._load_view(r["path"], "wfall")

    def _apply_banner(self, r=None):
        p = self.p
        if r is None:
            self.banner.setStyleSheet("QFrame#Banner{background:%s;border:1px solid %s;"
                                      "border-radius:10px;}" % (p["surface2"], p["border"]))
            self.lbl_verdict.setText("—— 选中左侧条目查看详情 ——")
            self.lbl_verdict.setStyleSheet("font-size:14.5px;font-weight:700;color:%s;" % p["text_dim"])
            self.lbl_file.setText("")
            self.gauge.set_data(None, None, "—", "真实截止", "")
            return
        lv = r.get("level", "warn")
        self.banner.setStyleSheet(
            "QFrame#Banner{background:%s;border:1px solid %s;border-radius:10px;}"
            % (p.get(lv + "_bg", p["surface2"]), p.get(lv + "_border", p["border"])))
        self.lbl_verdict.setText(r.get("verdict", ""))
        self.lbl_verdict.setStyleSheet("font-size:14.5px;font-weight:700;color:%s;" % p.get(lv, p["text"]))
        self.lbl_file.setText("%s    ·    %s    ·    %s" % (
            r["file"], meta_brief(r.get("meta") or {}), os.path.dirname(r["path"])))
        cut = r.get("cutoff_khz")
        ratio = r.get("cutoff_ratio")
        self.gauge.set_data(ratio, lv, ("%.2f" % cut) if cut else "—", "kHz 真实截止",
                            ("占奈奎斯特 %.0f%%" % (ratio * 100)) if ratio else "")

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
            ("体积 / 码率", "%.1f MB%s" % (r["size_mb"], " · ≈%dk" % r["kbps"] if r.get("kbps") else "")),
            ("真实截止", ("%.2f kHz" % r["cutoff_khz"]) if r.get("cutoff_khz") else "—"),
            ("占奈奎斯特", fmt_num((r.get("cutoff_ratio") or 0) * 100, "%", 0)),
            ("砖墙陡度", ("%.0f dB/oct" % r["brickwall_db_per_oct"]) if r.get("brickwall_db_per_oct") else "—"),
            ("峰值 / RMS", "%.2f / %.2f" % (r.get("peak_dbfs") or 0, r.get("rms_dbfs") or 0)),
            ("真峰值 ITU-R", ("%+.2f dBTP" % tp) if tp is not None else "—"),
            ("集成响度", fmt_num(r.get("lufs"), " LUFS", 1)),
            ("动态范围 DR", fmt_num(r.get("dr_db"), " dB", 1)),
            ("响度范围 LRA", fmt_num(r.get("lra"), " LU", 1)),
            ("波峰因数 PLR", fmt_num(r.get("plr") if r.get("plr") is not None else r.get("crest_db"), " dB", 1)),
            ("削波样本", fmt_num(r.get("clip_pct"), "%", 3)),
            ("相位相关", fmt_num(r.get("corr", r.get("phase_corr")), "", 2)),
            ("声道宽度", fmt_num(r.get("width_db"), " dB", 1)),
            ("频段重心", human_khz(r.get("centroid_hz"))),
            ("编码器串 vendor", (r.get("meta") or {}).get("vendor") or "—"),
        ]
        for i, (k, v) in enumerate(pairs):
            w = QWidget()
            w.setObjectName("Metric")
            vv = QVBoxLayout(w)
            vv.setContentsMargins(8, 4, 8, 4)
            vv.setSpacing(0)
            a = QLabel(k)
            a.setObjectName("MetricK")
            b = QLabel(str(v))
            b.setObjectName("MetricV")
            b.setToolTip(str(v))
            vv.addWidget(a)
            vv.addWidget(b)
            self.grid.addWidget(w, i // 6, i % 6)

    # ---- 元数据 / 全指标 ----
    @staticmethod
    def _metric_rows(r):
        g = lambda k: r.get(k)                                            # noqa: E731
        rows = [
            ("容器 / 编码", "%s ｜ %s ｜ %d声道" % (r["format"], r["bits"], r["channels"])),
            ("采样率 / 位深", "%.0f Hz ｜ %s" % (r["sr"], r["bits"])),
            ("时长 / 体积 / 码率", "%d:%02d ｜ %.1f MB ｜ 约 %s kbps" % (
                r["seconds"] // 60, r["seconds"] % 60, r["size_mb"], g("kbps") or "—")),
            ("编码器串 (vendor)", (r.get("meta") or {}).get("vendor") or "—"),
            ("", ""),
            ("真实截止", human_khz(g("cutoff_hz"))),
            ("占奈奎斯特", fmt_num((g("cutoff_ratio") or 0) * 100, " %", 1)),
            ("砖墙陡度", fmt_num(g("brickwall_db_per_oct"), " dB/oct", 1)),
            ("频谱重心", human_khz(g("centroid_hz"))),
            ("85% 滚降点", human_khz(g("rolloff85_hz"))),
            ("95% 滚降点", human_khz(g("rolloff95_hz"))),
            ("谱平坦度", fmt_num(g("flatness"), "", 5)),
            ("噪声底 (5%分位)", fmt_num(g("noise_floor_db"), " dB", 1)),
            ("", ""),
            ("峰值 / RMS", "%.2f / %.2f dBFS" % (g("peak_dbfs") or 0, g("rms_dbfs") or 0)),
            ("真峰值 (ITU-R BS.1770)", fmt_num(g("true_peak_dbfs"), " dBTP", 2)),
            ("集成响度 (EBU R128)", fmt_num(g("lufs"), " LUFS", 1)),
            ("响度范围 LRA", fmt_num(g("lra"), " LU", 1)),
            ("短时最大 / 最小", "%s / %s LUFS" % (fmt_num(g("st_max"), "", 1), fmt_num(g("st_min"), "", 1))),
            ("Peak-to-Loudness PLR", fmt_num(g("plr"), " dB", 1)),
            ("动态范围 DR (近似 TT)", fmt_num(g("dr_db"), " dB", 1)),
            ("波峰因数", fmt_num(g("crest_db"), " dB", 1)),
            ("削波样本", fmt_num(g("clip_pct"), " %", 4)),
            ("直流偏移", fmt_num(g("dc_max_dbfs"), " dBFS", 1)),
            ("", ""),
            ("相位相关", fmt_num(g("corr") if g("corr") is not None else g("phase_corr"), "", 3)),
            ("声道宽度 (M/S)", fmt_num(g("width_db"), " dB", 1)),
            ("单声道兼容性", g("mono_compat") or "—"),
            ("L/R 电平差", fmt_num(g("lr_level_diff_db"), " dB", 2)),
        ]
        if r.get("bands_pct"):
            rows.append(("", ""))
            for k, _, _ in BANDS:
                rows.append(("频段 " + BAND_CN[k], fmt_num(r["bands_pct"].get(k), " %", 1)))
        if r.get("dsp_error"):
            rows.append(("DSP 告警", r["dsp_error"]))
        return [(a, b) for a, b in rows if a or b]

    def _fill_meta(self, r):
        md = r.get("meta") or {}
        rows = meta_rows(md)
        self.tb_meta.set_rows(rows, mono_keys={"ISRC", "条码 / UPC", "目录号", "版权"})
        self.tb_metrics.set_rows(self._metric_rows(r), mono_keys={"编码器串 (vendor)"})

    def _cv_of(self, kind):
        return self.cv_spectro if kind == "spec" else self.cv_wfall

    def _load_view(self, path, kind):
        cv = self._cv_of(kind)
        key = (path, kind)
        if key in self._spectro_cache:
            cv.set_data(self.items[self._idx[path]]["result"] if path in self._idx else None,
                        self._spectro_cache[key])
            return
        cv.set_data(None, None, "频谱图生成中…" if kind == "spec" else "瀑布图生成中…")
        # 关键：不能只存一个属性。频谱图与瀑布图会同时开工，
        # 后一个覆盖前一个 → 运行中的 QThread 被 GC → Qt 直接崩。
        # 且只在「已停止」时回收：finished 信号发出时线程可能仍在收尾。
        self._view_workers = [x for x in self._view_workers if x.isRunning()][-6:]
        w = SpectroWorker(path, kind)
        self._view_workers.append(w)
        w.ready.connect(self._view_ready)
        w.start()

    def _view_ready(self, path, kind, t, f, S):
        if S is None:
            return
        self._spectro_cache[(path, kind)] = (t, f, S)
        while len(self._spectro_cache) > 12:
            self._spectro_cache.pop(next(iter(self._spectro_cache)))
        sel = [i for i in self.table.selectedItems() if i.column() == 0]
        if len(sel) == 1 and sel[0].data(Qt.UserRole) == path:
            i = self._idx.get(path)
            self._cv_of(kind).set_data(self.items[i]["result"] if i is not None else None, (t, f, S))

    # ================================================= 右键 / 导出
    def ctx_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        paths = self.selected_paths() or [item.data(Qt.UserRole)]
        if item.data(Qt.UserRole) not in paths:
            paths = [item.data(Qt.UserRole)]
        rs = [self.items[self._idx[p]]["result"] for p in paths
              if p in self._idx and self.items[self._idx[p]]["result"]]
        m = QMenu(self)
        if len(paths) == 1:
            p = paths[0]
            m.addAction(T.icon("folder", self.p["text"], 15), "打开所在文件夹",
                        lambda: os.startfile(os.path.dirname(p)))
            if rs:
                m.addAction(T.icon("copy", self.p["text"], 15), "复制结论",
                            lambda: QApplication.clipboard().setText(
                                "%s\n%s" % (rs[0]["file"], rs[0].get("verdict", ""))))
                m.addAction(T.icon("copy", self.p["text"], 15), "复制完整信息",
                            lambda: QApplication.clipboard().setText(self._as_text(rs[0])))
            m.addAction(T.icon("reload", self.p["text"], 15), "重新分析",
                        lambda: self.requeue([p]))
        else:
            m.addAction(T.icon("layers", self.p["text"], 15), "叠图对比这 %d 个" % len(paths),
                        lambda: (self.cv_cmp.set_data([(r, r.get("curve")) for r in rs]),
                                 self.tabs.setCurrentWidget(self.cv_cmp)))
        m.addSeparator()
        m.addAction(T.icon("trash", self.p["bad"], 15), "从列表移除", self._remove_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def requeue(self, paths):
        for p in paths:
            i = self._idx.get(p)
            if i is not None:
                self.items[i]["result"] = None
        self.queue.extend(paths)
        self.refresh_table()
        self.start()

    @staticmethod
    def _as_text(r):
        L = ["文件: %s" % r["file"], "路径: %s" % r["path"],
             "格式: %s %.1fkHz %s %dch %.2fMB" % (r["format"], r["sr"] / 1000, r["bits"],
                                                  r["channels"], r["size_mb"])]
        if r.get("cutoff_khz"):
            L.append("真实截止: %.2f kHz（占奈奎斯特 %.0f%%）砖墙 %.1f dB/oct" % (
                r["cutoff_khz"], (r.get("cutoff_ratio") or 0) * 100, r.get("brickwall_db_per_oct") or 0))
        L.append("电平: 峰值 %.2f dBFS ｜ RMS %.2f ｜ DR %.1f ｜ 削波 %.3f%%" % (
            r.get("peak_dbfs") or 0, r.get("rms_dbfs") or 0, r.get("dr_db") or 0, r.get("clip_pct") or 0))
        if r.get("bands_pct"):
            L.append("频段: " + "  ".join("%s %.0f%%" % (BAND_CN[k], r["bands_pct"].get(k, 0))
                                          for k, _, _ in BANDS))
        L.append("判定: %s" % r.get("verdict", ""))
        if r.get("advice"):
            L.append("建议: %s" % r["advice"])
        return "\n".join(L)

    def export(self):
        rs = [i["result"] for i in self.items if i["result"]]
        if not rs:
            QMessageBox.information(self, "无数据", "先分析一些文件再导出。")
            return
        p, flt = QFileDialog.getSaveFileName(
            self, "导出报告", "audiocheck_report",
            "HTML 报告 (*.html);;JSON 数据 (*.json);;CSV 表格 (*.csv);;当前视图 PNG (*.png)")
        if not p:
            return
        try:
            if p.endswith(".png"):
                self.tabs.currentWidget().fig.savefig(p, dpi=160, facecolor=self.p["surface"])
            elif p.endswith(".json"):
                json.dump([{k: v for k, v in r.items() if k != "curve"} for r in rs],
                          open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            elif p.endswith(".csv"):
                self._export_csv(p, rs)
            else:
                self._export_html(p, rs)
            self.status("已导出 %s" % p)
        except Exception as e:                                          # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))

    def _export_csv(self, path, rs):
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["文件", "路径", "格式", "采样率", "位深", "声道", "秒", "MB", "截止kHz",
                        "占奈奎斯特", "砖墙dB/oct", "峰值dBFS", "RMS", "DR", "波峰因数", "削波%",
                        "相位相关", "L/R差", "判定"] + [BAND_CN[k[0]] + "%" for k in BANDS])
            for r in rs:
                b = r.get("bands_pct") or {}
                w.writerow([r["file"], r["path"], r["format"], r["sr"], r["bits"], r["channels"],
                            r["seconds"], r["size_mb"], r.get("cutoff_khz"), r.get("cutoff_ratio"),
                            r.get("brickwall_db_per_oct"), r.get("peak_dbfs"), r.get("rms_dbfs"),
                            r.get("dr_db"), r.get("crest_db"), r.get("clip_pct"),
                            r.get("phase_corr"), r.get("lr_level_diff_db"), r.get("verdict")]
                           + [b.get(k[0], "") for k in BANDS])

    def _export_html(self, path, rs):
        import html
        p = self.p
        cnt = {lv: sum(1 for r in rs if r.get("level") == lv) for lv in ("ok", "warn", "bad")}
        H = ["<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
             "<title>audiocheck 音源质量报告</title><style>",
             "body{background:%s;color:%s;font:14px/1.65 'Microsoft YaHei',system-ui,sans-serif;"
             "margin:0;padding:34px 40px}" % (p["bg"], p["text"]),
             "h1{font-size:21px;margin:0 0 6px}.sub{color:%s;font-size:12.5px;margin-bottom:4px}" % p["text_dim"],
             ".cards{display:flex;gap:12px;margin:22px 0 26px}",
             ".card{flex:1;background:%s;border:1px solid %s;border-radius:12px;padding:14px 18px}"
             % (p["surface"], p["border"]),
             ".card .k{color:%s;font-size:11.5px}.card .v{font-size:23px;font-weight:700;margin-top:3px}"
             % p["text_faint"],
             "table{width:100%%;border-collapse:collapse;font-size:12.5px;background:%s;"
             "border:1px solid %s;border-radius:12px;overflow:hidden}" % (p["surface"], p["border"]),
             "th,td{padding:9px 11px;text-align:left;border-bottom:1px solid %s;vertical-align:top}"
             % p["border"],
             "th{color:%s;font-weight:600;font-size:11.5px;background:%s}" % (p["text_dim"], p["surface2"]),
             "tr:last-child td{border-bottom:0}",
             ".ok{color:%s;font-weight:700}.warn{color:%s;font-weight:700}.bad{color:%s;font-weight:700}"
             % (p["ok"], p["warn"], p["bad"]),
             ".pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11.5px;font-weight:700}",
             ".pill.ok{background:%s;color:%s}.pill.warn{background:%s;color:%s}.pill.bad{background:%s;color:%s}"
             % (p["ok_bg"], p["ok"], p["warn_bg"], p["warn"], p["bad_bg"], p["bad"]),
             "code{color:%s;font-size:11.5px}" % p["text_faint"],
             "</style></head><body>",
             "<h1>audiocheck · 音源质量报告</h1>",
             "<div class='sub'>共 %d 个文件 ｜ 生成时间 %s ｜ 全部在本机分析，未上传任何文件</div>"
             % (len(rs), __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")),
             "<div class='cards'>",
             "<div class='card'><div class='k'>合格</div><div class='v ok'>%d</div></div>" % cnt["ok"],
             "<div class='card'><div class='k'>有疑</div><div class='v warn'>%d</div></div>" % cnt["warn"],
             "<div class='card'><div class='k'>不达标</div><div class='v bad'>%d</div></div>" % cnt["bad"],
             "<div class='card'><div class='k'>内容 &gt;20kHz</div><div class='v'>%d</div></div>"
             % sum(1 for r in rs if (r.get("cutoff_khz") or 0) >= 20),
             "</div>",
             "<table><tr><th>文件</th><th>规格</th><th>真实截止</th><th>DR</th><th>削波</th>"
             "<th>判定</th><th>能不能用来评耳机</th></tr>"]
        order = sorted(rs, key=lambda r: {"bad": 0, "warn": 1, "ok": 2}.get(r.get("level"), 3))
        for r in order:
            lv = r.get("level", "warn")
            H.append("<tr><td>%s<br><code>%s</code></td><td>%.1fkHz %s %dch</td><td>%s</td>"
                     "<td>%.1f</td><td>%.3f%%</td><td><span class='pill %s'>%s</span></td><td>%s</td></tr>" % (
                         html.escape(r["file"]), html.escape(os.path.dirname(r["path"])),
                         r["sr"] / 1000, r["bits"], r["channels"],
                         ("%.2f kHz" % r["cutoff_khz"]) if r.get("cutoff_khz") else "—",
                         r.get("dr_db") or 0, r.get("clip_pct") or 0, lv,
                         html.escape(r.get("verdict", "")), html.escape(r.get("advice", ""))))
        H.append("</table></body></html>")
        open(path, "w", encoding="utf-8").write("\n".join(H))

    # ================================================= 交互
    def _shortcuts(self):
        def bind(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(fn)
        bind("Ctrl+O", self.add_files)
        bind("Ctrl+Shift+O", self.add_dir)
        bind("F5", self.start)
        bind("Ctrl+R", self.start)
        bind("Esc", self.stop)
        bind("Ctrl+E", self.export)
        bind("Ctrl+L", self.clear)
        bind("Delete", self._remove_selected)
        bind("Ctrl+F", lambda: self.ed_search.setFocus())
        bind("Ctrl+D", self.toggle_theme)
        bind("Ctrl+T", self.open_settings)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drop_hot(True)

    def dragLeaveEvent(self, e):
        self._drop_hot(False)

    def _drop_hot(self, hot):
        self.lbl_drop.setObjectName("DropHot" if hot else "Drop")
        st = self.lbl_drop.style()
        st.unpolish(self.lbl_drop)
        st.polish(self.lbl_drop)
        self.lbl_drop.update()

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        files = collect_files(paths)
        if files:
            self.enqueue(files)
        elif paths:
            self.status("拖入的内容里没有可识别的音频文件")

    def status(self, t):
        self.lbl_stat.setText(t)

    def _restore_state(self):
        g = self.settings.value("geometry")
        if g:
            self.restoreGeometry(g)
        sp = self.settings.value("splitter")
        if sp:
            self.split.restoreState(sp)
        f = self.settings.value("filter")
        if f is not None:
            try:
                self.cb_filter.setCurrentIndex(int(f))
            except (TypeError, ValueError):
                pass

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.split.saveState())
        self.settings.setValue("filter", self.cb_filter.currentIndex())
        for w in [self.worker] + list(self._view_workers):
            if w and w.isRunning():
                try:
                    w.stop()
                except AttributeError:
                    pass
                w.wait(1500)
        super().closeEvent(e)


# ------------------------------------------------------------------ 入口
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("audiocheck")
    fams = T.load_cjk_fonts()
    fam = T.pick_family(fams)
    app.setFont(QFont(fam, 10))

    import matplotlib
    mf = T.mpl_font(fams)
    if mf:
        matplotlib.rcParams["font.sans-serif"] = [mf]
    matplotlib.rcParams["axes.unicode_minus"] = False

    w = MainWindow()
    w.show()
    args = [a for a in sys.argv[1:] if os.path.exists(a)]
    if args:
        files = collect_files(args)
        if files:
            w.enqueue(files)
            w.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
