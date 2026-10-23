"""渲染引擎：把 `(源, 操作栈)` 算成目标文件，**并返回一张审计记录**。

为什么值得单独立一层（而不是在 UI 里就地改数组）
------------------------------------------------
* **非破坏**：源只读；目标绝不等于源（见 `_check_dest`）。导出是纯函数 ⇒ 可复算。
* **可审计**：返回 dict，逐操作记录"实际生效了多少样本"、输出峰值、**削波样本数**，
  频域操作还记**实测的 STFT 往返残差**与**被改的时频点数**。闭源工具点了按钮你只知道
  "它改了"，这里你能拿到**改了什么、依据什么、结果如何**。
* **分块流式**：整轨不进内存，长素材（几十分钟）也不会炸。

两条路径，各自的口径
--------------------
* **时间域**（`ops.GainOp`）：逐样本分块。`gain_db == 0.0` 整条跳过 ⇒ 未选中处**逐位不变**。
  选区半开 `[t0, t1)`：样本 `k` 受影响 ⟺ `t0 <= k/fs < t1`。`ceil(t*fs-eps)` 落点，
  `eps=1e-9` 只为吃掉浮点误差（`t=0.5, fs=100` 必须落在样本 50 而不是 49）。
* **频域**（`ops.BandGainOp`）：**STFT/WOLA** 分块。过 STFT 的时间跨度 = 选区**两侧各外扩一个窗宽
  `n_fft`**，跨度之外**原样拷贝、逐位不变**；每块两侧各取 `n_fft` 个**真实**样本当上下文
  （上下文用真音频，不是补零——否则窗内的谱被人工零填污染），只输出中间那段。
  块长是 `hop` 的整数倍 ⇒ **跨块的帧网格一致**（同一帧在不同块里被算两次会得到同一个值，
  否则接缝处会出现两套相位）。
  ⚠ **为什么要外扩**（这是实测出来的，不是保险起见）：掩码按"帧中心 ∈ 选区"生效，而被改的
  帧的**窗口**会伸到选区外 ±`n_fft/2`。若在选区边界上把重建结果硬切掉，被压掉的成分
  会在边界处**留一个台阶**（实测：边界两侧 8 kHz 成分幅度从 1.0 掉到 0.61 ⇒ 一声 click）。
  外扩后，窗外自然恢复原样（窗本身是锥形的），跨度之内**未受影响**的采样仍只有
  ~1e-15 的往返残差、跨度之外逐位不变。
* **修复**（`ops.DeclipOp`）：**逐选区整段载入**，POCS 重建（带限投影 + 削波不等式投影，
  见 `declip.py`）。**只改饱和样本**：选区之外与选区内的非饱和样本都**逐位不变**。
  ⚠ 这一条**不能分块**（"带限"是全局约束，切块会在接缝上留台阶）⇒ 它把**选区**整段进内存，
  内存 ≈ `选区采样 × 声道 × 8 B × ~4`，这条边界写在 `_render_declip` 的 docstring 里。
  ⚠ `bandwidth ≥ 奈奎斯特` 时没有约束可利用 ⇒ **逐位不变**（如实记 `noop`）。
* **去嗡**（`ops.DehumOp`）：**逐选区整段载入**，零相位陷波级联（`dehum.py`）。
  ⚠ 陷波是 **LTI** ⇒ **整段选区都被改动**（不像去削波只改饱和样本）；选区之外逐位不变，
  审计如实记 `changed_samples` 与每个谐波的**实测压制量**。
* 五条路径（时间 / 频域 / 去削波 / 去咔哒 / 去嗡）**不许混用**（见 `render` 的分发）：
  顺序语义都还没立判据，宁可不做。多个同域操作的选区之间也不许重叠（同理）。
* 都遵守：**削波不静默**、**截断源不许 traceback**（沿用 E2.1 规矩）。
"""
from __future__ import annotations

import math
import os

import numpy as np
import soundfile as sf

from ..context import io_path
from . import declick, declip, dehum, stft
from .declick import DEFAULT_MAX_RUN, DEFAULT_THRESHOLD
from .dehum import DEFAULT_HARMONICS, DEFAULT_Q
from .metrics import band_gain_db, energy_ratio_db
from .ops import BandGainOp, DeclickOp, DeclipOp, DehumOp, GainOp, Project

#: 一次读入的帧数（与 `analyzers/basic.py` 的读块同量级）。
BLOCKSIZE = 1 << 16

#: 会被 libsndfile 钳位到 [−1, 1] 的整型 subtype（浮点不钳位）。
_INT_SUBTYPES = {"PCM_S8", "PCM_U8", "PCM_16", "PCM_24", "PCM_32"}

#: 频域路径的默认 STFT 参数。**这个 `n_fft` 与频谱图显示用的 `nperseg=16384` 不同，
#: 是有意的**（显示要平滑的统计谱，编辑要局部性：4096 @48k ⇒ 时间分辨率 ~85 ms、
#: 频率分辨率 ~11.7 Hz）。`docs/theory.md §12.1` 要求"视图与编辑共用同一套 STFT 参数"
#: ——那是对**在频谱图上直接刷**的 P2 视图的要求；等 P2 的选区视图落地时必须把两边
#: 口径对齐，否则"看到的"和"刷到的"不是同一个时频格。CLI 已暴露 `--n-fft`/`--hop`。
DEFAULT_N_FFT = 4096

#: 频域路径的分块粒度（块长 = `CHUNK_HOPS * hop` 个采样）。内存 ≈ 4×块长×声道×8B。
CHUNK_HOPS = 512

_EPS = 1e-9


def _sample_at(t: float, fs: int) -> int:
    """秒 → 样本下标（半开区间左端）：`ceil(t*fs - eps)`。"""
    return int(math.ceil(t * fs - _EPS))


def _check_dest(source: str, dest: str, overwrite: bool) -> tuple[str, str]:
    """目标路径守卫：**不许覆盖源**（非破坏的硬保证），默认也不许覆盖已存在文件。"""
    src, dst = os.path.abspath(source), os.path.abspath(dest)
    if os.path.normcase(src) == os.path.normcase(dst):
        raise ValueError("导出目标不能是源文件（非破坏保证）：%s" % dest)
    if not overwrite and os.path.exists(io_path(dst)):
        raise FileExistsError("目标已存在（要覆盖请显式 overwrite=True）：%s" % dest)
    return io_path(src), io_path(dst)


#: 目标扩展名 → libsndfile 容器名（用于**导出前**的子类型相容性检查）。
_FORMAT_BY_EXT = {".wav": "WAV", ".wave": "WAV", ".flac": "FLAC",
                  ".aiff": "AIFF", ".aif": "AIFF", ".au": "AU", ".ogg": "OGG"}


def _check_subtype(dest: str, inherited: str, explicit: str | None) -> None:
    """导出前检查"源子类型能不能装进目标容器"，**在动目标文件之前**报清楚。

    实测踩到的坑：源是 `FLOAT`（WAV），目标是 `.flac` 且没显式给 subtype ⇒ 继承 FLOAT ⇒
    libsndfile 抛 `Invalid combination of format, subtype and endian`（既晚又难懂，
    而且那时目标文件已经建了一半）。这里提前用 `sf.check_format` 判，并给出修法。
    """
    if explicit is not None:
        return
    fmt = _FORMAT_BY_EXT.get(os.path.splitext(dest)[1].lower())
    if fmt is None:
        return
    if not sf.check_format(fmt, inherited):
        raise ValueError(
            "目标容器 %s 不接受源子类型 %s ⇒ 请显式给 subtype（FLAC 建议 PCM_24，"
            "AIFF/WAV 可用 PCM_24 或 FLOAT）" % (fmt, inherited))


def _account(b: np.ndarray, st: dict) -> None:
    """输出块的**如实记账**：峰值、整型编码下的钳位样本数。"""
    if len(b):
        st["peak"] = max(st["peak"], float(np.abs(b).max()))
    if st["clips_int"]:
        st["clipped"] += int((np.abs(b) > 1.0).sum())


def _copy_range(f: sf.SoundFile, g: sf.SoundFile, pos: int, end: int,
                blocksize: int, st: dict) -> int:
    """把源 `[pos, end)` **原样**写到目标（逐位不变）。返回实际写到哪。"""
    f.seek(pos)
    while pos < end:
        try:
            b = f.read(min(blocksize, end - pos), dtype="float64", always_2d=True)
        except Exception as e:                    # noqa: BLE001 —— libsndfile 抛的形形色色
            st["read_error"] = e
            break
        if not len(b):
            break
        _account(b, st)
        g.write(b)
        pos += len(b)
    return pos


def render(project: Project, dest: str, *, subtype: str | None = None,
           blocksize: int = BLOCKSIZE, overwrite: bool = False,
           n_fft: int = DEFAULT_N_FFT, hop: int | None = None,
           chunk_hops: int = CHUNK_HOPS) -> dict:
    """把 `project` 渲染到 `dest`，返回**审计记录**（dict）。

    参数
    ----
    subtype    : 目标编码；`None` = 沿用源的 subtype（保证"没动的地方逐位不变"的前提）。
    overwrite  : 是否允许覆盖已存在的目标（源文件永远不许，见 `_check_dest`）。
    n_fft/hop  : **频域路径**的 STFT 参数（`hop` 默认 `n_fft//4`；时间域路径忽略）。
    """
    ops = list(project.ops)
    if not ops or all(isinstance(o, GainOp) for o in ops):
        return _render_time(project, dest, subtype=subtype, blocksize=blocksize,
                            overwrite=overwrite)
    if all(isinstance(o, BandGainOp) for o in ops):
        return _render_band(project, dest, subtype=subtype, blocksize=blocksize,
                            overwrite=overwrite, n_fft=n_fft, hop=hop, chunk_hops=chunk_hops)
    if all(isinstance(o, DeclipOp) for o in ops):
        return _render_declip(project, dest, subtype=subtype, blocksize=blocksize,
                              overwrite=overwrite)
    if all(isinstance(o, DeclickOp) for o in ops):
        return _render_declick(project, dest, subtype=subtype, blocksize=blocksize,
                               overwrite=overwrite)
    if all(isinstance(o, DehumOp) for o in ops):
        return _render_dehum(project, dest, subtype=subtype, blocksize=blocksize,
                             overwrite=overwrite)
    raise ValueError("同一项目里混用时间域/频域/去削波/去咔哒/去嗡操作：顺序语义尚未立判据（宁可不做）")


def _render_time(project: Project, dest: str, *, subtype: str | None,
                 blocksize: int, overwrite: bool) -> dict:
    """时间域路径：逐样本分块施加 `GainOp`。"""
    src, dst = _check_dest(project.source, dest, overwrite)
    applied = [0] * len(project.ops)          # 每个操作实际生效的样本数
    st = {"read_error": None, "peak": 0.0, "clipped": 0,
          "clips_int": False, "floor": 0.0, "floor_samples": 0}
    frames = 0
    claimed = 0

    with sf.SoundFile(src) as f:
        fs = int(f.samplerate)
        ch = int(f.channels)
        sub = subtype or f.subtype
        _check_subtype(dest, sub, subtype)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            pos = 0
            while True:
                # ⚠ 源**可能是截断文件**（真实曲库里就有：容器头声称满长帧数，
                # 读到 99.5% 才抛）。按 E2.1 的规矩：**不许 traceback**，如实记录
                # "读不满"，并把已读到的部分正常写出（否则整条编辑都白做）。
                try:
                    b = f.read(blocksize, dtype="float64", always_2d=True)
                except Exception as e:            # noqa: BLE001 —— libsndfile 抛的形形色色
                    st["read_error"] = e
                    break
                if not len(b):
                    break
                n = int(b.shape[0])
                for i, op in enumerate(project.ops):
                    if not isinstance(op, GainOp) or op.is_noop:
                        continue
                    k0 = max(pos, _sample_at(op.t0, fs))
                    k1 = min(pos + n, _sample_at(op.t1, fs))
                    if k1 <= k0:
                        continue
                    b[k0 - pos:k1 - pos] *= 10.0 ** (op.gain_db / 20.0)
                    applied[i] += k1 - k0
                _account(b, st)
                g.write(b)
                pos += n

        frames = pos

    return _audit(project, dest, "time", fs, ch, sub, frames, claimed, st, applied,
                  tf_points=None)


def _render_band(project: Project, dest: str, *, subtype: str | None, blocksize: int,
                 overwrite: bool, n_fft: int, hop: int | None, chunk_hops: int) -> dict:
    """频域路径：分块 STFT/WOLA，只碰时频选区内的谱点；其余原样拷贝。"""
    hop = n_fft // 4 if hop is None else hop
    stft.check_params(n_fft, hop)
    src, dst = _check_dest(project.source, dest, overwrite)

    st = {"read_error": None, "peak": 0.0, "clipped": 0,
          "clips_int": False, "floor": 0.0, "floor_samples": 0}
    applied = [0] * len(project.ops)        # 每个操作覆盖的采样数（按其自身时间选区）
    tf_points = [0] * len(project.ops)      # 每个操作**实际改动**的时频点数
    meas_e = [[0.0, 0.0] for _ in project.ops]   # 每个操作：掩码**前/后**的带内能量
    frames = 0
    claimed = 0

    with sf.SoundFile(src) as f:
        fs = int(f.samplerate)
        ch = int(f.channels)
        sub = subtype or f.subtype
        _check_subtype(dest, sub, subtype)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        win = stft.default_window(n_fft)
        bins = stft.bin_freqs(fs, n_fft)
        # 只保留**真会动**的操作（`gain_db == 0` 是 no-op，整条跳过 ⇒ 全轨逐位不变）
        active = [(i, o) for i, o in enumerate(project.ops)
                  if isinstance(o, BandGainOp) and not o.is_noop]
        band = [(bins >= o.f0) & (bins < o.f1) for _, o in active]
        gains = [10.0 ** (o.gain_db / 20.0) for _, o in active]
        # 要过 STFT 的时间跨度 = 所有活跃操作选区的并集，**两侧各外扩一个窗宽**
        # （见文件头那条 ⚠：写出的范围必须盖住掩码帧的"溢出"，否则在选区边界上
        # 硬切会把被压掉的成分切出一个台阶）。
        ks = [max(0, min(_sample_at(o.t0, fs), claimed)) for _, o in active]
        ke = [max(0, min(_sample_at(o.t1, fs), claimed)) for _, o in active]
        # ⚠ 没有一个活跃操作时跨度必须是**空**的：否则 `k0 = k1 = 0` 会让下面"两侧外扩"
        # 扩出 [0, n_fft) 一段，把开头一个窗的采样白白过一遍 STFT（no-op 就不再逐位不变）。
        k0, k1 = (min(ks), max(ke)) if active else (0, 0)
        span_lo = max(0, k0 - n_fft) if active else 0
        span_hi = min(claimed, k1 + n_fft) if active else 0

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            pos = _copy_range(f, g, 0, span_lo, blocksize, st)
            step = max(1, chunk_hops) * hop
            cs = span_lo
            while st["read_error"] is None and cs < span_hi:
                ce = min(cs + step, span_hi)
                src_lo = cs - n_fft                       # 块首帧的**源坐标**（可为负）
                buf = np.zeros((ce - cs + 2 * n_fft, ch), dtype=np.float64)
                lo, hi = max(0, src_lo), min(claimed, ce + n_fft)
                f.seek(lo)
                try:
                    raw = f.read(hi - lo, dtype="float64", always_2d=True)
                except Exception as e:                    # noqa: BLE001
                    st["read_error"] = e
                    break
                if len(raw) < hi - lo:                    # 声称有、实际读不满 ⇒ 截断
                    st["read_error"] = OSError(
                        "声称 %d 帧，向后读时只拿到 %d 帧" % (hi - lo, len(raw)))
                    ce = min(ce, lo + len(raw))
                    if ce <= cs:
                        break
                buf[lo - src_lo:lo - src_lo + len(raw)] = raw
                S, meta = stft.analyze(buf, n_fft=n_fft, hop=hop, win=win, pad=0)
                times = stft.frame_times(fs, n_fft, hop, S.shape[1], start_sample=src_lo)
                seg = slice(n_fft, n_fft + (ce - cs))     # 只输出中间那段（两侧是上下文）
                # ① **先量未改谱的往返残差** —— 这是"频谱编辑不是位精确"的实测值
                rt = stft.synthesize(S, meta, keep=seg)
                d = float(np.abs(rt - buf[seg]).max())
                st["floor"] = max(st["floor"], d)
                st["floor_samples"] += (ce - cs) * ch
                # ② 再施加时频掩码（帧中心 ∩ bin 中心，双半开）
                for j, (i, o) in enumerate(active):
                    tm = (times >= o.t0) & (times < o.t1)
                    n_pts = int(band[j].sum()) * int(tm.sum())
                    if n_pts:
                        # **同一套 STFT 表示里**量一遍"设定 vs 实测"：
                        # 在这套表示里量，窗泄漏不会被误算成"编辑没做够"（见 metrics 的注意）。
                        sel = np.ix_(band[j], tm)
                        meas_e[i][0] += float(np.sum(np.abs(S[sel]) ** 2))
                        S[sel] *= gains[j]
                        meas_e[i][1] += float(np.sum(np.abs(S[sel]) ** 2))
                        tf_points[i] += n_pts
                    a0 = max(cs, min(_sample_at(o.t0, fs), claimed))
                    a1 = min(ce, max(_sample_at(o.t1, fs), 0))
                    applied[i] += max(0, a1 - a0)
                out = stft.synthesize(S, meta, keep=seg)
                _account(out, st)
                g.write(out)
                pos = cs = ce
            if st["read_error"] is None:
                pos = _copy_range(f, g, pos, claimed, blocksize, st)

        frames = pos

    measured = [(round(10.0 * math.log10(a / b0), 3) if b0 > 0 else None)
                for b0, a in meas_e]
    return _audit(project, dest, "band", fs, ch, sub, frames, claimed, st, applied,
                  tf_points=tf_points, measured=measured, n_fft=n_fft, hop=hop)


def _declip_record(seg: np.ndarray, out: np.ndarray, sstat: dict, op: DeclipOp,
                   fs: int) -> dict:
    """去削波的**如实记账**：除了饱和样本数与 `c`，还报两条**实测**（同一把尺子）：

    * `energy_ratio_measured_db` = 输出/输入的**能量比**——PHAIN 警告的"变安静被当成修好"
      就靠它防（`docs/theory.md §12.2`）；
    * `out_of_band_measured_db` = "`> bandwidth` 那一段"的能量变化——它证明"带限投影
      **确实生效**"（削波会在带外造出能量，重建把它收了回去；实测为负值）。
      ⚠ 上界取 `fs`（半开区间 ⇒ **含奈奎斯特那条 bin**）：若写成 `fs/2`，在"奈奎斯特那条
      bin 上有内容"的素材上会漏掉它 ⇒ 口径不一致。判据：
      `test_out_of_band_measurement_covers_the_nyquist_bin`。
    """
    rec = dict(sstat)
    rec["energy_ratio_measured_db"] = _round_db(energy_ratio_db(out, seg))
    rec["out_of_band_measured_db"] = (
        _round_db(band_gain_db(seg, out, fs, op.bandwidth, float(fs)))
        if op.bandwidth < fs / 2.0 else None)
    return rec


def _round_db(v: float | None) -> float | None:
    return None if v is None else round(float(v), 3)


def _render_declip(project: Project, dest: str, *, subtype: str | None,
                   blocksize: int, overwrite: bool) -> dict:
    """去削波路径：**逐选区整段载入**，POCS 重建，只改饱和样本。

    ⚠ **不做分块**（与时间域/频域路径的差别，必须让用户看见）：POCS 的核心是
    "带限"这条**全局**约束（对整个选区做一次 FFT），分块会把约束切断、接缝处必然出现
    台阶。所以本路径把**选区**整段载入内存：内存 ≈ `选区采样 × 声道 × 8 B × ~4`。
    选区之外**逐位不变**（原样拷贝）；选区之内**非饱和样本逐位不变**。
    ⚠ 多个去削波的选区**不许重叠**（顺序语义未立判据）⇒ 这里先校验完再动目标文件，
    免得留下半截输出。
    """
    src, dst = _check_dest(project.source, dest, overwrite)
    ops = list(project.ops)
    st = {"read_error": None, "peak": 0.0, "clipped": 0, "clips_int": False,
          "floor": 0.0, "floor_samples": 0}
    applied = [0] * len(ops)          # 每个操作被选区**覆盖**的采样数
    recs = [None] * len(ops)
    frames = 0
    claimed = 0

    with sf.SoundFile(src) as f:
        fs = int(f.samplerate)
        ch = int(f.channels)
        sub = subtype or f.subtype
        _check_subtype(dest, sub, subtype)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        spans = [(max(0, min(_sample_at(o.t0, fs), claimed)),
                  max(0, min(_sample_at(o.t1, fs), claimed))) for o in ops]
        order = sorted(range(len(ops)), key=lambda i: spans[i][0])
        end = 0
        for i in order:
            if spans[i][0] < end:
                raise ValueError("多个去削波选区**重叠**：先改哪段没有定义（宁可不做）")
            end = spans[i][1]

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            cursor = 0
            pos = 0
            for i in order:
                k0, k1 = spans[i]
                if k1 <= k0:
                    continue
                pos = cursor = _copy_range(f, g, cursor, k0, blocksize, st)
                if st["read_error"] is not None:
                    break
                f.seek(k0)
                try:
                    seg = f.read(k1 - k0, dtype="float64", always_2d=True)
                except Exception as e:                # noqa: BLE001 —— libsndfile 形形色色
                    st["read_error"] = e
                    break
                if len(seg) < k1 - k0:                # 声称有、实际读不满 ⇒ 截断
                    st["read_error"] = OSError(
                        "声称 %d 帧，向后读时只拿到 %d 帧" % (k1 - k0, len(seg)))
                    if not len(seg):
                        break
                op = ops[i]
                out, sstat = declip.reconstruct(seg, op.ceiling, fs, op.bandwidth,
                                                iterations=op.iterations)
                recs[i] = _declip_record(seg, out, sstat, op, fs)
                applied[i] = int(seg.shape[0])
                _account(out, st)
                g.write(out)
                pos = cursor = k0 + int(out.shape[0])
            if st["read_error"] is None:
                pos = _copy_range(f, g, cursor, claimed, blocksize, st)
        frames = pos

    return _audit(project, dest, "declip", fs, ch, sub, frames, claimed, st, applied,
                  tf_points=None, op_recs=recs)


def _render_declick(project: Project, dest: str, *, subtype: str | None,
                    blocksize: int, overwrite: bool) -> dict:
    """去咔哒路径：**逐选区整段载入**（探测器的 MAD 尺子是**全段**统计量 ⇒ **不分块**），

    只改**检出样本**：选区之外**逐位不变**，选区之内**未被检出的样本逐位不变**。
    ⚠ 多个去咔哒的选区**不许重叠**（顺序语义未立判据）⇒ 先校验完再动目标文件。
    """
    src, dst = _check_dest(project.source, dest, overwrite)
    ops = list(project.ops)
    st = {"read_error": None, "peak": 0.0, "clipped": 0, "clips_int": False,
          "floor": 0.0, "floor_samples": 0}
    applied = [0] * len(ops)
    recs = [None] * len(ops)
    frames = 0
    claimed = 0

    with sf.SoundFile(src) as f:
        fs = int(f.samplerate)
        ch = int(f.channels)
        sub = subtype or f.subtype
        _check_subtype(dest, sub, subtype)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        spans = [(max(0, min(_sample_at(o.t0, fs), claimed)),
                  max(0, min(_sample_at(o.t1, fs), claimed))) for o in ops]
        order = sorted(range(len(ops)), key=lambda i: spans[i][0])
        end = 0
        for i in order:
            if spans[i][0] < end:
                raise ValueError("多个去咔哒选区**重叠**：先改哪段没有定义（宁可不做）")
            end = spans[i][1]

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            cursor = 0
            pos = 0
            for i in order:
                k0, k1 = spans[i]
                if k1 <= k0:
                    continue
                pos = cursor = _copy_range(f, g, cursor, k0, blocksize, st)
                if st["read_error"] is not None:
                    break
                f.seek(k0)
                try:
                    seg = f.read(k1 - k0, dtype="float64", always_2d=True)
                except Exception as e:                # noqa: BLE001 —— libsndfile 形形色色
                    st["read_error"] = e
                    break
                if len(seg) < k1 - k0:                # 声称有、实际读不满 ⇒ 截断
                    st["read_error"] = OSError(
                        "声称 %d 帧，向后读时只拿到 %d 帧" % (k1 - k0, len(seg)))
                    if not len(seg):
                        break
                op = ops[i]
                out, sstat = declick.declick(seg, op.threshold, op.max_run)
                recs[i] = {**sstat, "energy_ratio_measured_db":
                           _round_db(energy_ratio_db(out, seg))}
                applied[i] = int(seg.shape[0])
                _account(out, st)
                g.write(out)
                pos = cursor = k0 + int(out.shape[0])
            if st["read_error"] is None:
                pos = _copy_range(f, g, cursor, claimed, blocksize, st)
        frames = pos

    return _audit(project, dest, "declick", fs, ch, sub, frames, claimed, st, applied,
                  tf_points=None, op_recs=recs)


def _render_dehum(project: Project, dest: str, *, subtype: str | None,
                  blocksize: int, overwrite: bool) -> dict:
    """去嗡路径：**逐选区整段载入**（零相位 `filtfilt` 要看整段），零相位陷波级联。

    ⚠ 与去削波/去咔哒的差别：陷波是 **LTI** ⇒ **整段选区都被改动**（不是"只改检出样本"）。
    选区之外仍然**逐位不变**（原样拷贝）；选区之内被改的采样数如实记 `changed_samples`。
    ⚠ 多个去嗡的选区**不许重叠**（同一段被滤两遍的语义未立判据）⇒ 先校验完再动目标文件。
    """
    src, dst = _check_dest(project.source, dest, overwrite)
    ops = list(project.ops)
    st = {"read_error": None, "peak": 0.0, "clipped": 0, "clips_int": False,
          "floor": 0.0, "floor_samples": 0}
    applied = [0] * len(ops)
    recs = [None] * len(ops)
    frames = 0
    claimed = 0

    with sf.SoundFile(src) as f:
        fs = int(f.samplerate)
        ch = int(f.channels)
        sub = subtype or f.subtype
        _check_subtype(dest, sub, subtype)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        spans = [(max(0, min(_sample_at(o.t0, fs), claimed)),
                  max(0, min(_sample_at(o.t1, fs), claimed))) for o in ops]
        order = sorted(range(len(ops)), key=lambda i: spans[i][0])
        end = 0
        for i in order:
            if spans[i][0] < end:
                raise ValueError("多个去嗡选区**重叠**：同一段被滤两遍没有定义（宁可不做）")
            end = spans[i][1]

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            cursor = 0
            pos = 0
            for i in order:
                k0, k1 = spans[i]
                if k1 <= k0:
                    continue
                pos = cursor = _copy_range(f, g, cursor, k0, blocksize, st)
                if st["read_error"] is not None:
                    break
                f.seek(k0)
                try:
                    seg = f.read(k1 - k0, dtype="float64", always_2d=True)
                except Exception as e:                # noqa: BLE001 —— libsndfile 形形色色
                    st["read_error"] = e
                    break
                if len(seg) < k1 - k0:                # 声称有、实际读不满 ⇒ 截断
                    st["read_error"] = OSError(
                        "声称 %d 帧，向后读时只拿到 %d 帧" % (k1 - k0, len(seg)))
                    if not len(seg):
                        break
                op = ops[i]
                out, sstat = dehum.dehum(seg, op.f0, fs, op.q, op.harmonics)
                recs[i] = {**sstat, "energy_ratio_measured_db":
                           _round_db(energy_ratio_db(out, seg))}
                applied[i] = int(seg.shape[0])
                _account(out, st)
                g.write(out)
                pos = cursor = k0 + int(out.shape[0])
            if st["read_error"] is None:
                pos = _copy_range(f, g, cursor, claimed, blocksize, st)
        frames = pos

    return _audit(project, dest, "dehum", fs, ch, sub, frames, claimed, st, applied,
                  tf_points=None, op_recs=recs)


def _audit(project: Project, dest: str, domain: str, fs: int, ch: int, sub: str,
           frames: int, claimed: int, st: dict, applied: list,
           tf_points: list | None, measured: list | None = None,
           n_fft: int | None = None, hop: int | None = None,
           op_recs: list | None = None) -> dict:
    """组装**审计记录**（四条路径共用；域特有的字段都不藏）。"""
    ops = []
    for i, op in enumerate(project.ops):
        rec = {**op.to_dict(), "applied_frames": applied[i]}
        if tf_points is not None:
            # 时频路径：`applied_frames` 是"选区覆盖的采样数"，**不等于**被改动的量；
            # 真正被改的是时频点数（帧 × bin），单列出来免得读者把它当成编辑幅度。
            rec["applied_tf_points"] = tf_points[i]
            #: **实测**的带内衰减（在**同一套 STFT 表示**里量的：掩码前/后的带内能量比）。
            #: `None` = 这个操作是 no-op（没跑掩码）或带内本来没能量。
            #: ⚠ 与 `gain_db`（设定值）**分开**报：说"我压了 20 dB"必须有实测值支撑。
            #: ⚠ 多个操作重叠时，这里是**累积作用之后**的结果，不是该操作单独的效果。
            rec["attenuation_measured_db"] = measured[i] if measured is not None else None
        if op_recs is not None and op_recs[i] is not None:
            # 去削波 / 去咔哒：`applied_frames` 是**选区覆盖**的采样数；真正被改的是
            # `replaced_samples` / `changed_samples`。另外带上各自的**实测**（见各自的构造处）。
            rec.update(op_recs[i])
        ops.append(rec)
    audit = {
        "source": os.path.abspath(project.source),
        "dest": os.path.abspath(dest),
        "domain": domain,
        "samplerate": fs,
        "channels": ch,
        "subtype": sub,
        "frames": frames,
        "duration_s": round(frames / fs, 6) if fs else 0.0,
        "peak_out_dbfs": (round(20.0 * math.log10(st["peak"]), 3) if st["peak"] > 0 else None),
        "clipped_frames": st["clipped"],      # 只对整型 subtype 统计；浮点恒为 0
        # 截断源：显式上报（frames_claimed > frames 即"读不满"）。已读部分照常写出。
        "read_incomplete": st["read_error"] is not None,
        "frames_claimed": claimed,
        "ops": ops,
    }
    if domain == "band":
        audit["n_fft"] = n_fft
        audit["hop"] = hop
        audit["window"] = "periodic_hann"
        #: **实测**的恒等往返残差（选区所在时段，逐块取最大）。"频谱编辑不是位精确的"
        #: 这条事实的值就是它；`None` = 全零信号（残差为 0，无 dBFS 可报）。
        audit["roundtrip_floor_dbfs"] = (
            round(20.0 * math.log10(st["floor"]), 3) if st["floor"] > 0 else None)
        #: 上面那个残差是在多少个采样上实测出来的（口径可查，不是拍脑袋的数）
        audit["roundtrip_floor_samples"] = st["floor_samples"]
    if domain == "declip":
        #: 用的是哪套重建（可复算的口径名）：带限投影 + 削波不等式投影，见 `declip.py`
        audit["algorithm"] = "pocs_declip"
    if domain == "declick":
        #: 检测口径（可复算的名字）：两点预测残差 + 全段 MAD 尺子 + 线性插值，见 `declick.py`
        audit["algorithm"] = "residual2_mad_linear"
    if domain == "dehum":
        #: 去嗡口径：零相位陷波级联（Q=10 与谐波集由 `dehum.py` 的漂移表定）
        audit["algorithm"] = "zerophase_notch_cascade"
    return audit


def render_gain(source: str, dest: str, t0: float, t1: float, gain_db: float,
                **kw) -> dict:
    """便捷入口：单个**时间选区**增益（`render()` 的薄封装，参数同它）。"""
    return render(Project(source, [GainOp(t0, t1, gain_db)]), dest, **kw)


def render_band(source: str, dest: str, t0: float, t1: float, f0: float, f1: float,
                gain_db: float, **kw) -> dict:
    """便捷入口：单个**时频选区**增益（`render()` 的薄封装，参数同它）。"""
    return render(Project(source, [BandGainOp(t0, t1, f0, f1, gain_db)]), dest, **kw)


def render_declip(source: str, dest: str, t0: float, t1: float, bandwidth: float,
                  ceiling: float | None = None, **kw) -> dict:
    """便捷入口：单个**去削波**选区（`render()` 的薄封装；`bandwidth`/`ceiling` 见 `DeclipOp`）。"""
    return render(Project(source, [DeclipOp(t0, t1, bandwidth, ceiling)]), dest, **kw)


def render_declick(source: str, dest: str, t0: float, t1: float, threshold: float | None = None,
                   max_run: int | None = DEFAULT_MAX_RUN, **kw) -> dict:
    """便捷入口：单个**去咔哒**选区（`render()` 的薄封装；`threshold`/`max_run` 见 `DeclickOp`）。"""
    th = DEFAULT_THRESHOLD if threshold is None else threshold
    return render(Project(source, [DeclickOp(t0, t1, th, max_run)]), dest, **kw)


def render_dehum(source: str, dest: str, t0: float, t1: float, f0: float,
                 q: float | None = None, harmonics=None, **kw) -> dict:
    """便捷入口：单个**去嗡**选区（`render()` 的薄封装；`f0` 必须给，见 `DehumOp`）。"""
    qq = DEFAULT_Q if q is None else q
    hs = DEFAULT_HARMONICS if harmonics is None else tuple(harmonics)
    return render(Project(source, [DehumOp(t0, t1, f0, qq, hs)]), dest, **kw)
