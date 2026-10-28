"""发布元数据的自洽判据：**入口点不能重名、声明的模块必须真实存在**。

## 为什么需要

2026-10-28 实测查出两个静默故障：

1. `audiocheck-edit` 同时出现在 `[project.scripts]`（CLI）和
   `[project.gui-scripts]`（GUI）。pip 对同名 console_script / gui_script 的
   落盘顺序不确定 ⇒ **命令行版编辑入口被 GUI 版覆盖**，用户敲
   `audiocheck-edit --batch x.json` 得到的是一个弹窗程序，什么也不输出。
2. pyproject 里声明的每个 `module:func` 都必须真的能 import。改文件名/函数名
   而忘记改 entry point 时，pip 安装成功、运行时才在启动瞬间炸。

这两类问题**都不会让构建失败**，只会在用户机器上表现为"命令不存在"或
"双击没反应"，所以必须有机器判据。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"
SRC = REPO / "src"


def _meta() -> dict:
    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)["project"]


def test_no_entry_point_name_collision():
    """同名入口在 scripts 与 gui-scripts 里出现 ⇒ pip 行为不确定、二者丢一。"""
    p = _meta()
    scripts = set(p.get("scripts", {}))
    gui = set(p.get("gui-scripts", {}))
    clash = scripts & gui
    assert not clash, (
        "入口点重名（pip 落盘顺序不确定，会静默丢失一个）：%s"
        % "、".join(sorted(clash)))


def test_every_entry_point_target_is_importable():
    """每个 `module:func` 必须能 import 且 attr 存在（否则装完启动即崩）。"""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    p = _meta()
    bad = []
    for group in ("scripts", "gui-scripts"):
        for name, target in p.get(group, {}).items():
            mod_name, _, attr = target.partition(":")
            try:
                mod = importlib.import_module(mod_name)
            except Exception as e:                                   # noqa: BLE001
                bad.append("%s → %s（import 失败：%s）" % (name, mod_name, e))
                continue
            if not hasattr(mod, attr):
                bad.append("%s → %s:%s（模块里没有这个属性）"
                           % (name, mod_name, attr))
    assert not bad, "入口点指向不存在的目标：\n  " + "\n  ".join(bad)


def test_console_entry_points_are_callable():
    """CLI 入口必须是可调用的 `main`（发现 `main` 被改名/变成类）。"""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    for group in ("scripts",):
        for name, target in _meta().get(group, {}).items():
            mod_name, _, attr = target.partition(":")
            fn = getattr(importlib.import_module(mod_name), attr)
            assert callable(fn), "%s 的入口 %s 不可调用" % (name, target)


def test_version_in_pyproject_matches_package():
    """`__version__` 与 pyproject 的 version 必须一致（发布物料全从这里取）。"""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    from audiocheck import __version__
    assert __version__ == _meta()["version"], (
        "版本不一致：包内 %s vs pyproject %s" % (__version__, _meta()["version"]))


def test_runtime_modules_are_in_pyinstaller_hiddenimports():
    """★ 新增的**运行时**模块必须列进 spec 的 `hiddenimports`。

    ## 为什么这条必须有

    PyInstaller 的静态分析对**动态导入**看不见（`pkgutil.iter_modules`、
    运行期 `importlib`、条件导入）。漏了的后果不是构建失败——构建永远成功——
    而是**打包后运行时 `ModuleNotFoundError`**，只有真实用户会踩到。
    本仓已经为此付过代价：`core/analyzers/*` 与 `core/edit/*` 的
    `hiddenimports` 清单都是这么来的（spec 的注释里记着）。

    2026-10-28 又踩了一次：新增 `diagnostics.py` 与 `ui/editor/jobs.py`
    （`jobs` 由 `shell.py` 直接 import，但 `diagnostics` 是在
    `window.main()` / `Job.run()` 内部**函数级导入**的 —— 静态分析看不到）。

    判据：把 spec 里 `hiddenimports` 列表中的字符串全抽出来，
    要求这些"关键运行时模块"都在里面。
    """
    spec = (REPO / "packaging" / "audiocheck.spec").read_text(encoding="utf-8")
    required = [
        "audiocheck.diagnostics",
        "audiocheck.ui.editor.jobs",
        "audiocheck.cli",
        "audiocheck.edit_cli",
        "audiocheck.core.edit.session",
        "audiocheck.core.edit.render",
        "audiocheck.ui.editor",
        "audiocheck.ui.editor.shell",
    ]
    missing = [m for m in required if '"%s"' % m not in spec]
    assert not missing, (
        "以下运行时模块没进 PyInstaller 的 hiddenimports，打包后会 "
        "ModuleNotFoundError：\n  " + "\n  ".join(missing))


def test_spec_disables_upx_everywhere():
    """★ UPX 必须在 **EXE 与 COLLECT 两处**都关掉。

    UPX 是杀软误报重灾区（社区共识，PyInstaller 官方文档也警告
    "Using UPX may end up corrupting a collected shared library"，
    已知案例包括 CFG-enabled Windows DLL 与 Qt6 插件）。
    2026-10-28 发现本仓的 EXE 段写了 `upx=False`、但 COLLECT 段仍是
    `upx=True` ⇒ 收进目录的 DLL 照样被压，误报动机一点没消。
    """
    spec = (REPO / "packaging" / "audiocheck.spec").read_text(encoding="utf-8")
    # 找所有 upx= 赋值，全部必须是 False
    import re
    vals = re.findall(r"upx\s*=\s*(\w+)", spec)
    assert vals, "spec 里应有 upx 设置"
    assert all(v == "False" for v in vals), (
        "spec 里存在 upx=True（误报风险）：%s" % vals)


def test_spec_does_not_exclude_load_bearing_modules():
    """★ excludes 里**不能**有"模块加载时就会被访问"的包。

    ## 这条判据的来历（同一个坑踩了两次）

    `packaging/audiocheck.spec` 顶部注释第 2 条早就写着：
    "早先这里列了 numpy.f2py（想减体积），结果 scipy 的 array_api_compat
    在**模块加载时**就要 import 它，EXE 一启动就 ModuleNotFoundError。"

    2026-10-28 我为了减体积又把它加回去了，构建**成功**（242 MB），
    冒烟测试才发现 EXE 一启动就
    `ModuleNotFoundError: No module named 'numpy.f2py'`。
    说明"注释里的坑"挡不住人——必须有机器判据。

    判据：禁止清单里的这些包一旦出现在 `excludes` 就红。
    """
    import re
    spec = (REPO / "packaging" / "audiocheck.spec").read_text(encoding="utf-8")
    m = re.search(r"excludes=\[(.*?)\n    \]", spec, re.S)
    assert m, "找不到 excludes 列表"
    body = m.group(1)
    excluded = set(re.findall(r'"([^"]+)"', body))

    # 这些模块在 import 链的**加载期**就被访问（不是延迟导入）
    forbidden = {
        "numpy": "整包不可能排除",
        "scipy": "整包不可能排除",
        # matplotlib 的 FigureCanvasQTAgg 在 import 期就 `from PIL import Image`
        "PIL": "matplotlib 的 Qt 后端在加载期 import PIL",
        "Pillow": "同上",
    }
    bad = [(k, v) for k, v in forbidden.items() if k in excluded]

    # ★★ **numpy 的任何子模块都不许排**（f2py / distutils / testing 全踩过）
    # scipy._external.array_api_compat._internal.clone_module 会在加载期遍历
    # numpy 的顶层属性，`numpy.__getattr__` 随即去 import 这些子模块。
    numpy_sub = sorted(m for m in excluded if m.startswith("numpy."))
    for m in numpy_sub:
        bad.append((m, "numpy 子模块在 scipy 加载期被 __getattr__ 导入"))

    assert not bad, (
        "excludes 里有加载期必需模块（EXE 会启动即崩）：\n  "
        + "\n  ".join("%s（%s）" % (k, why) for k, why in bad))


def test_spec_excludes_have_no_duplicates():
    """★ excludes 不能有重复项。

    PyInstaller 对重复的 `distutils` 会抛
    `ValueError: Target module "distutils" already imported as ExcludedModule(...)`
    ⇒ **整个构建失败**（2026-10-28 实测：加了一条 `distutils`，构建在
    Analysis 阶段直接异常，产物 0 MB）。
    """
    import re
    spec = (REPO / "packaging" / "audiocheck.spec").read_text(encoding="utf-8")
    m = re.search(r"excludes=\[(.*?)\n    \]", spec, re.S)
    assert m
    names = re.findall(r'"([^"]+)"', m.group(1))
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, "excludes 有重复项（会让 PyInstaller 抛 ValueError）：%s" % dupes
