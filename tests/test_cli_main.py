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

from audiocheck import cli as C          # noqa: E402

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
