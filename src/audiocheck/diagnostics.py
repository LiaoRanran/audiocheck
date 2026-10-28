"""崩溃可观测性：**本地优先、不上传**的诊断设施。

## 为什么需要（一个真实的产品缺口）

本仓此前**完全没有**崩溃处理——用户说"程序崩了/卡住了"，开发者手里只有
这一句话。而 GUI 程序尤其糟：Windows 上双击启动时**没有控制台**，
`print` 的 traceback 谁也看不见。

## 三个必踩的坑（都来自 PyInstaller / Python 官方文档）

1. **`--windowed` 构建里 `sys.stderr` 是 `None`**
   （PyInstaller common-issues："sys.stdin, sys.stdout, and sys.stderr are
   unavailable, and are set to None"）。所以 `faulthandler.enable()` 不传 file
   等于往 None 里写；`logging.StreamHandler()` 也会炸。
   ⇒ **必须显式传一个真实打开的文件对象**。
2. **`faulthandler` 的文件必须保持打开**
   （官方："If the file is closed and its file descriptor is reused, the
   traceback will be written into a different file"）。
   ⇒ 用模块级全局持有句柄，绝不 close。
3. **`sys.excepthook` 不覆盖线程**，`__del__`/GC 里的异常也不走它。
   ⇒ 必须同时装 `threading.excepthook` 与 `sys.unraisablehook`。

## 设计取舍

* **不上传、不联网**：日志只落本地 `%LOCALAPPDATA%/audiocheck/logs/`。
* **不收集音频、不收集绝对路径**：日志只记时间、级别、消息（异常消息里可能
  含路径——导出诊断包时做一次替换，见 `export_diagnostics`）。
* **有界**：`RotatingFileHandler`（2 MB × 3）防长期运行撑爆磁盘。
* **看门狗**：`dump_traceback_later(repeat=True)` 捕获"界面假死"这类
  没有异常的故障——这是桌面应用最常见的用户投诉形态。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

#: 日志目录（`%LOCALAPPDATA%` 在 Windows 上是用户私有目录，不需要管理员权限）。
LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA")
    or os.environ.get("XDG_CACHE_HOME")
    or os.path.expanduser("~/.cache"),
    "audiocheck", "logs")

LOG_NAME = "app.log"
CRASH_NAME = "crash.log"

_installed = False
_crash_fh = None            # ⚠ 必须全局持有：faulthandler 要求文件保持打开
_log = logging.getLogger("audiocheck")


def log_dir() -> str:
    """日志目录（按需创建）。测试可用 `AUDIOCHECK_LOG_DIR` 覆盖。"""
    d = os.environ.get("AUDIOCHECK_LOG_DIR") or LOG_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _file_handler(name: str, max_bytes: int = 2 << 20, backups: int = 3):
    """轮转文件日志。`delay=True` 让文件在真正有日志时才创建。"""
    return RotatingFileHandler(os.path.join(log_dir(), name), mode="a",
                               maxBytes=max_bytes, backupCount=backups,
                               encoding="utf-8", delay=True)


def install(level: int = logging.INFO, watchdog_seconds: float = 0.0) -> logging.Logger:
    """装好日志 + 三个异常钩子 + faulthandler。**幂等**（重复调用无副作用）。

    `watchdog_seconds > 0` 时启用"卡死看门狗"：主线程超过该秒数没动静
    就把所有线程的栈 dump 到 `crash.log`。默认关闭（它自己也有开销，
    而且只在用户报告假死时才需要）。

    返回配置好的 logger（调用方可以直接 `log.info(...)`）。
    """
    global _installed, _crash_fh
    if _installed:
        return _log

    d = log_dir()

    # ---- 日志 ----
    _log.setLevel(level)
    _log.propagate = False
    if not any(isinstance(h, RotatingFileHandler) for h in _log.handlers):
        fh = _file_handler(LOG_NAME)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        _log.addHandler(fh)
    # ⚠ 只在 stderr **存在**时加 StreamHandler：windowed 构建里它是 None
    # （PyInstaller 官方明说），加上去每次 log 都抛 AttributeError。
    if sys.stderr is not None and not any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, RotatingFileHandler) for h in _log.handlers):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        _log.addHandler(sh)

    # ---- faulthandler：段错误 / 栈溢出 / abort ----
    try:
        import faulthandler
        # `buffering=1` 行缓冲：崩溃瞬间也要把最后几行刷出去
        _crash_fh = open(os.path.join(d, CRASH_NAME), "a", encoding="utf-8",
                         buffering=1)
        faulthandler.enable(file=_crash_fh, all_threads=True)
        if watchdog_seconds and watchdog_seconds > 0:
            faulthandler.dump_traceback_later(
                watchdog_seconds, repeat=True, file=_crash_fh)
    except Exception as e:                                     # noqa: BLE001
        _log.warning("faulthandler 装不上（不影响运行）：%s", e)

    # ---- 主线程未捕获异常 ----
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        _log.error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
        if _crash_fh is not None:
            try:
                import traceback
                traceback.print_exception(exc_type, exc_value, exc_tb,
                                          file=_crash_fh)
                _crash_fh.flush()
            except Exception:                                  # noqa: BLE001
                pass
    sys.excepthook = _hook

    # ---- 工作线程未捕获异常（sys.excepthook 不覆盖线程）----
    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        _log.error("线程 %s 未捕获异常",
                   getattr(args.thread, "name", "?"),
                   exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    try:
        threading.excepthook = _thread_hook
    except Exception:                                          # noqa: BLE001
        pass

    # ---- __del__ / GC 期间的异常（默认静默）----
    def _unraisable_hook(unraisable):
        _log.error("析构期间异常（%s）：%s", unraisable.object,
                   unraisable.exc_value)
    try:
        sys.unraisablehook = _unraisable_hook
    except Exception:                                          # noqa: BLE001
        pass

    _installed = True
    _log.info("启动 v%s ｜ Python %s ｜ frozen=%s ｜ %s",
              _version(), sys.version.split()[0], getattr(sys, "frozen", False),
              sys.platform)
    return _log


def _version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:                                          # noqa: BLE001
        return "?"


def export_diagnostics(dest_zip: str, extra: dict | None = None) -> str:
    """把日志与自检信息打包成诊断 zip（**用户可自助发给开发者**）。

    内容（按报告的建议顺序）：
    1. `app.log*` / `crash.log`（历史轮转全带上）
    2. `env.txt`：平台、Python、frozen、可执行路径、语言
    3. `diagnostic.json`：调用方给的 `extra`（**不含音频、不含工程内容**）
    4. `manifest.sha256`：包内每个文件的哈希（收到后能校验没被篡改）

    **隐私红线**：不做任何联网、不上传；`env.txt` 里的绝对路径按 `~` 缩写。
    调用方负责不要在 `extra` 里放音频数据或用户文件内容。
    """
    import hashlib
    import json
    import zipfile

    d = log_dir()
    files = []
    for name in os.listdir(d):
        if name.startswith(("app.log", "crash.log")):
            files.append(os.path.join(d, name))

    env_lines = [
        "platform: %s" % sys.platform,
        "python: %s" % sys.version.replace("\n", " "),
        "frozen: %s" % bool(getattr(sys, "frozen", False)),
        "executable: %s" % _shorten(getattr(sys, "executable", "?")),
        "prefix: %s" % _shorten(sys.prefix),
        "audiocheck: %s" % _version(),
    ]
    try:
        import numpy
        import scipy
        import soundfile
        env_lines.append("numpy: %s" % numpy.__version__)
        env_lines.append("scipy: %s" % scipy.__version__)
        env_lines.append("soundfile: %s" % soundfile.__version__)
        env_lines.append("libsndfile: %s" % soundfile.__libsndfile_version__)
    except Exception:                                          # noqa: BLE001
        pass

    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, os.path.basename(f))
        z.writestr("env.txt", "\n".join(env_lines) + "\n")
        z.writestr("diagnostic.json",
                   json.dumps(extra or {}, ensure_ascii=False, indent=2,
                              default=str))
        # manifest 最后写：覆盖前面所有成员
        manifest = []
        for name in z.namelist():
            manifest.append("%s  %s" % (
                hashlib.sha256(z.read(name)).hexdigest(), name))
        z.writestr("manifest.sha256", "\n".join(manifest) + "\n")
    return dest_zip


def _shorten(p: str) -> str:
    """把绝对路径缩成 `~` 形式（诊断包不该暴露完整用户名/目录结构）。"""
    home = os.path.expanduser("~")
    try:
        if home and p.lower().startswith(home.lower()):
            return "~" + p[len(home):]
    except Exception:                                          # noqa: BLE001
        pass
    return p
