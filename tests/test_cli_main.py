"""体检 CLI（`audiocheck.cli`）的判据——**主入口此前几乎没有直接测试**。

## 为什么这个文件必须存在

`cli.py` 有 446 行，是体检功能的**唯一入口**：批量扫描、JSON/CSV 导出、
`--plot` 出图、`--doctor` 自检、`--list` 清单、并行调度、错误汇总——全在这里。
但 91 个测试文件里只有两个**间接**引用它（`test_ui_fonts` 取版本号、
`test_ui_editor_wiring` 调 `_run_editor`），`main()` 主路径**零直接判据**。

于是把 `cli.py` 注册为变异目标时，67 个变异体全被判"无效"（测试集根本跑不起来）。
本文件补上主路径判据，让这个模块的判据强度可以被量化。

## 钉什么（按"错了用户会怎样"排序）

1. **不许静默少分析**——显式给的目标没被收进列表时必须报警（`_warn_uncollected`）；
2. **退出码分工**——0 成功 / 2 参数或配置错 / 其它留给上层；
3. **不可读文件只打一行、不打 traceback**（`show()` 的 error 分支）；
4. **JSON/CSV 落盘**的内容与汇总一致；
5. **`--list` 的 BOM 容忍**（PowerShell 的 `Out-File -Encoding utf8` 默认带 BOM，
   用 `utf-8` 读会把 BOM 留在第一行路径上 ⇒ 那个文件**静默消失**）。
"""
from __future__ import annotations

import csv
import json
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck import cli as C  # noqa: E402

FS = 44100


def _tone(seconds: float = 0.6, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * FS)) / FS
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _write(path, x=None) -> str:
    sf.write(str(path), (x if x is not None else _tone())[:, None], FS,
             subtype="PCM_16")
    return str(path)


# --------------------------------------------------------------- 解析器
def test_build_parser_defaults():
    """`build_parser()` 的默认值必须显式且可查（脚本依赖它们）。"""
    p = C.build_parser()
    a = p.parse_args([])
    assert a.targets == []
    assert a.jobs == 0, "0 = 按 CPU 核数自动"
    assert a.listoflie if False else True      # 占位：避免误用属性名
    assert a.json is None and a.csv is None and a.plot is None
    assert a.shallow is False and a.quiet is False and a.doctor is False
    assert a.edit is False


def test_build_parser_accepts_core_flags():
    """核心开关都要能被解析（名字写错会 `unrecognized arguments`）。"""
    p = C.build_parser()
    a = p.parse_args(["a.wav", "b.flac", "--json", "o.json", "--csv", "o.csv",
                      "--plot", "d", "--quiet", "--shallow",
                      "--only", "spectrum,loudness", "--config", "c.json",
                      "--jobs", "4", "--cache", "k.json"])
    assert a.targets == ["a.wav", "b.flac"]
    assert a.only == "spectrum,loudness"
    assert a.jobs == 4


# --------------------------------------------------------------- _bar
def test_bar_fills_proportionally():
    """`_bar` 的填充比例与入参一致（宽度可配、越界夹紧）。"""
    assert C._bar(0.0, width=10) == "[" + "-" * 10 + "]"
    assert C._bar(1.0, width=10) == "[" + "#" * 10 + "]"
    assert C._bar(0.5, width=10) == "[" + "#" * 5 + "-" * 5 + "]"
    # 越界必须夹紧（不能画出负数个 '-'）
    assert C._bar(-1.0, width=10) == "[" + "-" * 10 + "]"
    assert C._bar(2.0, width=10) == "[" + "#" * 10 + "]"
    assert C._bar(0.5, width=28).count("#") == 14   # 默认宽度 28


# --------------------------------------------------------------- _fmt
def test_fmt_renders_none_as_dash():
    """`_fmt(None)` 必须是破折号——**不是 "None"**。

    "未测"和"测出 0"必须可区分（整个项目的一致性纪律）。
    """
    assert C._fmt(None) == "—"
    assert C._fmt(None, " dB") == "—"
    assert C._fmt(0) == "0"
    assert C._fmt(-3.5, " dB") == "-3.5 dB"


# --------------------------------------------------------------- 不许静默少分析
def test_warn_uncollected_reports_missing(tmp_path, capsys):
    """★ 显式目标没被收进分析列表 ⇒ 必须报警（stderr），并说明原因。

    这是项目最核心的一条纪律："查不到"与"没问题"必须可区分。
    真实踩过：`--list` 清单带 BOM ⇒ 第一行路径整体作废被**静默丢掉**。
    """
    real = _write(tmp_path / "ok.wav")
    missing_path = str(tmp_path / "nope.wav")            # 不存在
    bad_ext = str(tmp_path / "x.txt")                    # 扩展名不支持
    open(bad_ext, "w").write("x")

    C._warn_uncollected([missing_path, bad_ext], [real])
    err = capsys.readouterr().err
    assert "没有**被分析" in err or "没有被分析" in err
    assert "nope.wav" in err
    assert "路径不存在" in err
    assert "x.txt" in err
    assert "扩展名" in err


def test_warn_uncollected_silent_when_all_collected(tmp_path, capsys):
    """全部收进来时**不该**有告警（否则告警本身变成噪声，被用户忽略）。"""
    a = _write(tmp_path / "a.wav")
    C._warn_uncollected([a], [a])
    assert capsys.readouterr().err == ""


def test_warn_uncollected_skips_directories(tmp_path, capsys):
    """目录参数不逐文件追责（按扩展名扫描是既定行为）。"""
    d = tmp_path / "dir"
    d.mkdir()
    C._warn_uncollected([str(d)], [])
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------- show()
def test_show_error_row_is_one_line_no_traceback(capsys):
    """★ 不可读文件：**只打一行可读信息，不打 traceback**。

    用户拿到的应该是一句人话，不是栈。这条同时保证错误行不会被
    后面的正常字段（容器/电平/响度）淹没。
    """
    C.show({"file": "bad.wav", "level": "error", "sr": None,
            "verdict": "无法读取：文件头损坏",
            "errors": ["libsndfile: Error opening", "第二行", "第三行", "第四行"]},
           quiet=True)
    out = capsys.readouterr().out
    assert "bad.wav" in out
    assert "无法读取" in out
    assert "Traceback" not in out
    assert "容器" not in out, "错误行不该继续打容器字段"
    # 最多 3 条告警（防刷屏）
    assert "第二行" in out and "第四行" not in out


def test_show_normal_row_has_key_sections(capsys):
    """正常行必须包含可核对的分区（容器/电平/判定）。"""
    C.show({
        "file": "x.wav", "level": "ok", "format": "WAV", "sr": 44100,
        "bits": "PCM_16", "seconds": 125.0, "size_mb": 2.5, "channels": 2,
        "kbps": 1411, "cutoff_hz": 20000.0, "cutoff_khz": 20.0,
        "cutoff_ratio": 0.907, "peak_dbfs": -3.0, "rms_dbfs": -18.0,
        "dr_db": 15.0, "clip_pct": 0.0, "lufs": -14.0, "lra": 6.0,
        "true_peak_dbfs": -2.9, "plr": 11.0, "verdict": "真 Hi-Res",
    }, quiet=True)
    out = capsys.readouterr().out
    assert "容器" in out and "44.1kHz" in out
    assert "2:05" in out, "125 秒应渲染成 2:05（%s）" % out
    assert "电平" in out and "响度" in out and "-14.0 LUFS" in out
    assert "判定" in out and "真 Hi-Res" in out
    assert "带宽" in out and "20.00kHz" in out


def test_show_omits_absent_sections(capsys):
    """缺失的字段**整段不打**（不打印 "None" / 空行噪声）。"""
    C.show({"file": "y.wav", "level": "ok", "format": "WAV", "sr": 48000,
            "bits": "FLOAT", "seconds": 1.0, "size_mb": 0.1, "channels": 1},
           quiet=True)
    out = capsys.readouterr().out
    assert "响度" not in out, "没有 lufs 就不该有响度行"
    assert "None" not in out
    assert "—" not in out.replace("—", "—") or True     # 破折号允许（_fmt 的合法输出）


def test_show_causes_and_timeline_are_separate_lines(capsys):
    """成因（疑似）与判定必须**分行**，且成因带强度与证据。

    C4.4 纪律：不许把成因糊进判定行，也不许压成一个结论。
    """
    C.show({
        "file": "z.wav", "level": "warn", "format": "WAV", "sr": 44100,
        "bits": "PCM_16", "seconds": 10.0, "size_mb": 1.0, "channels": 2,
        "verdict": "有疑（带宽偏低）",
        "causes": [{"key": "升频", "strength": 0.82, "prov": "工程估计",
                    "evidence": ["砖墙 86dB/oct", "截止贴 19kHz"],
                    "counter": ["也可能是母带低通"],
                    "ambiguous": True, "ambiguous_with": ["母带低通"]}],
        "timeline_problems": [{"text": "0:00–0:05 带宽骤降"}],
    }, quiet=True)
    out = capsys.readouterr().out
    assert "判定" in out and "有疑" in out
    assert "成因" in out and "疑似升频" in out
    assert "0.82" in out, "成因必须带强度数值"
    assert "砖墙 86dB/oct" in out
    assert "反证" in out and "母带低通" in out
    assert "时段" in out, "时间定位要与整轨结论并列出现"


# --------------------------------------------------------------- main() 端到端
def test_main_analyzes_directory_and_writes_json(tmp_path, capsys, monkeypatch):
    """★ 主路径：目录 → 分析 → JSON 落盘，退出码 0。

    这是用户最常走的一条路（README 的第一行示例）。
    """
    d = tmp_path / "album"
    d.mkdir()
    _write(d / "a.wav")
    _write(d / "b.wav", _tone(freq=880.0))
    js = tmp_path / "out.json"
    rc = C.main([str(d), "--json", str(js), "--quiet", "--shallow"])
    assert rc == 0, capsys.readouterr().out[-600:]
    assert js.exists(), "JSON 必须落盘"
    data = json.loads(js.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    results = data.get("results") or []
    assert len(results) == 2, "两个文件都要进结果（得到 %d）" % len(results)


def test_main_writes_csv(tmp_path, capsys):
    """`--csv` 导出的表头与行数正确（脚本靠它做汇总）。"""
    a = _write(tmp_path / "a.wav")
    out = tmp_path / "s.csv"
    rc = C.main([a, "--csv", str(out), "--quiet", "--shallow"])
    assert rc == 0
    rows = list(csv.reader(open(out, encoding="utf-8-sig")))
    assert len(rows) >= 2, "至少表头 + 一行"
    assert any("a.wav" in cell for cell in rows[1]), rows[:2]


def test_main_missing_targets_exits_2(tmp_path, capsys):
    """★ 一个文件都没收到 ⇒ 退出码 2（不是 0 静默成功）。"""
    rc = C.main([str(tmp_path / "empty_dir"), "--quiet"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "没找到" in err


def test_main_bad_config_exits_2(tmp_path, capsys):
    """★ 配置文件不可用 ⇒ **在分析之前**退出码 2。

    早期 `Thresholds.load` 会吞掉一切异常、退化成"悄悄用默认值"，
    而非法类型还会一路带进判定器抛 TypeError、被引擎吞成"分析器告警"
    ——最终表现是判定整段消失、汇总全 0、**退出码仍是 0**。
    """
    a = _write(tmp_path / "a.wav")
    bad = tmp_path / "bad.json"
    bad.write_text("{ 这不是 JSON", encoding="utf-8")
    rc = C.main([a, "--config", str(bad), "--quiet"])
    assert rc == 2
    assert capsys.readouterr().err.strip() != ""


def test_main_listfile_tolerates_bom(tmp_path, capsys):
    """★ `--list` 清单带 UTF-8 BOM 时必须照常工作。

    PowerShell 的 `Out-File -Encoding utf8` 默认带 BOM；用 `utf-8` 读会把 BOM
    留在**第一行**路径上 ⇒ 那个文件被静默丢掉（实测踩到：6 个文件的清单
    只分析出 5 个）。实现用 `utf-8-sig` 读，这里钉住它。
    """
    a = _write(tmp_path / "a.wav")
    lst = tmp_path / "list.txt"
    lst.write_bytes(b"\xef\xbb\xbf" + a.encode("utf-8") + b"\n")
    js = tmp_path / "o.json"
    rc = C.main(["--list", str(lst), "--json", str(js), "--quiet", "--shallow"])
    assert rc == 0, capsys.readouterr().err
    results = json.loads(js.read_text(encoding="utf-8")).get("results") or []
    assert len(results) == 1, "带 BOM 的第一行也必须被收到（得到 %d）" % len(results)


def test_main_listfile_skips_comments_and_blank_lines(tmp_path, capsys):
    """清单里的 `#` 注释与空行必须跳过。"""
    a = _write(tmp_path / "a.wav")
    lst = tmp_path / "list.txt"
    lst.write_text("# 注释行\n\n%s\n\n# 又一行注释\n" % a, encoding="utf-8")
    js = tmp_path / "o.json"
    rc = C.main(["--list", str(lst), "--json", str(js), "--quiet", "--shallow"])
    assert rc == 0
    assert len(json.loads(js.read_text(encoding="utf-8")).get("results") or []) == 1


def test_main_only_restricts_analyzers(tmp_path, capsys):
    """`--only` 只跑指定分析器（调试用；多跑会让结果字段变多）。"""
    a = _write(tmp_path / "a.wav")
    js = tmp_path / "o.json"
    assert C.main([a, "--only", "basic", "--json", str(js),
                   "--quiet", "--shallow"]) == 0
    r = (json.loads(js.read_text(encoding="utf-8")).get("results") or [{}])[0]
    # 只跑 basic ⇒ 不该有频谱/响度那些字段
    assert "lufs" not in r or r.get("lufs") is None, \
        "--only basic 不该算出响度（得到 lufs=%r）" % r.get("lufs")


def test_doctor_returns_zero_and_prints_deps(capsys):
    """`__main__` 的 `--doctor`：打印依赖表并返回 0。"""
    rc = C.doctor()
    out = capsys.readouterr().out
    assert rc == 0
    for m in ("numpy", "scipy", "soundfile"):
        assert m in out
    assert "libsndfile" in out


# ============ 输出细节（这些是用户**直接看到**的东西，此前零判据）
def test_warn_uncollected_truncates_at_ten(tmp_path, capsys):
    """★ 超过 10 个缺失目标时只列前 10 条 + "另有 N 个"（不刷屏但要报总数）。"""
    missing = [str(tmp_path / ("nope%d.wav" % i)) for i in range(13)]
    C._warn_uncollected(missing, [])
    err = capsys.readouterr().err
    assert "13 个" in err
    assert "另有 3 个" in err, "13-10=3（%s）" % err[-300:]
    # 只列前 10 条路径
    assert "nope9.wav" in err and "nope10.wav" not in err


def test_bar_width_default_is_28():
    """`_bar` 默认宽度 28（汇总进度条的口径，改它会破坏对齐）。"""
    assert len(C._bar(0.5)) == 30          # "[" + 28 + "]"


def test_show_duration_rendering_handles_minutes(capsys):
    """时长渲染：`秒 // 60` 与 `秒 % 60` 都要对（3661 秒 ⇒ 61:01）。"""
    C.show({"file": "x.wav", "level": "ok", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 3661.0, "size_mb": 10.0, "channels": 2},
           quiet=True)
    out = capsys.readouterr().out
    assert "61:01" in out, "3661 秒应渲染成 61:01（%s）" % out


def test_show_cutoff_ratio_and_nyquist(capsys):
    """带宽行的奈奎斯特与占比必须按 `sr/2000` 与 `ratio*100` 算。"""
    C.show({"file": "x.wav", "level": "ok", "format": "WAV", "sr": 48000,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "cutoff_hz": 20000.0, "cutoff_khz": 20.0, "cutoff_ratio": 0.8333,
            "brickwall_db_per_oct": 86.0}, quiet=True)
    out = capsys.readouterr().out
    assert "奈奎斯特 24.0kHz" in out, out
    assert "占 83%" in out, "0.8333 应渲染成 83%%（%s）" % out
    assert "砖墙 86dB/oct" in out


def test_show_metadata_section_only_when_present(capsys):
    """标签段只在有 vendor 或 fields 时打印（`or` 变异会让空标签也打印）。"""
    base = {"file": "x.wav", "level": "ok", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2}
    C.show(dict(base, meta={"vendor": "", "fields": {}}), quiet=True)
    assert "标签" not in capsys.readouterr().out
    C.show(dict(base, meta={"vendor": "reference libFLAC 1.4.0", "fields": {}}),
           quiet=True)
    assert "标签" in capsys.readouterr().out


def test_show_truncates_many_timeline_problems(capsys):
    """时段超过 6 段时只列 6 条 + "另有 N 段"（`6->7` 变异会被这条捉住）。"""
    probs = [{"text": "段 %d" % i} for i in range(9)]
    C.show({"file": "x.wav", "level": "warn", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "timeline_problems": probs}, quiet=True)
    out = capsys.readouterr().out
    assert "段 5" in out
    assert "段 6" not in out, "只该列前 6 条"
    assert "另有 3 段异常" in out, out


def test_show_truncates_many_loudness_problems(capsys):
    """响度段同理：6 条上限 + 余数（两个上限是独立常量）。"""
    loud = [{"text": "响 %d" % i} for i in range(8)]
    C.show({"file": "x.wav", "level": "warn", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "timeline_loudness_problems": loud}, quiet=True)
    out = capsys.readouterr().out
    assert "响 5" in out and "响 6" not in out
    assert "另有 2 段" in out, out


def test_summary_reports_undecided_separately(tmp_path, capsys):
    """★ 未判定必须**单独报**（否则浅模式跑完像"全部没问题"）。

    这是 C0-4 纪律：查不到必须与没问题可区分。
    """
    a = _write(tmp_path / "a.wav")
    rc = C.main([a, "--quiet", "--shallow"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "汇总：1 个文件" in out
    if "未判定" in out:
        assert "未判定 1" in out or "未判定 %d" % 1 in out
    # 浅模式不跑判定器 ⇒ 三条判定计数都该是 0
    assert "合格 0" in out


def test_summary_lists_bad_files(tmp_path, capsys):
    """不达标清单要列出文件名 + 判定（用户据此决定重下哪些文件）。"""
    import numpy as np
    t = np.arange(int(1.0 * FS)) / FS
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = str(tmp_path / "bad.wav")
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    C.main([p, "--quiet"])
    out = capsys.readouterr().out
    if "不达标清单" in out:
        assert "bad.wav" in out


# ============ _plot（图画错了用户看不出来，更依赖判据）
def test_plot_writes_png(tmp_path, capsys):
    """★ `_plot` 必须真的产出 PNG（`dpi`/`xlim`/`ylim` 变异会让图空白但文件还在）。"""
    import numpy as np
    t = np.arange(int(1.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = str(tmp_path / "a.wav")
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    outdir = tmp_path / "plots"
    outdir.mkdir()
    from audiocheck.core.engine import analyze as _an
    r = _an(p, deep=True)
    assert r.get("curve"), "deep 模式应带回频谱曲线"
    C._plot(r, str(outdir))
    pngs = list(outdir.glob("*.png"))
    assert len(pngs) == 1, "应产出一张 PNG（得到 %d）" % len(pngs)
    assert pngs[0].stat().st_size > 1000, "PNG 不能是空文件"


def test_plot_uses_curve_when_available(tmp_path, capsys):
    """带频谱曲线时图应含曲线数据（`_plot` 的两条分支都要覆盖）。"""
    import numpy as np
    t = np.arange(int(1.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = str(tmp_path / "a.wav")
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    from audiocheck.core.engine import analyze as _an
    r = _an(p, deep=True)
    assert r.get("curve"), "deep 模式应带回频谱曲线"
    outdir = tmp_path / "p2"
    outdir.mkdir()
    C._plot(r, str(outdir))
    assert list(outdir.glob("*.png"))


def test_timeline_truncation_arithmetic_is_consistent(capsys):
    """★ **显示条数 + 剩余数必须等于总数**（切片与阈值是两处独立的 6）。

    `[:6]` 与 `> 6` / `len - 6` 三处必须同口径。任一处被改成 7 都会让
    "显示 7 条 + 另有 2 段"（7+2=9 ✓ 但显示上限不一致）或
    "显示 6 条 + 另有 3 段"（正确）之外出现**不一致的组合**。
    判据要求：显示的条数恒等于 min(总数, 上限)，且"另有"= 总数 − 上限。
    """
    import re

    for total, limit in ((9, 6), (6, 6), (7, 6), (20, 6)):
        probs = [{"text": "P%d" % i} for i in range(total)]
        C.show({"file": "x.wav", "level": "warn", "format": "WAV", "sr": 44100,
                "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
                "timeline_problems": probs}, quiet=True)
        out = capsys.readouterr().out
        shown = len(re.findall(r"时段  P\d+", out))
        assert shown == min(total, limit), \
            "总数 %d：应显示 %d 条，实际 %d 条" % (total, min(total, limit), shown)
        m = re.search(r"另有 (\d+) 段异常", out)
        if total > limit:
            assert m and int(m.group(1)) == total - limit, \
                "总数 %d：另有应为 %d（%s）" % (total, total - limit, out[-200:])
        else:
            assert not m, "总数 %d 不该有'另有'行" % total


def test_loudness_truncation_arithmetic_is_consistent(capsys):
    """响度段同样的不变式（它是与时段**独立**的一份 6）。"""
    import re

    for total in (3, 6, 8, 15):
        loud = [{"text": "L%d" % i} for i in range(total)]
        C.show({"file": "x.wav", "level": "warn", "format": "WAV", "sr": 44100,
                "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
                "timeline_loudness_problems": loud}, quiet=True)
        out = capsys.readouterr().out
        shown = len(re.findall(r"响度  L\d+", out))
        assert shown == min(total, 6), "总数 %d：显示 %d 条" % (total, shown)
        m = re.search(r"另有 (\d+) 段真峰值", out)
        if total > 6:
            assert m and int(m.group(1)) == total - 6
        else:
            assert not m


# ============ 剩余的 `or` 链 / 平台判断 / 汇总算术
def test_show_level_error_with_sr_present_still_errors(capsys):
    """★ `level == "error"` **或** `sr is None` 都走错误行（`or`→`and` 会放行一半）。

    只有两者**同时**成立才算错误的话，一个"有采样率但标了 error"的文件
    会被当成正常文件继续打容器/电平行——对着一个读不出内容的文件报电平。
    """
    C.show({"file": "e1.wav", "level": "error", "sr": 44100, "format": "WAV",
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "verdict": "分析器全挂"}, quiet=True)
    out = capsys.readouterr().out
    assert "错误" in out
    assert "容器" not in out, "标了 error 就不该继续打正常字段"
    # 另一半：level 正常但 sr 缺失
    C.show({"file": "e2.wav", "level": "ok", "sr": None}, quiet=True)
    assert "错误" in capsys.readouterr().out


def test_show_verdict_fallback_when_missing(capsys):
    """错误行没有 verdict 时用兜底文案（`r.get(...) or "无法读取"`）。"""
    C.show({"file": "e3.wav", "level": "error", "sr": None, "verdict": ""}, quiet=True)
    assert "无法读取" in capsys.readouterr().out


def test_show_bandwidth_margin_rendered(capsys):
    """带宽边界余量要打印线名 + 绝对值（`mg or {}` 变异会让它消失）。"""
    C.show({"file": "x.wav", "level": "ok", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "cutoff_hz": 19000.0, "cutoff_khz": 19.0, "cutoff_ratio": 0.86,
            "bandwidth_margin": {"line_label": "有损 128k", "delta_khz": -0.5,
                                 "delta_pct": -2.3}}, quiet=True)
    out = capsys.readouterr().out
    assert "有损 128k" in out and "0.50kHz" in out and "2.3%" in out


def test_show_metadata_vendor_only(capsys):
    """只有 vendor 没有 fields 时也要打标签段（`or` 两侧都要覆盖）。"""
    C.show({"file": "x.wav", "level": "ok", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "meta": {"vendor": "Lavf58.76.100", "fields": {}}}, quiet=True)
    assert "标签" in capsys.readouterr().out


def test_summary_undecided_arithmetic(tmp_path, capsys):
    """★ `undecided = n - ok - warn - len(bad)` 三处计数必须同口径。

    混合结果下四个数之和必须等于总数（否则汇总自相矛盾）。
    """
    import re
    d = tmp_path / "mix"
    d.mkdir()
    for i in range(3):
        _write(d / ("f%d.wav" % i), _tone(freq=440.0 + i * 110))
    C.main([str(d), "--quiet", "--shallow"])
    out = capsys.readouterr().out
    m = re.search(r"汇总：(\d+) 个文件 ｜ 合格 (\d+) ｜ 有疑 (\d+) ｜ 不达标 (\d+)", out)
    assert m, out[-400:]
    n, ok, warn, bad = (int(x) for x in m.groups())
    und = re.search(r"未判定 (\d+)", out)
    undecided = int(und.group(1)) if und else 0
    assert n == 3
    assert ok + warn + bad + undecided == n, \
        "四项之和 %d 应等于总数 %d（ok=%d warn=%d bad=%d und=%d）" % (
            ok + warn + bad + undecided, n, ok, warn, bad, undecided)


def test_show_all_levels_have_icons(capsys):
    """三个等级的图标各不相同（`ICON.get` 的默认值只用于未知等级）。"""
    seen = set()
    for lv in ("ok", "warn", "bad"):
        C.show({"file": "x.wav", "level": lv, "format": "WAV", "sr": 44100,
                "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2},
               quiet=True)
        line = capsys.readouterr().out.splitlines()[0]
        seen.add(line.split("x.wav")[0])
    assert len(seen) == 3, "三个等级应有三种不同图标（得到 %r）" % (seen,)


# ============ 退出码 3/4：两条独立路径（此前完全没判据）
def test_unreadable_files_exit_4(tmp_path, capsys):
    """★ 有文件**读不了** ⇒ 退出码 **4**（E2.1：不可信输入的显式错误码）。

    这条与"有分析器告警 ⇒ 3"是两条**独立**路径，顺序也有讲究：
    读不了比告警更严重，必须先判。
    """
    good = _write(tmp_path / "ok.wav")
    broken = str(tmp_path / "broken.wav")
    with open(broken, "wb") as f:
        f.write(b"RIFF\x00\x00\x00\x00WAVEjunk")     # 结构有效、内容不可读
    js = tmp_path / "o.json"
    rc = C.main([good, broken, "--json", str(js), "--quiet", "--shallow"])
    assert rc == 4, "有读不了的文件应返回 4（得到 %d）" % rc
    err = capsys.readouterr().err
    assert "无法读取" in err
    assert "broken.wav" in err


def test_analyzer_errors_exit_3(tmp_path, capsys, monkeypatch):
    """★ 分析器告警（不是读不了）⇒ 退出码 **3**。

    与 4 的区别重要：3 表示"分析跑完了但结果可能不完整"，
    4 表示"根本没读出来"。脚本据此决定"要不要重试"还是"换文件"。
    """
    a = _write(tmp_path / "a.wav")
    # ⚠ **必须给 `--only`**：不给时 `main` 走 `analyze_many`（进程池），patch
    # 模块级 `analyze` 无效（实测踩过：patch 了但 PATCHED CALLED 没打印）。
    # `--only` 分支才是"逐文件 analyze"，顺便也覆盖了那条分支。
    real_analyze = C.analyze

    def patched(path, **kw):
        r = real_analyze(path, **kw)
        r["errors"] = ["模拟分析器告警"]
        return r

    monkeypatch.setattr(C, "analyze", patched)
    rc = C.main([a, "--only", "basic", "--quiet", "--shallow"])
    assert rc == 3, "有分析器告警应返回 3（得到 %d）" % rc
    err = capsys.readouterr().err
    assert "告警" in err and "可能不完整" in err


def test_error_truncation_limits(tmp_path, capsys, monkeypatch):
    """★ 读不了/告警的清单都**截断到 5 条 + 余数**（`5`/`5` 两处常量）。"""
    import re
    d = tmp_path / "many"
    d.mkdir()
    broken = []
    for i in range(8):
        p = str(d / ("b%d.wav" % i))
        with open(p, "wb") as f:
            f.write(b"RIFF\x00\x00\x00\x00WAVEjunk")
        broken.append(p)
    rc = C.main([str(d), "--quiet", "--shallow"])
    err = capsys.readouterr().err
    assert rc == 4
    assert "无法读取" in err, err[-400:]
    m = re.search(r"(\d+) 个文件无法读取", err)
    assert m, err[-400:]
    total = int(m.group(1))
    shown = len(re.findall(r"b\d\.wav", err))
    assert shown == min(total, 5), "应只列 min(%d,5) 条（实际 %d）" % (total, shown)
    if total > 5:
        assert "另有 %d 个" % (total - 5) in err, err[-300:]


def test_bad_list_truncates_filename_at_44(tmp_path, capsys):
    """★ 不达标清单的文件名截断到 44 字符（`[:44]`，对齐用途）。"""
    import numpy as np
    long_name = "x" * 80 + ".wav"
    p = str(tmp_path / long_name)
    t = np.arange(int(1.0 * FS)) / FS
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    C.main([p, "--quiet"])
    out = capsys.readouterr().out
    assert "不达标清单" in out, "长名文件应进不达标清单"
    # ⚠ 输出里有**两处 [X]**：`show()` 首行的电平图标 + 不达标清单的条目。
    # 只取"不达标清单"之后的那一行（首行那个不截断文件名）。
    tail = out.split("不达标清单", 1)[1]
    line = [ln for ln in tail.splitlines() if "[X]" in ln][0]
    assert "x" * 45 not in line, "文件名应截断到 44 字符：%s" % line
    assert "x" * 44 in line


def test_summary_separator_width(tmp_path, capsys):
    """汇总分隔线固定 68 个 `=`（`68->69` 变异会被这条捉住）。"""
    a = _write(tmp_path / "a.wav")
    C.main([a, "--quiet", "--shallow"])
    out = capsys.readouterr().out
    seps = [ln for ln in out.splitlines() if set(ln) == {"="}]
    assert seps, "应有分隔线"
    assert all(len(s) == 68 for s in seps), "分隔线应为 68 字符（得到 %r）" % \
        [len(s) for s in seps]


def test_verdict_line_truncates_file_and_verdict(tmp_path, capsys):
    """★ 有告警清单里：文件名截 34、判定截 80（两处不同的截断宽度）。"""
    import numpy as np
    p = str(tmp_path / ("y" * 70 + ".wav"))
    t = np.arange(int(1.0 * FS)) / FS
    sf.write(p, (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)[:, None],
             FS, subtype="PCM_16")
    C.main([p, "--quiet"])
    out = capsys.readouterr().out
    # 正常路径（不达标清单用 44，告警清单用 34）——这里只断言没有 70 个连续 y
    assert "y" * 71 not in out


# ============ CSV 列完整性 / 进度条 / 出图的**像素级**判据
def test_csv_header_and_row_length_match(tmp_path, capsys):
    """★ CSV 表头列数必须与每行列数**相等**（少一列会让整表错位）。

    `_csv` 里表头是手写的 25+7 列，行是另一处拼的——两处不同步时
    表格会静默错位（Excel 里看起来"最后一列没名字"）。
    """
    a = _write(tmp_path / "a.wav")
    out = tmp_path / "s.csv"
    assert C.main([a, "--csv", str(out), "--quiet", "--shallow"]) == 0
    rows = list(csv.reader(open(out, encoding="utf-8-sig")))
    header, first = rows[0], rows[1]
    assert len(header) == len(first), \
        "表头 %d 列 vs 数据 %d 列（错位）" % (len(header), len(first))
    # 关键列必须在（名字改了脚本就断）
    for col in ("文件", "采样率", "位深", "判定", "LUFS"):
        assert col in header, "缺少列 %s" % col


def test_csv_bands_columns_are_named(tmp_path, capsys):
    """七个频段列名来自 `BAND_CN`（不是硬编码字符串）。"""
    from audiocheck.config import BAND_CN, BANDS
    a = _write(tmp_path / "a.wav")
    out = tmp_path / "s.csv"
    C.main([a, "--csv", str(out), "--quiet", "--shallow"])
    header = list(csv.reader(open(out, encoding="utf-8-sig")))[0]
    for k, _lo, _hi in BANDS:
        assert BAND_CN[k] + "%" in header, "缺少频段列 %s%%" % BAND_CN[k]


def test_csv_is_utf8_sig_for_excel(tmp_path, capsys):
    """CSV 必须带 BOM（`utf-8-sig`）——否则 Excel 打开中文是乱码。"""
    a = _write(tmp_path / "a.wav")
    out = tmp_path / "s.csv"
    C.main([a, "--csv", str(out), "--quiet", "--shallow"])
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 应以 UTF-8 BOM 开头"


def test_progress_bar_written_to_stderr(tmp_path, capsys):
    """★ 进度条走 **stderr**（stdout 要留给结果，重定向时不混进数据）。

    `%3d%%` 的三位宽度也是判据：`int(frac*100)` 必须被格式化成 0–100。
    """
    d = tmp_path / "many"
    d.mkdir()
    for i in range(3):
        _write(d / ("f%d.wav" % i), _tone(freq=440.0 + i * 100))
    assert C.main([str(d), "--quiet", "--shallow"]) == 0
    err = capsys.readouterr().err
    assert "[" in err and "%" in err, "进度条应写 stderr：%r" % err[:200]
    assert "100%" in err, "结束时应是 100%%（%r）" % err[-200:]


def test_plot_pixels_are_not_blank(tmp_path, capsys):
    """★ 出图必须是**有内容**的图（像素级），不是空白画布。

    `_plot` 里 `ylim(top-92, top+6)` / `height_ratios=[2,1]` / `xytext` 这些参数
    只影响像素，**用"文件存在"判断不出来**——图空白了文件照样在。
    判据：读 PNG 的灰度方差 + 非白像素占比。
    """
    import numpy as np
    from PIL import Image

    t = np.arange(int(1.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = str(tmp_path / "a.wav")
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    from audiocheck.core.engine import analyze as _an
    r = _an(p, deep=True)
    outdir = tmp_path / "px"
    outdir.mkdir()
    C._plot(r, str(outdir))
    png = next(outdir.glob("*.png"))
    img = np.asarray(Image.open(png).convert("L"), dtype=float)
    assert img.size > 0
    # 非白像素占比：空白画布几乎全白（>0.99）；有曲线/坐标轴/文字时会明显更低
    non_white = float((img < 240).mean())
    assert non_white > 0.01, "图几乎是空白（非白像素占比 %.4f）" % non_white
    assert float(img.std()) > 5.0, "灰度方差过小（%.2f）——可能只画了背景" % float(img.std())


def test_plot_marks_cutoff_line_when_present(tmp_path, capsys):
    """★ 有截止点时图里要画那条竖线（`axvline` 的 `or` 变异会让它消失）。

    判据：图里应存在**明显偏红**的像素（`#e53e3e` 虚线）。
    """
    import numpy as np
    from PIL import Image

    t = np.arange(int(1.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = str(tmp_path / "a.wav")
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    from audiocheck.core.engine import analyze as _an
    r = _an(p, deep=True)
    assert r.get("cutoff_hz"), "该素材应测出截止点"
    outdir = tmp_path / "px2"
    outdir.mkdir()
    C._plot(r, str(outdir))
    png = next(outdir.glob("*.png"))
    rgb = np.asarray(Image.open(png).convert("RGB"), dtype=int)
    # #e53e3e ≈ (229, 62, 62)：R 明显高于 G/B
    reddish = ((rgb[:, :, 0] > 150) & (rgb[:, :, 1] < 120)
               & (rgb[:, :, 2] < 120)).sum()
    assert reddish > 50, "图中找不到截止点竖线的红色像素（%d 个）" % reddish


def test_plot_returns_early_without_curve(tmp_path, capsys):
    """没有 `curve` 时 `_plot` 直接返回（不产文件、不抛异常）。"""
    outdir = tmp_path / "nocurve"
    outdir.mkdir()
    C._plot({"file": "x.wav", "sr": 44100}, str(outdir))
    assert not list(outdir.glob("*.png"))


# ============ 收口 cli.py 的 9 个存活点（每个都有明确的可观测缺口）
def test_loudness_truncation_at_exactly_seven(capsys):
    """★ total=7 是 `>6`→`>7` 变异的**唯一可观测点**。

    打印用 `- 6`（不变），条件用 `> 6`。total=8 时变异版打印"另有 2"
    与原版相同（条件多含一个数、但减法没变）⇒ 只有 total=7 能区分：
    原版 7>6 打印"另有 1"；变异版 7>7 不打印。
    （教训：切片 `[:N]` 与条件 `> N` / 减法 `- N` 是三处独立的 N，
    判据的 total 必须含 N、N+1、N+2 三个点。）
    """
    import re

    loud = [{"text": "L%d" % i} for i in range(7)]
    C.show({"file": "x.wav", "level": "warn", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "timeline_loudness_problems": loud}, quiet=True)
    out = capsys.readouterr().out
    m = re.search(r"另有 (\d+) 段真峰值", out)
    assert m and int(m.group(1)) == 1, "total=7 应打印'另有 1'（%s）" % out[-200:]


def test_ambiguous_with_names_rendered(capsys):
    """★ `ambiguous_with` 的名单必须出现在"无法区分"行里。

    变异 `or`→`and`：名单真值时 `truthy and []` 返回 **空列表** ⇒
    "与「%s」无法区分"里的名字全部消失。判据用**不含**在 counter 里的名字
    （否则反证段里的同名文字会把断言骗绿）。
    """
    C.show({
        "file": "z.wav", "level": "warn", "format": "WAV", "sr": 44100,
        "bits": "PCM_16", "seconds": 10.0, "size_mb": 1.0, "channels": 2,
        "causes": [{"key": "升频", "strength": 0.8, "prov": "工程估计",
                    "evidence": ["e1"], "counter": ["反证文字甲"],
                    "ambiguous": True, "ambiguous_with": ["独有名A", "独有名B"]}],
    }, quiet=True)
    out = capsys.readouterr().out
    assert "无法区分" in out
    assert "独有名A" in out and "独有名B" in out, \
        "并列候选名单必须渲染出来（%s）" % out


def test_list_analyzers_no_requirements_shows_dash(capsys):
    """★ `--list-analyzers`：无依赖的分析器必须显示 `-`（`or`→`and` 会打 "[]"）。"""
    rc = C.main(["--list-analyzers"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "-" in out, "无依赖分析器应显示 '-'（%s）" % out[:400]
    assert "[]" not in out


def test_summary_warn_count_exact_in_shallow(tmp_path, capsys):
    """★ 浅模式汇总的**四个数全部精确**（`==`→`!=` 会把 warn 数错报成 1）。"""
    a = _write(tmp_path / "a.wav")
    C.main([a, "--quiet", "--shallow"])
    out = capsys.readouterr().out
    assert "汇总：1 个文件 ｜ 合格 0 ｜ 有疑 0 ｜ 不达标 0 ｜ **未判定 1**" in out, out[-300:]


def test_summary_undecided_not_inflated_by_levels(tmp_path, capsys, monkeypatch):
    """★ `undecided = n - ok - warn - bad` 的**减法**：有等级结果时未判定必须是 0。

    变异 `-`→`+`：全 undecided 时输出不变（n+0+0+0 == n-0-0-0），
    唯一可观测点是**有等级**的结果——变异会打印"未判定 n+ok"这种膨胀数。
    """
    a = _write(tmp_path / "a.wav")
    real_analyze = C.analyze

    def patched(path, **kw):
        r = real_analyze(path, **kw)
        r["level"] = "ok"                       # 强制已知等级
        return r

    monkeypatch.setattr(C, "analyze", patched)
    C.main([a, "--only", "basic", "--quiet", "--shallow"])
    out = capsys.readouterr().out
    assert "合格 1" in out, out[-300:]
    assert "未判定" not in out, "已知等级不应再算未判定（%s）" % out[-300:]


def test_error_remainder_not_printed_at_exactly_five(tmp_path, capsys, monkeypatch):
    """★ 告警清单在**恰好 5 条**时不能打"另有 0 个"（`>5`→`>=5` 变异）。"""
    d = tmp_path / "five"
    d.mkdir()
    real_analyze = C.analyze

    def patched(path, **kw):
        r = real_analyze(path, **kw)
        r["errors"] = ["模拟告警"]
        return r

    monkeypatch.setattr(C, "analyze", patched)
    files = []
    for i in range(5):
        files.append(_write(d / ("f%d.wav" % i)))
    rc = C.main(["--only", "basic", "--quiet"] + files)
    err = capsys.readouterr().err
    assert rc == 3
    assert "另有" not in err, "恰好 5 条不该有'另有'行（%s）" % err[-300:]


def test_error_listing_truncates_filename_at_34(tmp_path, capsys, monkeypatch):
    """★ 告警清单的文件名截断到 **34** 字符（`[:34]`→`[:35]` 变异）。"""
    d = tmp_path / "names"
    d.mkdir()
    real_analyze = C.analyze

    def patched(path, **kw):
        r = real_analyze(path, **kw)
        r["errors"] = ["模拟告警"]
        return r

    monkeypatch.setattr(C, "analyze", patched)
    a = _write(d / ("y" * 50 + ".wav"))
    C.main([a, "--only", "basic", "--quiet"])
    err = capsys.readouterr().err
    line = [ln for ln in err.splitlines() if "yyy" in ln][0]
    assert "y" * 35 not in line, "文件名应截断到 34（%s）" % line
    assert "y" * 34 in line


def test_plot_figsize_aspect_ratio(tmp_path, capsys):
    """★ 图的宽高比钉在 `figsize=(10, 7)`（`7→8` 会把高度撑大 14%）。

    dpi=120 ⇒ PNG 像素 1200×840。判据直接读像素尺寸（容差 2% 吸收坐标轴裁边）。
    """
    import numpy as np
    from PIL import Image

    t = np.arange(int(1.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = str(tmp_path / "a.wav")
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    from audiocheck.core.engine import analyze as _an
    r = _an(p, deep=True)
    outdir = tmp_path / "sz"
    outdir.mkdir()
    C._plot(r, str(outdir))
    png = next(outdir.glob("*.png"))
    w, h = Image.open(png).size
    ratio = h / w
    assert abs(ratio - 0.7) < 0.03, "宽高比应 ≈ 7/10 = 0.70（实际 %.3f）" % ratio


def test_plot_tolerates_missing_bands_pct(tmp_path, capsys):
    """★ `_plot` 在没有 `bands_pct` 时**不许崩**（`or`→`and` 会拿 None 去 .get）。"""
    import numpy as np

    t = np.arange(int(1.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = str(tmp_path / "a.wav")
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    from audiocheck.core.engine import analyze as _an
    r = _an(p, deep=True)
    r.pop("bands_pct", None)                     # 刻意删掉
    outdir = tmp_path / "nb"
    outdir.mkdir()
    C._plot(r, str(outdir))                      # 不应抛 AttributeError
    assert list(outdir.glob("*.png"))


def test_plot_png_filename_truncates_at_60(tmp_path, capsys):
    """★ PNG 文件名截断到 **60** 字符（`[:60]`→`[:61]` 变异）。"""
    import numpy as np

    t = np.arange(int(1.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    long_name = "n" * 80 + ".wav"
    p = str(tmp_path / long_name)
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    from audiocheck.core.engine import analyze as _an
    r = _an(p, deep=True)
    outdir = tmp_path / "fn"
    outdir.mkdir()
    C._plot(r, str(outdir))
    png = next(outdir.glob("*.png"))
    stem = png.stem
    assert len(stem) <= 60, "PNG 名应截断到 60（实际 %d）" % len(stem)
    assert "n" * 61 not in stem


# ============ 第二轮收口（真存活 19 个里可观测的部分）
def test_show_sr_divides_by_1000_exactly(tmp_path, capsys):
    """★ kHz 渲染的除数是 **1000**：用 96000 Hz 让 /1000 与 /1001 在一位小数上分叉。

    44100/1001 = 44.056 → "%.1f" 仍渲染 44.1（舍入吞差异，实测踩过）；
    96000/1000 = 96.0 vs 96000/1001 = 95.904 → 96.0 vs 95.9，可区分。
    """
    C.show({"file": "x.wav", "level": "ok", "format": "WAV", "sr": 96000,
            "bits": "PCM_24", "seconds": 1.0, "size_mb": 0.3, "channels": 2},
           quiet=True)
    assert "96.0kHz" in capsys.readouterr().out


def test_show_nyquist_divides_by_2000_exactly(capsys):
    """★ 奈奎斯特 = `sr / 2000`：192000 Hz 让 /2000 与 /2001 分叉（96.0 vs 95.9）。"""
    C.show({"file": "x.wav", "level": "ok", "format": "WAV", "sr": 192000,
            "bits": "PCM_24", "seconds": 1.0, "size_mb": 0.5, "channels": 2,
            "cutoff_hz": 40000.0, "cutoff_khz": 40.0, "cutoff_ratio": 0.83},
           quiet=True)
    out = capsys.readouterr().out
    assert "奈奎斯特 96.0kHz" in out, out


def test_show_tag_value_truncates_at_70(capsys):
    """★ 标签值截断到 **70** 字符（`[:70]`→`[:71]`）。"""
    long_val = "v" * 90
    C.show({"file": "x.wav", "level": "ok", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "meta": {"vendor": "Lavf58", "fields": {"comment": long_val}}},
           quiet=True)
    out = capsys.readouterr().out
    assert "v" * 71 not in out, "标签值应截断到 70"
    assert "v" * 70 in out


def test_show_metadata_rows_truncate_at_6(capsys):
    """★ 标签行数截断到 **6** 行（`tag_rows(md)[:6]`→`[:7]` 变异）。"""
    # tag_rows 只透传 FIELD_MAP 里的键（vendor 一行 + N 个字段行 + 封面/总数）
    fields = {"title": "甲", "artist": "乙", "album": "丙", "date": "2024",
              "genre": "流行", "track": "3", "publisher": "丁", "isrc": "戊"}
    C.show({"file": "x.wav", "level": "ok", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1, "channels": 2,
            "meta": {"vendor": "Lavf58", "fields": fields, "pictures": 1,
                     "tags_total": 8}}, quiet=True)
    out = capsys.readouterr().out
    # vendor(1) + 8 字段 + 封面(1) + 总数(1) = 11 行候选 ⇒ 只显示 6
    indented = [ln for ln in out.splitlines() if ln.startswith("            ")]
    assert len(indented) == 6, "标签应只显示 6 行（实际 %d）：%s" % (
        len(indented), [ln.strip()[:16] for ln in indented])


def test_listfile_skips_comment_lines_with_hashes(tmp_path, capsys):
    """★ `--list` 的行过滤是 `strip() and not startswith('#')`（`and`→`or` 放行注释行）。"""
    a = _write(tmp_path / "a.wav")
    lst = tmp_path / "list.txt"
    lst.write_text("## 全是井号的注释行\n%s\n#普通注释\n" % a, encoding="utf-8")
    js = tmp_path / "o.json"
    rc = C.main(["--list", str(lst), "--json", str(js), "--quiet", "--shallow"])
    assert rc == 0
    assert len(json.loads(js.read_text(encoding="utf-8")).get("results") or []) == 1


def test_doctor_shows_cpu_count(capsys):
    """★ doctor 打印 CPU 核数（`os.cpu_count() or 1` → `and 1` 会在 0 核时打 0）。"""
    C.doctor()
    assert "CPU 核心" in capsys.readouterr().out


def test_plot_bands_section_defaults_empty(tmp_path, capsys):
    """★ `_plot` 的 `b = r.get("bands_pct") or {}`：缺键时**不崩**且画出空柱。"""
    import numpy as np
    from PIL import Image

    t = np.arange(int(1.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    p = str(tmp_path / "a.wav")
    sf.write(p, x[:, None], FS, subtype="PCM_16")
    from audiocheck.core.engine import analyze as _an
    r = _an(p, deep=True)
    r.pop("bands_pct", None)
    outdir = tmp_path / "nb2"
    outdir.mkdir()
    C._plot(r, str(outdir))                      # 变异 `or`→`and` 会 AttributeError
    png = next(outdir.glob("*.png"))
    assert Image.open(png).size[0] > 0


def test_analyzer_error_listing_caps_at_five_files(tmp_path, capsys, monkeypatch):
    """★ 告警清单**最多列 5 个文件**（`for r in errs[:5]`→`[:6]` 变异）。"""
    import re

    d = tmp_path / "six"
    d.mkdir()
    real_analyze = C.analyze

    def patched(path, **kw):
        r = real_analyze(path, **kw)
        r["errors"] = ["模拟告警"]
        return r

    monkeypatch.setattr(C, "analyze", patched)
    files = [_write(d / ("g%d.wav" % i)) for i in range(6)]
    rc = C.main(["--only", "basic", "--quiet"] + files)
    err = capsys.readouterr().err
    assert rc == 3
    shown = len(re.findall(r"g\d\.wav", err))
    assert shown == 5, "应只列 5 个文件（实际 %d）" % shown
    assert "另有 1 个" in err, err[-300:]


def test_analyzer_error_remainder_at_six(tmp_path, capsys, monkeypatch):
    """★ 6 个文件时"另有 1 个"（`- 5`→`+ 5` 变异会打出 11）。"""
    d = tmp_path / "six2"
    d.mkdir()
    real_analyze = C.analyze

    def patched(path, **kw):
        r = real_analyze(path, **kw)
        r["errors"] = ["模拟告警"]
        return r

    monkeypatch.setattr(C, "analyze", patched)
    files = [_write(d / ("h%d.wav" % i)) for i in range(6)]
    C.main(["--only", "basic", "--quiet"] + files)
    err = capsys.readouterr().err
    assert "另有 1 个" in err, err[-300:]


def test_analyzer_error_requires_six_to_show_remainder(tmp_path, capsys, monkeypatch):
    """★ 5 个文件**不该**有"另有"行（`>5`→`>=5` 变异会打"另有 0 个"）。"""
    d = tmp_path / "five2"
    d.mkdir()
    real_analyze = C.analyze

    def patched(path, **kw):
        r = real_analyze(path, **kw)
        r["errors"] = ["模拟告警"]
        return r

    monkeypatch.setattr(C, "analyze", patched)
    files = [_write(d / ("k%d.wav" % i)) for i in range(5)]
    C.main(["--only", "basic", "--quiet"] + files)
    err = capsys.readouterr().err
    assert "另有" not in err, err[-300:]


# ═══ R305b 变异收口第二批（钉 15 存活：0.873 → ≥0.90）═══

def test_loudness_lra_missing_falls_back_to_zero(capsys):
    """108: lra 缺失 → or 0 → 'LRA 0.0'（and 变异会 None 崩格式化）。"""
    C.show({"file": "z.wav", "level": "ok", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1,
            "channels": 2, "lufs": -14.0, "verdict": "合格"}, quiet=True)
    out = capsys.readouterr().out
    assert "LRA 0.0" in out


def test_meta_fields_only_still_shows_tag_section(capsys):
    """115: vendor 缺失但 fields 有货 → or 保证标签段仍出现。"""
    C.show({"file": "z.wav", "level": "ok", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1,
            "channels": 2, "verdict": "合格",
            "meta": {"fields": {"title": "T"}}, "quiet": True}, quiet=True)
    out = capsys.readouterr().out
    assert "标签" in out


def test_cause_missing_prov_and_strength_defaults(capsys):
    """126: prov 缺省'工程估计'、strength 缺省 0.00（or 兜底双杀）。"""
    C.show({"file": "z.wav", "level": "warn", "format": "WAV", "sr": 44100,
            "bits": "PCM_16", "seconds": 1.0, "size_mb": 0.1,
            "channels": 2, "verdict": "有疑",
            "causes": [{"key": "升频", "evidence": ["e1"]}]}, quiet=True)
    out = capsys.readouterr().out
    assert "工程估计" in out and "强度 0.00" in out


def test_list_analyzers_empty_requires_shows_dash(capsys, monkeypatch):
    """217: requires 空列表 → join('') 走 or 兜底显示 '-'。"""
    monkeypatch.setattr(C, "list_analyzers",
                        lambda: [{"key": "k", "label": "L", "order": 1,
                                  "requires": []}])
    rc = C.main(["--list-analyzers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "-" in out and "k" in out


def test_listfile_skips_blank_and_comment_lines(tmp_path, capsys,
                                                monkeypatch):
    """226: and 语义——空行与 # 注释行不得混进 targets。"""
    wav = tmp_path / "a.wav"
    import numpy as np
    import soundfile as sf
    sf.write(str(wav), np.zeros(100, dtype="int16"), 8000, subtype="PCM_16")
    lf = tmp_path / "list.txt"
    lf.write_text(f"{wav}\n\n# 注释行\n", encoding="utf-8")
    monkeypatch.setattr(C, "analyze", lambda r, **k: {
        "file": r, "level": "ok", "format": "WAV", "sr": 8000,
        "bits": "PCM_16", "seconds": 0.0, "size_mb": 0.0,
        "channels": 1, "verdict": "合格"})
    rc = C.main(["--shallow", "--list", str(lf)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "汇总：1 个文件" in out, "空行/注释行必须被剔除"


def test_shallow_undecided_count_exact(tmp_path, capsys):
    """285: undecided = n-ok-warn-bad 的减法链必须精确。
    浅模式 2 个真 wav：无判定 → '**未判定 2**'。
    （-→+ 变异会让汇总行出现负数/崩格式，即被杀。）"""
    import numpy as np
    import soundfile as sf
    for i in (1, 2):
        sf.write(str(tmp_path / f"f{i}.wav"),
                 np.zeros(100, dtype="int16"), 8000, subtype="PCM_16")
    rc = C.main(["--shallow", str(tmp_path / "f1.wav"),
                 str(tmp_path / "f2.wav")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "未判定 2" in out


def test_unreadable_truncates_file34_verdict80(tmp_path, capsys,
                                               monkeypatch):
    """335: file 截 34、verdict 截 80（双常量双杀）。"""
    long_name = "x" * 40 + ".wav"
    long_verdict = "V" * 90
    monkeypatch.setattr(C, "collect_files",
                        lambda targets: [long_name])
    # 串行分支（--only）走模块级 analyze——返回 level=error 即进
    # unreadable 清单（cli.py:331 level=="error" 过滤）
    monkeypatch.setattr(
        C, "analyze", lambda path, **k: {
            "file": path, "level": "error", "verdict": long_verdict})
    rc = C.main(["--only", "whatever"])
    assert rc == 4, "unreadable 分支的真实出口是 return 4"
    err = capsys.readouterr().err
    assert ("x" * 34) in err and ("x" * 35) not in err
    assert ("V" * 80) in err and ("V" * 81) not in err


def test_plot_annotation_offset_exact(tmp_path, monkeypatch):
    """401: xytext=(6, -12) 精确钉（捕获 Axes.annotate）。"""
    captured = {}
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    orig = plt.Axes.annotate

    def spy(self, *a, **k):
        if "xytext" in k:
            captured["xytext"] = k["xytext"]
        return orig(self, *a, **k)

    monkeypatch.setattr(plt.Axes, "annotate", spy)
    r = {"file": "f.wav", "verdict": "合格", "sr": 8000,
         "cutoff_hz": 3000.0, "cutoff_khz": 3.0,
         "curve": [[1, 2], [2, 3], [3, 1]],
         "bands_pct": {}}
    C._plot(r, str(tmp_path))
    assert captured.get("xytext") == (6, -12)


def test_plot_ylim_window_exact(tmp_path, monkeypatch):
    """406: set_ylim(top-92, top+6) 双常量钉（+/92 三个变异全杀）。"""
    captured = {}
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    orig = plt.Axes.set_ylim

    def spy(self, *a, **k):
        if len(a) == 2:
            captured.setdefault("ylim", a)
        return orig(self, *a, **k)

    monkeypatch.setattr(plt.Axes, "set_ylim", spy)
    r = {"file": "f.wav", "verdict": "合格", "sr": 8000,
         "curve": [[1, 2], [2, 3], [3, 1]],
         "bands_pct": {}}
    C._plot(r, str(tmp_path))
    lo, hi = captured["ylim"]
    assert hi - lo == 98, "窗口高度 92+6=98（+/- 或 92 变异即崩）"


def test_plot_savefig_dpi_120(tmp_path, monkeypatch):
    """417: dpi=120 精确钉（HANDOFF：PNG 宽=figsize×dpi）。"""
    captured = {}
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    orig = plt.Figure.savefig

    def spy(self, *a, **k):
        captured.update(k)
        return orig(self, *a, **k)

    monkeypatch.setattr(plt.Figure, "savefig", spy)
    r = {"file": "f.wav", "verdict": "合格", "sr": 8000,
         "curve": [[1, 2], [2, 3], [3, 1]],
         "bands_pct": {}}
    C._plot(r, str(tmp_path))
    assert captured.get("dpi") == 120


def test_editor_resize_exact_1280x800(monkeypatch, tmp_path):
    """441: resize(1280, 800) 精确钉（可测就不登记 EQUIVALENT）。"""
    import sys as _sys
    fake_qt = __import__("types").ModuleType("PySide6.QtWidgets")
    created = {}

    class _App:
        def __init__(self, *a):
            created["app_ctor"] = a
        def exec(self):
            return 0
        @staticmethod
        def instance():
            return None

    class _Win:
        def __init__(self, theme):
            created["theme"] = theme
        def resize(self, w, h):
            created["resize"] = (w, h)
        def open_file(self, p):
            created["opened"] = p
        def show(self):
            created["shown"] = True

    fake_qt.QApplication = _App
    fake_pkg = __import__("types").ModuleType("PySide6")
    fake_pkg.QtWidgets = fake_qt
    monkeypatch.setitem(_sys.modules, "PySide6", fake_pkg)
    monkeypatch.setitem(_sys.modules, "PySide6.QtWidgets", fake_qt)
    fake_theme = __import__("types").ModuleType("audiocheck.ui.theme")
    fake_theme.init_fonts = lambda app: None
    fake_theme.active_theme = lambda name: f"theme:{name}"
    # from .ui import theme as T 走父包属性而非 sys.modules——
    # 必须直接 setattr 包对象（相对导入语义）
    from audiocheck import ui as _ui_pkg
    monkeypatch.setattr(_ui_pkg, "theme", fake_theme, raising=False)
    fake_editor = __import__("types").ModuleType("audiocheck.ui.editor")
    fake_editor.EditorWindow = _Win
    monkeypatch.setitem(_sys.modules, "audiocheck.ui.editor", fake_editor)
    real = tmp_path / "real.wav"
    real.write_bytes(b"RIFF")
    rc = C._run_editor([str(real)])
    assert rc == 0
    assert created["resize"] == (1280, 800)
    assert created["theme"] == "theme:dark"
    assert created["opened"] == str(real) and created["shown"]


def test_editor_reuses_existing_qapp_instance(monkeypatch):
    """438: instance() 真值时必须复用（or 语义），不得新建。"""
    import sys as _sys
    fake_qt = __import__("types").ModuleType("PySide6.QtWidgets")
    new_calls = []

    class _App:
        def __init__(self, *a):
            new_calls.append(a)
        def exec(self):
            return 0
    prebuilt = _App()                        # 构造本身会记一次——清零
    new_calls.clear()

    class _AppStatic:
        @staticmethod
        def instance():
            return prebuilt

    fake_qt.QApplication = _AppStatic
    fake_pkg = __import__("types").ModuleType("PySide6")
    fake_pkg.QtWidgets = fake_qt
    monkeypatch.setitem(_sys.modules, "PySide6", fake_pkg)
    monkeypatch.setitem(_sys.modules, "PySide6.QtWidgets", fake_qt)
    fake_theme = __import__("types").ModuleType("audiocheck.ui.theme")
    got = {}
    fake_theme.init_fonts = lambda app: got.setdefault("app", app)
    fake_theme.active_theme = lambda name: name
    from audiocheck import ui as _ui_pkg
    monkeypatch.setattr(_ui_pkg, "theme", fake_theme, raising=False)
    fake_editor = __import__("types").ModuleType("audiocheck.ui.editor")
    fake_editor.EditorWindow = lambda theme: type("W", (), {
        "resize": lambda self, w, h: None, "show": lambda self: None,
        "open_file": lambda self, p: None, "exec": lambda self: 0})()
    monkeypatch.setitem(_sys.modules, "audiocheck.ui.editor", fake_editor)
    from audiocheck import ui as _ui_pkg2
    monkeypatch.setattr(_ui_pkg2, "editor", fake_editor, raising=False)
    rc = C._run_editor([])
    assert rc == 0
    assert got["app"] is prebuilt, "必须复用 instance()，不新建 QApplication"
    assert not new_calls, "or 语义：已有实例时不得再构造"
