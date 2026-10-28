"""打包入口的参数分派判据（**发布包里只有这一个 exe**，分派错了 CLI 就整块消失）。

## 为什么需要这组测试

2026-10-28 实测发现：`dist/audiocheck/audiocheck.exe --help` **卡住 180 s**——
入口被硬接在 `ui.app.main` 上，任何参数都会开窗口进主循环。而 zip 里
`audiocheck.exe` 是**唯一**的可执行文件，`pip` 安装路径又不在发布包用户手里
⇒ 压缩包用户拿到的产品**没有 CLI**，README 宣传的 `--json`/`--doctor`/
批量体检全部不可达。

这组测试直接调用 `packaging/launch_gui.py` 的 `_wants_gui`，把"哪些 argv
走 GUI、哪些走 CLI"钉死。它是**纯函数**，不需要起进程、不需要 Qt。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LAUNCH = REPO / "packaging" / "launch_gui.py"


def _load():
    spec = importlib.util.spec_from_file_location("_pkg_launch", LAUNCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_launch_script_exists():
    assert LAUNCH.is_file(), "打包入口脚本必须在（spec 里写死了这个路径）"


def test_no_args_opens_gui():
    """双击 exe（无参数）必须开窗口——这是 GUI 程序的基本预期。"""
    assert _load()._wants_gui([]) is True


def test_cli_flags_do_not_open_gui():
    """CLI 参数一个都不能掉进 GUI 分支（掉进去 = 卡死主循环、无输出）。"""
    m = _load()
    for argv in (["--help"], ["-h"], ["--version"], ["--doctor"],
                 ["--list-analyzers"], ["--edit"], ["--json", "o.json"],
                 ["a.wav"], ["D:/music"], ["--quiet", "x.flac"],
                 ["--seed", "0.5", "--help"]):
        # 带 --seed 的那条走 GUI（主题参数优先），其余必须走 CLI
        expect_cli = "--seed" not in argv
        got = m._wants_gui(argv)
        assert got is (not expect_cli), (
            "argv=%r 期望 %s，得到 %s" % (argv, "CLI" if expect_cli else "GUI",
                                          "GUI" if got else "CLI"))


def test_gui_theme_flags_open_gui():
    """GUI 专属主题参数（含 `--seed=0.5` 这种等号写法）仍走 GUI。"""
    m = _load()
    for argv in (["--seed", "0.5"], ["--seed=0.5"], ["--mode", "dark"],
                 ["--vibe", "warm"], ["--mode=light", "--seed=0.2"]):
        assert m._wants_gui(argv) is True, "argv=%r 应走 GUI" % (argv,)


def test_wants_gui_handles_flag_with_value_after_equals():
    """`--seed=0.5` 必须被识别（按 `=` 切分），否则会被当成"要跑 CLI"
    并把 `--seed=0.5` 喂给 argparse ⇒ 报未知参数、GUI 打不开。"""
    m = _load()
    assert m._wants_gui(["--seed=0.5"]) is True
    assert m._wants_gui(["--modes=dark"]) is False   # 前缀相同但不是同一参数
