"""批量分析管线：并行调度 + 结果缓存 + 进度回报。

为什么单独一层：单文件的「怎么算」在 engine.analyze，批量时的
「分给谁算 / 算过没有 / 还剩多久」是另一类问题，混在一起会让两者都难改。

设计要点
--------
* 并行用进程池而非线程池：分析主体是 numpy/scipy 的 C 循环，会释放 GIL，
  但 soundfile 解码与 Python 层的块统计仍受 GIL 制约，进程池才能真吃到多核。
* 缓存键 = 路径 + 大小 + 修改时间：文件被替换或重编码后键自然失效，
  不需要维护版本号。
* 回调在**主进程**触发，因此可以直接驱动 GUI，无需跨进程序列化复杂对象。
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict

from ..config import Thresholds
from .engine import analyze, collect_files

#: 少于这个数量的文件时不开进程池（spawn 启动开销会超过收益）
MIN_PARALLEL = 4


def _job(payload):
    """进程池里的执行单元。必须是模块级函数（要能 pickle）。"""
    path, deep, th_dict, keep_curve = payload
    th = Thresholds.from_dict(th_dict) if th_dict else None
    r = analyze(path, deep=deep, thresholds=th)
    if not keep_curve:
        r.pop("curve", None)
    return r


class ResultCache:
    """单文件结果缓存。文件变更（大小或 mtime）即自动失效。"""

    SCHEMA = 1

    def __init__(self, path: str | None = None):
        self.path = path
        self.data: dict[str, dict] = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    blob = json.load(f)
                if blob.get("schema") == self.SCHEMA:
                    self.data = blob.get("items", {})
            except (OSError, ValueError):
                self.data = {}

    @staticmethod
    def key(path: str) -> str:
        try:
            st = os.stat(path)
            return "%s|%d|%d" % (os.path.abspath(path), st.st_size, int(st.st_mtime))
        except OSError:
            return os.path.abspath(path)

    def get(self, path: str) -> dict | None:
        return self.data.get(self.key(path))

    def put(self, path: str, result: dict) -> None:
        self.data[self.key(path)] = result

    def save(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"schema": self.SCHEMA, "items": self.data},
                          f, ensure_ascii=False)
        except OSError:
            pass


class Progress:
    """进度与预计剩余时间（指数滑动平均，避免 ETA 跳来跳去）。"""

    def __init__(self, total: int):
        self.total = max(1, total)
        self.done = 0
        self.started = time.time()
        self.eta: float | None = None

    def tick(self, n: int = 1) -> tuple[float, float | None]:
        self.done += n
        elapsed = time.time() - self.started
        rate = self.done / elapsed if elapsed > 0.05 else 0.0
        if rate > 0:
            now = (self.total - self.done) / rate
            self.eta = now if self.eta is None else self.eta * 0.7 + now * 0.3
        return self.done / self.total, self.eta

    def elapsed(self) -> float:
        return time.time() - self.started


def analyze_many(paths, *, deep: bool = True, thresholds: Thresholds | None = None,
                 jobs: int = 0, cache: ResultCache | None = None,
                 keep_curve: bool = True, on_result=None, on_progress=None):
    """批量分析。

    返回按输入顺序排列的结果列表。
    `on_result(result, index, total)`、`on_progress(frac, eta_seconds)` 在主进程回调。
    """
    paths = list(paths)
    total = len(paths)
    results: list[dict | None] = [None] * total
    th_dict = asdict(thresholds) if thresholds else None

    todo: list[tuple[int, str]] = []
    for i, p in enumerate(paths):
        hit = cache.get(p) if cache else None
        if hit is not None:
            results[i] = hit
            if on_result:
                on_result(hit, i, total)
        else:
            todo.append((i, p))

    prog = Progress(total)
    if on_progress:
        on_progress(len(paths) - len(todo), None)

    if todo:
        workers = jobs if jobs > 0 else (os.cpu_count() or 1)
        workers = max(1, min(workers, len(todo)))
        # Windows/macOS 默认 spawn：每个 worker 要重新导入包，启动开销约 1s。
        # 文件太少时开进程是负收益，这里退回串行。
        if len(todo) < MIN_PARALLEL:
            workers = 1
        payloads = [(p, deep, th_dict, keep_curve) for _, p in todo]

        if workers == 1:
            for (i, _p), pl in zip(todo, payloads):
                r = _job(pl)
                results[i] = r
                if cache:
                    cache.put(_p, r)
                if on_result:
                    on_result(r, i, total)
                if on_progress:
                    on_progress(*prog.tick())
        else:
            pos = {p: i for i, p in todo}
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_job, pl): pl[0] for pl in payloads}
                for fut in as_completed(futs):
                    src = futs[fut]
                    try:
                        r = fut.result()
                    except Exception as e:                        # noqa: BLE001
                        r = {"file": os.path.basename(src), "path": src, "level": "bad",
                             "verdict": "分析失败：%s" % e, "errors": [str(e)], "failed": True}
                    i = pos[src]
                    results[i] = r
                    if cache:
                        cache.put(src, r)
                    if on_result:
                        on_result(r, i, total)
                    if on_progress:
                        on_progress(*prog.tick())

    return [r for r in results if r is not None]


def scan(targets, **kw):
    """一步到位：展开目录 → 并行分析。"""
    return analyze_many(collect_files(targets), **kw)
