"""批量管线：并行、缓存失效、进度回调。"""
from __future__ import annotations

import json
import os

from audiocheck.config import Thresholds
from audiocheck.core.pipeline import Progress, ResultCache, analyze_many, scan


def test_cache_hit_and_invalidation(audio, tmp_path):
    p = audio["cd_full"]
    c = ResultCache(str(tmp_path / "c.json"))
    c.put(p, {"file": os.path.basename(p), "level": "ok"})
    assert c.get(p)["level"] == "ok"

    # 改 mtime → 键应失效（文件被替换/重编码后不能拿旧结果）
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 10))
    assert c.get(p) is None


def test_cache_roundtrip(tmp_path):
    f = str(tmp_path / "c.json")
    c = ResultCache(f)
    c.put("/some/path.flac", {"x": 1})
    c.save()
    c2 = ResultCache(f)
    assert c2.get("/some/path.flac") == {"x": 1}


def test_cache_corrupt_file_is_ignored(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    c = ResultCache(str(f))
    assert c.data == {}


def test_analyze_many_sequential(audio):
    paths = [audio["cd_full"], audio["lossy_16k"]]
    rows = analyze_many(paths, jobs=1, keep_curve=False)
    assert len(rows) == 2
    assert {r["file"] for r in rows} == {os.path.basename(p) for p in paths}
    for r in rows:
        assert r["verdict"]


def test_analyze_many_preserves_input_order(audio):
    paths = [audio["cd_full"], audio["lossy_16k"], audio["up_44"]]
    rows = analyze_many(paths, jobs=1, keep_curve=False)
    assert [r["path"] for r in rows] == [os.path.abspath(p) for p in paths]


def test_small_batch_forces_sequential(audio, monkeypatch):
    """文件太少时开进程是负收益（spawn 启动开销）——应自动退回串行。"""
    calls = []
    monkeypatch.setattr("audiocheck.core.pipeline.MIN_PARALLEL", 99)
    paths = [audio["cd_full"]]
    analyze_many(paths, jobs=8, keep_curve=False,
                 on_progress=lambda *a: calls.append(a))
    assert len(calls) >= 1


def test_progress_eta():
    p = Progress(10)
    f1, _ = p.tick(5)
    assert abs(f1 - 0.5) < 1e-6
    f2, eta = p.tick(5)
    assert abs(f2 - 1.0) < 1e-6


def test_scan_expands_directory(audio):
    d = os.path.dirname(audio["cd_full"])
    rows = scan([d], jobs=1, keep_curve=False)
    assert len(rows) >= 1
    assert all("verdict" in r for r in rows)


def test_thresholds_pickle_roundtrip(audio):
    """并行时阈值要能跨进程传递。"""
    th = Thresholds(cd_full=18.0)
    rows = analyze_many([audio["band_19k"]], jobs=1, thresholds=th, keep_curve=False)
    assert rows[0]["level"] == "ok"


def test_result_is_json_safe(audio):
    rows = analyze_many([audio["cd_full"]], jobs=1, keep_curve=False)
    json.dumps(rows)
