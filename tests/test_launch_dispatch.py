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


# ============================ 编辑 CLI 的分派（v1.1.5 补：此前完全不可达）
def test_edit_flags_route_to_edit_cli():
    """★ 编辑专属参数必须走 `edit_cli`（此前全部落到体检 CLI ⇒ unrecognized arguments）。

    2026-10-29 实测：打包 exe 跑
    `audiocheck.exe 源.wav 目标.wav --gain -3 --at 0-0.5` 报
    `unrecognized arguments: --gain -3 --at 0-0.5` —— 修复能力对压缩包用户
    **完全不可达**，而那正是本项目的核心差异化。
    """
    m = _load()
    cases = [
        ["--gain", "-3", "--at", "0-0.5"],
        ["--declip", "--at", "0-1", "--ceiling", "0.5"],
        ["--declick", "--at", "0-1"],
        ["--dehum", "--hum-f0", "50", "--at", "0-1"],
        ["--denoise", "--noise-at", "0-0.3", "--at", "0.5-1"],
        ["--batch", "jobs.json"],
        ["--verify-audit", "a.json"],
        ["--chain", "c.json"],
        ["--poly", "0,100;1,100;1,400"],
        ["--gain", "-3", "--band", "1000-4000", "--at", "0-1"],
    ]
    bad = [c for c in cases if not m._wants_edit(c)]
    assert not bad, "以下参数应走编辑 CLI：%s" % bad


def test_edit_flag_equals_form_is_detected():
    """`--gain=-3` 这种等号写法也必须被识别（按 `=` 切分）。"""
    m = _load()
    assert m._wants_edit(["--gain=-3", "--at=0-1"]) is True
    assert m._wants_edit(["--verify-audit=a.json"]) is True


def test_assessment_flags_do_not_route_to_edit():
    """★ 体检专属参数**不该**走编辑 CLI（防止误分派）。"""
    m = _load()
    for argv in (["--json", "o.json", "a.wav"], ["--csv", "o.csv"],
                 ["--plot", "d/"], ["--shallow"], ["--list-analyzers"],
                 ["--only", "spectrum"], ["--config", "c.json"],
                 ["--cache", "c.json"], ["a.wav"], ["D:/music"]):
        assert m._wants_edit(argv) is False, "%r 不该走编辑 CLI" % (argv,)


def test_gui_flags_still_win_over_edit():
    """`--seed` 等 GUI 参数优先（即使同时出现编辑参数）。"""
    m = _load()
    assert m._wants_gui(["--seed", "0.5", "--gain", "-3"]) is True
    assert m._wants_gui(["--mode=dark", "--at", "0-1"]) is True


def test_no_args_still_opens_gui():
    """无参数仍开窗口（不能因为加了编辑分派就改掉双击行为）。"""
    m = _load()
    assert m._wants_gui([]) is True
    assert m._wants_edit([]) is False


def test_version_and_doctor_are_shared_not_edit():
    """★ `--version` / `--doctor` **两边都有** ⇒ 不能当编辑判据。

    它们必须落到体检 CLI（先注册的那套），否则编辑 CLI 的 `--version`
    会覆盖体检的——两者输出格式不同，脚本会读到不一样的东西。
    """
    m = _load()
    assert m._wants_edit(["--version"]) is False
    assert m._wants_edit(["--doctor"]) is False
    assert m._wants_gui(["--version"]) is False
