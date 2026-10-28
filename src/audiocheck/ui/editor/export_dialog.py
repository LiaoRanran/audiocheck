"""导出预设：格式 / 子类型 / 抖动的**导出配方**（命名、落盘、可复算）。

## 为什么是这一层

渲染内核早就支持全参数导出（`render(..., subtype=..., dither=...)`：WAV/FLAC/AIFF 容器、
PCM_16/24/32/FLOAT 子类型、TPDF 抖动），但 GUI 的导出入口此前把口径**写死**成
"WAV + 源子类型"——内核的导出能力在 UI 上只露了一个角。本模块把"导出配方"变成
**可命名的预设**（与算子预设同一套落盘约定：tempdir 下的 JSON，用户可读可改跨会话）。

两条校验**镜像内核规则**（提前到弹窗里报，而不是让内核在渲染时抛）：
* 子类型必须装进目标容器（内核 `_check_subtype` 的规矩：FLAC 不收 FLOAT ⇒ 弹窗里直接
  不给这个选项，而不是让用户点完导出才收到报错）；
* 抖动只对整型子类型有意义（内核 `_check_dither` 的规矩：浮点上选抖动 = 明确拒绝）。
"""
from __future__ import annotations

import json
import os

import soundfile as sf

#: GUI 暴露的容器（内核 `_FORMAT_BY_EXT` 的 UI 侧口径；容器名与 libsndfile 一致）。
FORMATS: tuple[str, ...] = ("WAV", "FLAC", "AIFF")

#: 候选子类型全集（`available_subtypes` 用 `sf.check_format` 逐个过滤）。
_CANDIDATE_SUBTYPES: tuple[str, ...] = ("PCM_16", "PCM_24", "PCM_32", "FLOAT", "DOUBLE")

#: 扩展名（按容器）；FLAC 没有浮点子类型——不是本模块的判断，是 `sf.check_format` 的回答。
_EXT = {"WAV": ".wav", "FLAC": ".flac", "AIFF": ".aiff"}


def available_subtypes(fmt: str) -> list[str]:
    """容器能装下的子类型（**以 libsndfile 的回答为准**，不在本模块里背一张表）。"""
    return [s for s in _CANDIDATE_SUBTYPES if sf.check_format(fmt, s)]


def validate(fmt: str, subtype: str, dither: bool) -> str | None:
    """导出配方校验：返回错误文案或 `None`（= 可渲染）。

    与内核同规矩（提前报，避免"点完导出才炸"）：容器收不下该子类型、
    或浮点子类型上要求抖动，都在这里拦下。
    """
    if fmt not in FORMATS:
        return "不认识的容器 %r（可选：%s）" % (fmt, " / ".join(FORMATS))
    if not subtype:
        return "子类型不能为空"
    if not sf.check_format(fmt, subtype):
        return "容器 %s 装不下子类型 %s（可选：%s）" % (fmt, subtype,
                                                    " / ".join(available_subtypes(fmt)))
    if dither and subtype not in ("PCM_S8", "PCM_U8", "PCM_16", "PCM_24", "PCM_32"):
        return "抖动只对整型子类型有意义（%s 是浮点）——与内核 --dither 同一口径" % subtype
    return None


def filter_for(fmt: str) -> str:
    """文件对话框的扩展名过滤器（跟随所选容器）。"""
    return "%s (*%s)" % (fmt, _EXT.get(fmt, ".wav"))


def preset_path() -> str:
    """导出预设的落盘位置（与算子预设同一约定：tempdir，用户可读可改、跨会话）。"""
    import tempfile
    return os.path.join(tempfile.gettempdir(), "audiocheck_export_presets.json")


def save_export_preset(path: str, name: str, cfg: dict) -> None:
    """把导出配方存成命名预设（追加覆盖同名；与算子预设同一套健壮性口径）。"""
    presets: dict = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                presets = json.load(fh)
        except (OSError, ValueError, json.JSONDecodeError):
            presets = {}
    if not isinstance(presets, dict):
        presets = {}
    presets[name] = cfg
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(presets, fh, ensure_ascii=False, indent=2)


def load_export_presets(path: str) -> dict:
    """读导出预设（读不了返回空 dict，不抛——坏文件不许挡导出）。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ 对话框（延迟导入 Qt）
def build_export_dialog(parent=None):
    """建导出配方对话框（Qt 依赖**延迟到调用时**，纯函数部分可无 Qt 单测）。

    返回 `(dialog)`；`dialog.result_cfg()` 在接受后给出 `{"fmt","subtype","dither"}`。
    """
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QHBoxLayout,
        QLineEdit,
        QPushButton,
        QVBoxLayout,
    )

    class _ExportDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("导出配方")
            self._cfg = None
            v = QVBoxLayout(self)
            form = QFormLayout()
            self.fmt = QComboBox()
            self.fmt.setAccessibleName("容器格式")
            self.fmt.addItems(FORMATS)
            self.sub = QComboBox()
            self.sub.setAccessibleName("子类型")
            self.dither = QCheckBox("TPDF 抖动（±1 LSB，整型专用）")
            form.addRow("容器", self.fmt)
            form.addRow("子类型", self.sub)
            form.addRow("", self.dither)
            v.addLayout(form)
            # 预设行
            pr = QHBoxLayout()
            self.name = QLineEdit("我的导出")
            self.btn_save = QPushButton("存预设")
            self.btn_load = QPushButton("载预设")
            pr.addWidget(self.name)
            pr.addWidget(self.btn_save)
            pr.addWidget(self.btn_load)
            v.addLayout(pr)
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                  | QDialogButtonBox.StandardButton.Cancel)
            bb.accepted.connect(self._accept)
            bb.rejected.connect(self.reject)
            v.addWidget(bb)
            self.fmt.currentIndexChanged.connect(self._refill)
            self._refill()
            self.btn_save.clicked.connect(self._save)
            self.btn_load.clicked.connect(self._load)

        def _refill(self) -> None:
            cur = self.sub.currentText()
            self.sub.clear()
            self.sub.addItems(available_subtypes(self.fmt.currentText()))
            if cur in [self.sub.itemText(i) for i in range(self.sub.count())]:
                self.sub.setCurrentText(cur)

        def _accept(self) -> None:
            err = validate(self.fmt.currentText(), self.sub.currentText(),
                           self.dither.isChecked())
            if err:
                self.setWindowTitle("导出配方（✗ %s）" % err)
                return
            self._cfg = self.result_cfg()
            self.accept()

        def _save(self) -> None:
            cfg = self.result_cfg()
            err = validate(cfg["fmt"], cfg["subtype"], cfg["dither"])
            if err:
                self.setWindowTitle("导出配方（✗ 存预设被拒：%s）" % err)
                return
            save_export_preset(preset_path(), self.name.text().strip() or "未命名", cfg)
            self.setWindowTitle("导出配方（已存预设：%s）" % (self.name.text().strip() or "未命名"))

        def _load(self) -> None:
            presets = load_export_presets(preset_path())
            names = sorted(presets)
            if not names:
                self.setWindowTitle("导出配方（还没有预设）")
                return
            # 载最近一个（字典序最后 = 简单可复算的口径；用户也可手改 JSON）
            cfg = presets[names[-1]]
            if isinstance(cfg, dict):
                if cfg.get("fmt") in FORMATS:
                    self.fmt.setCurrentText(cfg["fmt"])
                if cfg.get("subtype"):
                    self.sub.setCurrentText(cfg["subtype"])
                self.dither.setChecked(bool(cfg.get("dither")))

        def result_cfg(self) -> dict:
            return {"fmt": self.fmt.currentText(), "subtype": self.sub.currentText(),
                    "dither": self.dither.isChecked()}

    return _ExportDialog(parent)
