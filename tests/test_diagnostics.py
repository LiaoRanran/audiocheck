"""崩溃可观测性的判据（本仓此前**完全没有**这套设施）。

## 为什么这对桌面应用是必需品

用户说"程序崩了/卡住了"，开发者手里只有这一句话——GUI 程序在 Windows 上
双击启动时**没有控制台**，`print` 的 traceback 谁也看不见。

## 三个坑（都来自官方文档，且都在本仓踩得到）

1. **`--windowed` 下 `sys.stderr is None`**（PyInstaller common-issues 原文）
   ⇒ 无脑加 `StreamHandler(sys.stderr)` 会让每次 log 都抛 `AttributeError`。
2. **`faulthandler` 的文件必须保持打开**
   ⇒ 用局部变量存句柄，函数返回后 GC 掉，崩溃时写不进去。
3. **`sys.excepthook` 不覆盖线程**，`__del__`/GC 的异常也不走它
   ⇒ 只装主线程钩子会漏掉一半的崩溃。
"""
from __future__ import annotations

import logging
import os
import sys
import threading

import pytest

sys.path.insert(0, "src")

from audiocheck import diagnostics as D  # noqa: E402


@pytest.fixture()
def fresh_logs(tmp_path, monkeypatch):
    """每个测试一个独立日志目录 + 重置 install 状态（install 是幂等的）。"""
    monkeypatch.setenv("AUDIOCHECK_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(D, "_installed", False)
    # 清掉已挂的 handler，避免跨测试串台
    log = logging.getLogger("audiocheck")
    for h in list(log.handlers):
        log.removeHandler(h)
    yield tmp_path


def test_install_creates_log_files(fresh_logs):
    """装好之后必须真的产生 `app.log`（并且启动横幅能被写进去）。"""
    D.install()
    D._log.info("探针消息")
    p = os.path.join(str(fresh_logs), D.LOG_NAME)
    assert os.path.exists(p), "app.log 应被创建"
    text = open(p, encoding="utf-8").read()
    assert "探针消息" in text
    assert "启动 v" in text, "应有启动横幅（含版本与 frozen 状态）"


def test_install_is_idempotent(fresh_logs):
    """重复调用不应重复挂 handler（否则同一条日志写 N 遍）。"""
    D.install()
    n1 = len(D._log.handlers)
    D.install()
    n2 = len(D._log.handlers)
    assert n1 == n2, "install 必须幂等（handler 数 %d → %d）" % (n1, n2)


def test_install_tolerates_missing_stderr(fresh_logs, monkeypatch):
    """★ `sys.stderr is None`（windowed 构建）时 install 不能抛异常。

    这是 PyInstaller `--windowed` 的真实状态：stdin/stdout/stderr 全是 None。
    若实现里无条件 `logging.StreamHandler(sys.stderr)`，这里会红。
    """
    monkeypatch.setattr(sys, "stderr", None)
    D.install()                        # 不应抛
    log = logging.getLogger("audiocheck")
    assert any(isinstance(h, logging.handlers.RotatingFileHandler)
               for h in log.handlers), "文件 handler 必须仍然在"


def test_main_thread_hook_records_exception(fresh_logs):
    """主线程未捕获异常必须落盘（含异常消息）。"""
    D.install()
    try:
        raise ValueError("主线程探针异常")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    text = open(os.path.join(str(fresh_logs), D.LOG_NAME), encoding="utf-8").read()
    assert "主线程探针异常" in text
    assert "ValueError" in text


def test_thread_hook_records_exception(fresh_logs):
    """★ 工作线程的异常也必须落盘（`sys.excepthook` 不覆盖线程）。"""
    D.install()

    def boom():
        raise RuntimeError("线程探针异常")
    t = threading.Thread(target=boom, name="probe-worker")
    t.start()
    t.join()
    text = open(os.path.join(str(fresh_logs), D.LOG_NAME), encoding="utf-8").read()
    assert "线程探针异常" in text, "线程异常必须被 threading.excepthook 捕获"
    assert "probe-worker" in text, "应记下线程名（区分是谁崩的）"


def test_crash_log_file_is_created_and_kept_open(fresh_logs):
    """`crash.log` 必须被创建且**句柄保持打开**（faulthandler 的硬要求）。

    官方原话：文件被关闭且 fd 被复用时，traceback 会写进**另一个**文件。
    """
    D.install()
    assert D._crash_fh is not None, "crash.log 句柄必须被全局持有"
    assert not D._crash_fh.closed, "句柄不能是关闭状态"
    assert os.path.exists(os.path.join(str(fresh_logs), D.CRASH_NAME))


def test_export_diagnostics_has_manifest_and_env(fresh_logs, tmp_path):
    """诊断包结构：日志 + env.txt + diagnostic.json + manifest.sha256。"""
    import zipfile
    D.install()
    D._log.info("导出探针")
    out = str(tmp_path / "diag.zip")
    D.export_diagnostics(out, extra={"ops": 7, "note": "探针"})
    assert os.path.exists(out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "env.txt" in names
        assert "diagnostic.json" in names
        assert "manifest.sha256" in names
        env = z.read("env.txt").decode()
        assert "platform:" in env and "audiocheck:" in env
        # manifest 必须覆盖除自己以外的全部成员
        manifest = z.read("manifest.sha256").decode()
        for n in names:
            if n != "manifest.sha256":
                assert n in manifest, "manifest 漏了 %s" % n


def test_export_shortens_home_paths(fresh_logs, tmp_path):
    """诊断包里的绝对路径必须被缩写（不暴露完整用户名/目录结构）。"""
    import zipfile
    D.install()
    out = str(tmp_path / "diag2.zip")
    D.export_diagnostics(out)
    with zipfile.ZipFile(out) as z:
        env = z.read("env.txt").decode()
    home = os.path.expanduser("~")
    # 若 executable 在 home 下，必须已缩成 ~ 开头
    if getattr(sys, "executable", "").lower().startswith(home.lower()):
        assert home.lower() not in env.lower(), "home 路径未被缩写"


def test_export_does_not_require_extra(fresh_logs, tmp_path):
    """`extra` 缺省时也要能导出（UI 里可能只想给个空包）。"""
    D.install()
    out = str(tmp_path / "diag3.zip")
    D.export_diagnostics(out)
    assert os.path.getsize(out) > 0
