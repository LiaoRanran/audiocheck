# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— audiocheck 单文件可执行。

要点（都是踩过的，别随手改）：
  1. **hiddenimports 必须显式列分析器**：core/analyzers/__init__.py 用
     pkgutil.iter_modules 动态发现，PyInstaller 的静态分析看不到这种导入，
     不打进包就会「一个分析器都没有」。
  2. **collect_submodules('scipy.signal')**：scipy.signal 里大量惰性导入
     （spline 滤波、重采样等），静态分析同样抓不到。
  3. **matplotlib 的 backend 走 Agg**：GUI 里已显式 use("Agg")，但打包后
     仍可能被拖进 Tk/Qt 依赖，体积暴涨。这里只收 matplotlib 数据文件。
  4. **不打包 tests/ 与 data/**：测试与示例数据不是运行时依赖。
  5. **excludes 砍掉用不到的大件**：PySide6 的 3D/QtWebEngine、numpy 的
     f2py、IPython/jupyter 栈，砍完通常能少 60–100 MB。
"""
import os

block_cipher = None

HERE = os.path.dirname(os.path.abspath(SPEC))

# 分析器必须显式列出（动态发现，静态分析看不见）。
# 注意这里列的是**模块名**，不是分析器的 key：`dc_offset` 分析器定义在
# loudness.py 里（DcOffsetAnalyzer），没有 analyzers/dc_offset.py 这个模块。
# 按 key 列会列出一个不存在的模块 —— 无害但会让人以为漏打了东西。
ANALYZERS = [
    "basic", "bitdepth", "codec", "loudness",
    "metadata", "normalization", "spectrum", "stereo", "verdict",
]

hiddenimports = (
    [f"audiocheck.core.analyzers.{a}" for a in ANALYZERS]
    + ["audiocheck.core", "audiocheck.core.dsp", "audiocheck.core.engine",
       "audiocheck.core.context", "audiocheck.core.metrics",
       "audiocheck.core.pipeline", "audiocheck.core.registry",
       "audiocheck.core.views", "audiocheck.ui", "audiocheck.ui.app",
       "audiocheck.cli", "audiocheck.config", "audiocheck.summarize",
       # 编辑工作站（P0–P5 新主线）：内核 + CLI + 入口都显式列出
       #（与 ANALYZERS 同理：动态导入的路径静态分析看不见）。
       "audiocheck.core.edit", "audiocheck.core.edit.ops",
       "audiocheck.core.edit.render", "audiocheck.core.edit.session",
       "audiocheck.core.edit.report", "audiocheck.core.edit.corpus",
       "audiocheck.core.edit.stft", "audiocheck.core.edit.declip",
       "audiocheck.core.edit.declick", "audiocheck.core.edit.dehum",
       "audiocheck.core.edit.denoise", "audiocheck.core.edit.metrics",
       "audiocheck.core.edit.playback", "audiocheck.edit_cli",
       "audiocheck.ui.editor", "audiocheck.ui.editor.shell",
       "audiocheck.ui.editor.ops", "audiocheck.ui.editor.inspector",
       "audiocheck.ui.editor.batch", "audiocheck.ui.editor.transport",
       "audiocheck.ui.editor.editor_view", "audiocheck.ui.editor.export_dialog",
       "soundfile", "mutagen", "mutagen.flac", "matplotlib.backends.backend_agg"]
)

a = Analysis(
    # 入口必须是**顶层模块 + 绝对导入**。直接指 src/audiocheck/ui/app.py 会让
    # PyInstaller 丢掉包上下文，`from .window import main` 立刻炸
    # ImportError: attempted relative import with no known parent package。
    [os.path.join(HERE, "launch_gui.py")],
    pathex=[os.path.join(HERE, "..", "src")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 砍掉用不到的大件。**每条都必须是实测过的**——
    # 早先这里列了 numpy.f2py（想减体积），结果 scipy 的 array_api_compat
    # 在**模块加载时**就要 import 它，EXE 一启动就 ModuleNotFoundError。
    # 构建成功 ≠ 能跑，exclude 列表必须逐个验证。
    excludes=[
        "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DQuick",
        "PySide6.Qt3DRender", "PySide6.QtWebEngine", "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
        "PySide6.QtDataVisualization", "PySide6.QtCharts",
        "IPython", "jupyter", "notebook", "pytest", "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="audiocheck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,   # Agent G 调研：UPX 是杀软误报重灾区（ClamAV/AVG 直接报毒）——关。
    console=False,                    # GUI 程序：不弹控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 图标由 build 脚本按平台注入；缺文件时 PyInstaller 只是警告
    icon=os.path.join(HERE, "icon.ico") if os.path.exists(os.path.join(HERE, "icon.ico")) else None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name="audiocheck",
)
