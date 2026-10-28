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
* **复合操作**（`_render_chained`）：栈里混用不同域时，按 `canonical_order` 的规范顺序
  **逐算子**渲染，每步落一个中间文件（`FLOAT`）。依据是 `packaging/op_order_probe.py`
  实测（SDR 极差 0.44 dB，但咔哒残差差 2.2 倍、嗡压制差 1.75 dB）；被规范化时审计记
  `reordered_from`。
* 都遵守：**削波不静默**、**截断源不许 traceback**（沿用 E2.1 规矩）。
"""
from __future__ import annotations

import math
import os

import numpy as np
import soundfile as sf

from ..context import io_path
from . import declick, declip, dehum, denoise, stft
from .declick import DEFAULT_MAX_RUN, DEFAULT_THRESHOLD
from .dehum import DEFAULT_HARMONICS, DEFAULT_Q
from .denoise import DEFAULT_REDUCTION_DB
from .metrics import band_gain_db, energy_ratio_db
from .ops import BandGainOp, DeclickOp, DeclipOp, DehumOp, DenoiseOp, GainOp, PolyGainOp, Project

#: 一次读入的帧数（与 `analyzers/basic.py` 的读块同量级）。
BLOCKSIZE = 1 << 16

#: 会被 libsndfile 钳位到 [−1, 1] 的整型 subtype（浮点不钳位）。
_INT_SUBTYPES = {"PCM_S8", "PCM_U8", "PCM_16", "PCM_24", "PCM_32"}

#: 整型 subtype 的位深（用于**抖动**的 LSB 计算）。
_PCM_BITS = {"PCM_S8": 8, "PCM_U8": 8, "PCM_16": 16, "PCM_24": 24, "PCM_32": 32}

#: 抖动 RNG 的固定种子：同一输入必须给同一字节（`render` 是纯函数）。
_DITHER_SEED = 0

#: 频域路径的默认 STFT 参数。**这个 `n_fft` 与频谱图显示用的 `nperseg=16384` 不同，
#: 是有意的**（显示要平滑的统计谱，编辑要局部性：4096 @48k ⇒ 时间分辨率 ~85 ms、
#: 频率分辨率 ~11.7 Hz）。`docs/theory.md §12.1` 要求"视图与编辑共用同一套 STFT 参数"
#: ——那是对**在频谱图上直接刷**的 P2 视图的要求。**P2 套索已落地（2026-10-27）**：
#: 套索顶点是连续 (t, f) 数据坐标、与显示网格无关 ⇒ 看/刷口径在多边形语义下天然解耦
#: （显示格比编辑格细是有意的——显示要平滑统计、编辑要局部性；解析注记见
#: `editor_view` 模块 docstring）。矩形/多边形掩膜的**粒度**仍是本文件的编辑网格
#: （4096）；CLI 已暴露 `--n-fft`/`--hop`。
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


def _check_dither(dither: bool, sub: str) -> None:
    """抖动只对**整型**目标有意义；浮点目标上要求抖动 ⇒ 明确拒绝（不静默当没发生）。"""
    if dither and sub not in _INT_SUBTYPES:
        raise ValueError("抖动只对整型 subtype 有效（PCM_16/24/32…），目标 subtype=%s"
                         % sub)


class _DitherWriter:
    """写出口的**TPDF 抖动**：`d[n] = (U1 − U2)·LSB`（三角分布、±1 LSB、无直流）。

    ⚠ 这是**全文件重量化**：连"没被编辑"的采样也会动，`逐位不变`的承诺在开启抖动时
      **不再成立**（CLI/审计必须显式说）。RNG 固定种子 ⇒ 渲染仍是纯函数。
    """

    def __init__(self, f, lsb: float, seed: int = _DITHER_SEED):
        self._f = f
        self._lsb = lsb
        self._rng = np.random.default_rng(seed)

    def write(self, b: np.ndarray) -> None:
        a = np.asarray(b, dtype=np.float64)
        # ⚠ libsndfile 的 float→PCM 是**截断**（实测 0.0001 → 3 LSB，不是 3.28 的四舍五入）
        # ⇒ 加 0.5 LSB 把它变成"以 0 为无偏中心"的量化；否则抖动会留下 −0.5 LSB 直流。
        d = ((self._rng.random(a.shape) - self._rng.random(a.shape)) + 0.5) * self._lsb
        self._f.write(a + d)


def _open_writer(g, sub: str, dither: bool):
    """按 subtype 决定是否给写出口套上抖动；LSB = `1 / 2^(位深−1)`。"""
    if not dither:
        return g
    bits = _PCM_BITS.get(sub)
    if bits is None:
        raise ValueError("抖动只对整型 subtype 有效（目标 subtype=%s）" % sub)
    return _DitherWriter(g, 1.0 / (2 ** (bits - 1)))


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
           chunk_hops: int = CHUNK_HOPS, dither: bool = False) -> dict:
    """把 `project` 渲染到 `dest`，返回**审计记录**（dict）。

    参数
    ----
    subtype    : 目标编码；`None` = 沿用源的 subtype（保证"没动的地方逐位不变"的前提）。
    overwrite  : 是否允许覆盖已存在的目标（源文件永远不许，见 `_check_dest`）。
    n_fft/hop  : **频域路径**的 STFT 参数（`hop` 默认 `n_fft//4`；时间域路径忽略）。
    dither     : **TPDF 抖动**（±1 LSB），只对整型 subtype 有效。⚠ 它是全文件重量化：
                 开启后连"没被编辑"的采样也会动，**逐位不变的承诺不再成立**。
    """
    ops = list(project.ops)
    if not ops or all(isinstance(o, GainOp) for o in ops):
        return _render_time(project, dest, subtype=subtype, blocksize=blocksize,
                            overwrite=overwrite, dither=dither)
    # ⚠ **同域多算子且选区重叠** ⇒ 走链式（v1.1 起）：重叠的"先后语义"现在有定义了——
    # **操作栈的顺序就是语义**（后一个作用在前一个的结果上），与混域链式同一条原则。
    # 不重叠时仍走单趟路径（性能不变）。判据：
    # `test_overlapping_same_domain_ops_chain_in_stack_order`。
    same_domain_single = (
        all(isinstance(o, (BandGainOp, PolyGainOp)) for o in ops)
        or all(isinstance(o, DeclipOp) for o in ops)
        or all(isinstance(o, DeclickOp) for o in ops)
        or all(isinstance(o, DehumOp) for o in ops)
        or all(isinstance(o, DenoiseOp) for o in ops))
    if same_domain_single and len(ops) > 1 and _spans_overlap(ops):
        return _render_chained(project, dest, subtype=subtype, blocksize=blocksize,
                               overwrite=overwrite, n_fft=n_fft, hop=hop,
                               chunk_hops=chunk_hops, dither=dither)
    if all(isinstance(o, (BandGainOp, PolyGainOp)) for o in ops):
        return _render_band(project, dest, subtype=subtype, blocksize=blocksize,
                            overwrite=overwrite, n_fft=n_fft, hop=hop,
                            chunk_hops=chunk_hops, dither=dither)
    if all(isinstance(o, DeclipOp) for o in ops):
        return _render_declip(project, dest, subtype=subtype, blocksize=blocksize,
                              overwrite=overwrite, dither=dither)
    if all(isinstance(o, DeclickOp) for o in ops):
        return _render_declick(project, dest, subtype=subtype, blocksize=blocksize,
                               overwrite=overwrite, dither=dither)
    if all(isinstance(o, DehumOp) for o in ops):
        return _render_dehum(project, dest, subtype=subtype, blocksize=blocksize,
                             overwrite=overwrite, dither=dither)
    if all(isinstance(o, DenoiseOp) for o in ops):
        return _render_denoise(project, dest, subtype=subtype, blocksize=blocksize,
                               overwrite=overwrite, dither=dither)
    return _render_chained(project, dest, subtype=subtype, blocksize=blocksize,
                           overwrite=overwrite, n_fft=n_fft, hop=hop,
                           chunk_hops=chunk_hops, dither=dither)


def _spans_overlap(ops) -> bool:
    """同域多算子的时间选区是否**两两重叠**（按 `[t0, t1)` 排序后扫一遍）。

    `PolyGainOp` 用顶点时间包络；其它用 `t0/t1`。重叠 ⇒ 单趟路径无法表达顺序语义
    ⇒ 调用方转链式渲染。
    """
    spans = []
    for o in ops:
        if isinstance(o, PolyGainOp):
            ts = [t for t, _ in o.points]
            spans.append((min(ts), max(ts)))
        else:
            spans.append((float(o.t0), float(o.t1)))
    spans.sort()
    end = float("-inf")
    for lo, hi in spans:
        if lo < end:
            return True
        end = max(end, hi)
    return False


#: 复合操作的**规范顺序**（相位号，小的先做）。依据是 `packaging/op_order_probe.py`：
#:
#: * 脉冲类（declick）先：去削波的带限投影会把孤立咔哒**抹宽**，之后两点预测残差
#:   就抓不住（实测咔哒残差 0.1538 → 0.3448，差 2.2 倍）；
#: * 去削波次之：它要看到**未被滤波**的饱和形状（带宽投影依赖原始波形）；
#: * 去嗡再次：它是 LTI，会改动**全部**样本，先做会污染所有检测尺子；
#: * 去噪再次：它的噪声谱从**噪声段**学（LTI 类操作也会改整段），先做会污染学谱；
#: * 电平类（band_gain / gain）**最后**：修复阶段应当看到**原始电平**——自动削波电平
#:   （`ceiling=None` 时从数据里读峰值）与检测门限都从数据里量，先改电平会让它们读错。
#: 同相位内保持用户给的相对顺序（`sorted` 是稳定的）。
PHASE = {"declick": 0, "declip": 1, "dehum": 2, "denoise": 3, "band_gain": 4,
         "poly_gain": 4, "gain": 5}


def canonical_order(ops) -> list:
    """按 `PHASE` 排成规范顺序（稳定排序）。未知类型保持原位并在末尾（不会静默丢）。"""
    return sorted(ops, key=lambda o: PHASE.get(_kind_of(o), 99))


def _kind_of(op) -> str:
    """操作对象的 kind（复用 `to_dict`，避免再维护一份 isinstance 表）。"""
    return op.to_dict().get("kind", "")


def _render_chained(project: Project, dest: str, *, subtype: str | None,
                    blocksize: int, overwrite: bool,
                    n_fft: int, hop: int | None, chunk_hops: int,
                    dither: bool) -> dict:
    """**复合操作**：按栈顺序逐算子渲染，每步落一个中间文件（`FLOAT`），最后写目标。

    为什么可以放开"混用"了（`packaging/op_order_probe.py` 实测）：

    * **SDR 极差只有 0.44 dB**（6 种顺序里 12.95–13.39），顺序不是"生死攸关"；
    * 但**咔哒残差差 2.2 倍**（declip 先 ⇒ 0.3448；declick 先 ⇒ 0.1538）、
      嗡压制差 1.75 dB ⇒ 需要一个**规范顺序**（见 `PHASE` / `canonical_order`），不是随便排。
    * 机制：去削波的带限投影会把孤立咔哒**抹宽**，后续残差检测（两点预测）就抓不住；
      所以**脉冲类缺陷先修**，稳态嗡最后修（它会改所有样本，先修会污染检测尺子）。

    ⚠ 中间文件是 `FLOAT`（float32）：对 PCM16/24 与 FLOAT 源**不引入额外量化**
      （整数解码值在 float32 里精确），但**每个阶段的浮点结果会被舍入到 float32**
      （−144 dBFS 量级）。单算子路径仍是全 float64 → 不许把链式当"逐位保真"。
    ⚠ 每个阶段都**重新做**目标/覆盖/子类型检查；临时目录用完即删（异常路径也删）。
    """
    import shutil
    import tempfile

    _check_dest(project.source, dest, overwrite)
    work = tempfile.mkdtemp(prefix="ac_chain_")
    try:
        ordered = canonical_order(project.ops)
        given = list(project.ops)
        cur = project.source
        stages = []
        audits = []
        for i, op in enumerate(ordered):
            stage_dst = os.path.join(work, "stage_%03d.wav" % i)
            last = i == len(ordered) - 1
            a = render(Project(cur, [op]), dest if last else stage_dst,
                       subtype=subtype if last else "FLOAT",
                       blocksize=blocksize, overwrite=overwrite,
                       n_fft=n_fft, hop=hop, chunk_hops=chunk_hops,
                       dither=dither and last)
            audits.append(a)
            stages.append({"index": i, "op": op.to_dict(),
                           "algorithm": a.get("algorithm"),
                           "applied_frames": [r.get("applied_frames") for r in a.get("ops", [])],
                           "peak": a.get("peak"), "read_error": a.get("read_error"),
                           "n_fft": a.get("n_fft"), "hop": a.get("hop"),
                           "roundtrip_floor_dbfs": a.get("roundtrip_floor_dbfs")})
            cur = stage_dst
        final = audits[-1]
        # 顶层审计要能回答"这条链一共做了什么"：ops 拼起来、源写回原始路径、标明链式。
        final["ops"] = [r for a in audits for r in a.get("ops", [])]
        final["stages"] = stages
        final["chained"] = True
        final["source"] = project.source
        if [id(o) for o in ordered] != [id(o) for o in given]:
            # 顺序被规范化了：如实记账（原始顺序与规范顺序都要能查）
            final["reordered_from"] = [o.to_dict() for o in given]
        return final
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_time(project: Project, dest: str, *, subtype: str | None,
                 blocksize: int, overwrite: bool, dither: bool) -> dict:
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
        _check_dither(dither, sub)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            g = _open_writer(g, sub, dither)
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
                  tf_points=None, dither=dither)


def _point_in_poly(px: np.ndarray, py: np.ndarray, poly) -> np.ndarray:
    """**射线法**（ray casting）向量化：点 `(px, py)` 是否在多边形 `poly` 内部。

    `px/py` 任意广播形状；返回同形状 bool。**边界行为是实现定义**（`px < x_edge` 的
    严格不等号给出"左/下边缘在外"的语义）——选区坐标应偏离谱点网格以避开边界
    （见 `PolyGainOp` 的 docstring）。完全确定性、无随机。
    """
    inside = np.zeros(np.broadcast_shapes(np.shape(px), np.shape(py)), dtype=bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if y1 == y2:
            continue                                  # 水平边：对跨线计数无贡献
        crosses = (y1 > py) != (y2 > py)
        x_at = (x2 - x1) * (py - y1) / (y2 - y1) + x1
        inside ^= crosses & (px < x_at)
    return inside


def _op_t0(op) -> float:
    """选区起始时间（矩形用 t0；多边形用顶点最小 t）。"""
    if isinstance(op, PolyGainOp):
        return min(t for t, _ in op.points)
    return op.t0


def _op_t1(op) -> float:
    """选区结束时间（矩形用 t1；多边形用顶点最大 t）。"""
    if isinstance(op, PolyGainOp):
        return max(t for t, _ in op.points)
    return op.t1


def _region_mask(op, bins: np.ndarray, times: np.ndarray | None):
    """算子的时频掩膜。矩形（BandGainOp）：`times is None` 时给 **bin 向**掩膜
    （与历史路径逐位一致）；多边形（PolyGainOp）：`times` 必须给，返回 (bins, frames)。"""
    if isinstance(op, PolyGainOp):
        assert times is not None
        # 多边形顶点是 (t, f) ⇒ 点也必须 (t, f)：px=帧中心时刻、py=bin 中心频率
        return _point_in_poly(times[None, :], bins[:, None], op.points)
    return (bins >= op.f0) & (bins < op.f1)


def _render_band(project: Project, dest: str, *, subtype: str | None, blocksize: int,
                 overwrite: bool, n_fft: int, hop: int | None, chunk_hops: int,
                 dither: bool) -> dict:
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
        _check_dither(dither, sub)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        win = stft.default_window(n_fft)
        bins = stft.bin_freqs(fs, n_fft)
        # 只保留**真会动**的操作（`gain_db == 0` 是 no-op，整条跳过 ⇒ 全轨逐位不变）
        active = [(i, o) for i, o in enumerate(project.ops)
                  if isinstance(o, (BandGainOp, PolyGainOp)) and not o.is_noop]
        # 矩形：bin 向掩膜（历史路径逐位一致）；多边形：None（逐块 2D 计算）
        band = [_region_mask(o, bins, None) if isinstance(o, BandGainOp) else None
                for _, o in active]
        gains = [10.0 ** (o.gain_db / 20.0) for _, o in active]
        # 要过 STFT 的时间跨度 = 所有活跃操作选区的并集，**两侧各外扩一个窗宽**
        # （见文件头那条 ⚠：写出的范围必须盖住掩码帧的"溢出"，否则在选区边界上
        # 硬切会把被压掉的成分切出一个台阶）。
        ks = [max(0, min(_sample_at(_op_t0(o), fs), claimed)) for _, o in active]
        ke = [max(0, min(_sample_at(_op_t1(o), fs), claimed)) for _, o in active]
        # ⚠ 没有一个活跃操作时跨度必须是**空**的：否则 `k0 = k1 = 0` 会让下面"两侧外扩"
        # 扩出 [0, n_fft) 一段，把开头一个窗的采样白白过一遍 STFT（no-op 就不再逐位不变）。
        k0, k1 = (min(ks), max(ke)) if active else (0, 0)
        span_lo = max(0, k0 - n_fft) if active else 0
        span_hi = min(claimed, k1 + n_fft) if active else 0

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            g = _open_writer(g, sub, dither)
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
                    if isinstance(o, PolyGainOp):
                        # 掩膜盖**整块的所有帧**（与矩形路径同语义：上下文帧也参与
                        # OLA，写出的范围已外扩窗宽盖住掩膜帧的"溢出"）。
                        mask = _region_mask(o, bins, times)          # (bins, frames) 2D
                        sel_pts = int(mask.sum())
                        if sel_pts:
                            m = mask[:, :, None]                     # (bins, frames, ch)
                            meas_e[i][0] += float(np.sum((np.abs(S) ** 2) * m))
                            S *= np.where(m, gains[j], 1.0)
                            meas_e[i][1] += float(np.sum((np.abs(S) ** 2) * m))
                            tf_points[i] += sel_pts
                        # applied：多边形的时间覆盖 = 掩膜覆盖到的帧的时间范围
                        cols = np.nonzero(mask.any(axis=0))[0]
                        if cols.size:
                            t_lo = times[cols.min()]
                            t_hi = times[cols.max()]
                            a0 = max(cs, min(_sample_at(t_lo, fs), claimed))
                            a1 = min(ce, max(_sample_at(t_hi, fs), 0))
                            applied[i] += max(0, a1 - a0)
                        continue
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
                  tf_points=tf_points, measured=measured, n_fft=n_fft, hop=hop,
                  dither=dither)


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
                   blocksize: int, overwrite: bool, dither: bool) -> dict:
    """去削波路径：**逐选区整段载入**，POCS 重建，只改饱和样本。

    ⚠ **不做分块**（与时间域/频域路径的差别，必须让用户看见）：POCS 的核心是
    "带限"这条**全局**约束（对整个选区做一次 FFT），分块会把约束切断、接缝处必然出现
    台阶。所以本路径把**选区**整段载入内存：内存 ≈ `选区采样 × 声道 × 8 B × ~4`。
    选区之外**逐位不变**（原样拷贝）；选区之内**非饱和样本逐位不变**。
    ⚠ 多个去削波的选区**重叠时会走链式渲染**（`render()` 分派，v1.1 起：栈顺序即语义）。
    本函数只处理**不重叠**的多选区（单趟、性能不变）。
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
        _check_dither(dither, sub)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        spans = [(max(0, min(_sample_at(o.t0, fs), claimed)),
                  max(0, min(_sample_at(o.t1, fs), claimed))) for o in ops]
        order = sorted(range(len(ops)), key=lambda i: spans[i][0])
        end = 0
        for i in order:
            if spans[i][0] < end:
                raise AssertionError("重叠应由 render() 分派到链式路径——这是内部不变量")
            end = spans[i][1]

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            g = _open_writer(g, sub, dither)
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
                  tf_points=None, op_recs=recs, dither=dither)


def _render_declick(project: Project, dest: str, *, subtype: str | None,
                    blocksize: int, overwrite: bool, dither: bool) -> dict:
    """去咔哒路径：**逐选区整段载入**（探测器的 MAD 尺子是**全段**统计量 ⇒ **不分块**），

    只改**检出样本**：选区之外**逐位不变**，选区之内**未被检出的样本逐位不变**。
    ⚠ 多个去咔哒的选区**重叠时走链式渲染**（栈顺序即语义）；本函数只处理不重叠的多选区。
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
        _check_dither(dither, sub)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        spans = [(max(0, min(_sample_at(o.t0, fs), claimed)),
                  max(0, min(_sample_at(o.t1, fs), claimed))) for o in ops]
        order = sorted(range(len(ops)), key=lambda i: spans[i][0])
        end = 0
        for i in order:
            if spans[i][0] < end:
                raise AssertionError("重叠应由 render() 分派到链式路径——这是内部不变量")
            end = spans[i][1]

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            g = _open_writer(g, sub, dither)
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
                out, sstat = declick.declick(seg, op.threshold, op.max_run,
                                             step_repair=op.step_repair)
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
                  tf_points=None, op_recs=recs, dither=dither)


def _render_dehum(project: Project, dest: str, *, subtype: str | None,
                  blocksize: int, overwrite: bool, dither: bool) -> dict:
    """去嗡路径：**逐选区整段载入**（零相位 `filtfilt` 要看整段），零相位陷波级联。

    ⚠ 与去削波/去咔哒的差别：陷波是 **LTI** ⇒ **整段选区都被改动**（不是"只改检出样本"）。
    选区之外仍然**逐位不变**（原样拷贝）；选区之内被改的采样数如实记 `changed_samples`。
    ⚠ 多个去嗡的选区**重叠时走链式渲染**（栈顺序即语义，前一个的输出是后一个的输入）；
    本函数只处理不重叠的多选区。
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
        _check_dither(dither, sub)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        spans = [(max(0, min(_sample_at(o.t0, fs), claimed)),
                  max(0, min(_sample_at(o.t1, fs), claimed))) for o in ops]
        order = sorted(range(len(ops)), key=lambda i: spans[i][0])
        end = 0
        for i in order:
            if spans[i][0] < end:
                raise AssertionError("重叠应由 render() 分派到链式路径——这是内部不变量")
            end = spans[i][1]

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            g = _open_writer(g, sub, dither)
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
                  tf_points=None, op_recs=recs, dither=dither)


def _render_denoise(project: Project, dest: str, *, subtype: str | None,
                    blocksize: int, overwrite: bool, dither: bool) -> dict:
    """去噪路径：**逐选区整段载入**，噪声谱从每个算子的 `noise_t0/noise_t1` 学。

    ⚠ 与去嗡同型：增益逐帧逐 bin ⇒ **整段选区都被改动**；选区之外**逐位不变**。
    ⚠ 噪声段先校验（非空、至少一个窗长）再动目标文件；噪声段**可以落在选区之外**，
      那段不会被写（仍逐位不变），只是"学谱"的输入。
    ⚠ 多个去噪的选区**不许重叠**（同一段被滤两遍的语义未立判据）。
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
        _check_dither(dither, sub)
        st["clips_int"] = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        spans = [(max(0, min(_sample_at(o.t0, fs), claimed)),
                  max(0, min(_sample_at(o.t1, fs), claimed))) for o in ops]
        order = sorted(range(len(ops)), key=lambda i: spans[i][0])
        end = 0
        for i in order:
            if spans[i][0] < end:
                raise AssertionError("重叠应由 render() 分派到链式路径——这是内部不变量")
            end = spans[i][1]
        nspans = [(max(0, min(_sample_at(o.noise_t0, fs), claimed)),
                   max(0, min(_sample_at(o.noise_t1, fs), claimed))) for o in ops]
        for i, (nk0, nk1) in enumerate(nspans):
            need = int(ops[i].n_fft)
            if nk1 - nk0 < need:
                raise ValueError(
                    "去噪的噪声段太短：需要至少一个窗长 %d 采样，得到 %d"
                    "（检查噪声段 %.3f–%.3f s 是否落在文件内）"
                    % (need, nk1 - nk0, ops[i].noise_t0, ops[i].noise_t1))

        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            g = _open_writer(g, sub, dither)
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
                    nk0, nk1 = nspans[i]
                    f.seek(nk0)
                    noise = f.read(nk1 - nk0, dtype="float64", always_2d=True)
                except Exception as e:            # noqa: BLE001 —— libsndfile 形形色色
                    st["read_error"] = e
                    break
                if len(seg) < k1 - k0 or len(noise) < nk1 - nk0:   # 声称有、实际读不满
                    st["read_error"] = OSError(
                        "声称 %d 帧，向后读时只拿到 %d 帧（段）／噪声 %d 帧"
                        % (k1 - k0, len(seg), len(noise)))
                    break
                op = ops[i]
                out, sstat = denoise.denoise(seg, noise, reduction_db=op.reduction_db,
                                             n_fft=op.n_fft, hop=op.hop,
                                             track_floor=op.track_floor)
                recs[i] = {**sstat, "noise_t0": op.noise_t0, "noise_t1": op.noise_t1,
                           "noise_samples": int(noise.shape[0])}
                applied[i] = int(seg.shape[0])
                _account(out, st)
                g.write(out)
                pos = cursor = k0 + int(out.shape[0])
            if st["read_error"] is None:
                pos = _copy_range(f, g, cursor, claimed, blocksize, st)
        frames = pos

    return _audit(project, dest, "denoise", fs, ch, sub, frames, claimed, st, applied,
                  tf_points=None, op_recs=recs, dither=dither)


#: **证据等级**（借鉴 queyi_core 的分级思想，按音频修复语义重定义）：
#: 每个审计数值都标注"这条数字有多可信"——读者不必猜量具的可靠性。
#: * `L1_measured`  —— 本次渲染**实测**出来的（能量比、压制量、饱和数……）
#: * `L2_derived`   —— 由实测值**确定性推导**出来的（占比 = 检出/总数、时长 = 帧/fs）
#: * `L3_setting`   —— **调用方设定**的参数（阈值、增益、Q）——不是测量结果
#: * `L4_protocol`  —— **协议字段**（采样率/编码/帧数/哈希）——容器事实而非算法输出
#: * `L5_unknown`   —— 量具不可用（`None`）——**明确弃权**，不是"零"
#: 字段→等级的表（没列到的按 `L4_protocol` 处理：容器/记账事实）。
_EVIDENCE_LEVELS: dict[str, str] = {
    # L1 实测
    "energy_ratio_measured_db": "L1_measured",
    "noise_reduction_measured_db": "L1_measured",
    "out_of_band_measured_db": "L1_measured",
    "attenuation_measured_db": "L1_measured",
    "suppression_db": "L1_measured",
    "detected_samples": "L1_measured",
    "replaced_samples": "L1_measured",
    "changed_samples": "L1_measured",
    "saturated_samples": "L1_measured",
    "rejected_runs": "L1_measured",
    "step_samples": "L1_measured",
    "step_runs": "L1_measured",
    "floor_track_db": "L1_measured",
    "residual_scale": "L1_measured",
    "peak": "L1_measured",
    "peak_out_dbfs": "L1_measured",
    "roundtrip_floor_dbfs": "L1_measured",
    "clipped_frames": "L1_measured",
    "applied_tf_points": "L1_measured",
    # L2 推导
    "fraction": "L2_derived",
    "duration_s": "L2_derived",
    "applied_frames": "L2_derived",
    "runs": "L2_derived",
    "longest_run": "L2_derived",
    "noise_samples": "L2_derived",
    # L3 设定
    "threshold": "L3_setting",
    "max_run": "L3_setting",
    "gain_db": "L3_setting",
    "ceiling": "L3_setting",
    "bandwidth": "L3_setting",
    "iterations": "L3_setting",
    "f0": "L3_setting",
    "q": "L3_setting",
    "reduction_db": "L3_setting",
    "n_fft": "L3_setting",
    "hop": "L3_setting",
    "step_repair": "L3_setting",
    "track_floor": "L3_setting",
    "dither": "L3_setting",
    "dither_lsb": "L3_setting",
    # L5 弃权
    "ambiguity_margin": "L5_unknown",
}


def evidence_level(field: str) -> str:
    """字段的**证据等级**（见 `_EVIDENCE_LEVELS`）。未列字段视为容器/记账事实。"""
    return _EVIDENCE_LEVELS.get(field, "L4_protocol")


def canonical_json(obj) -> str:
    """**确定性序列化**（借鉴 queyi_core 的信任根做法）：sort_keys + 紧凑分隔 +
    非 ASCII 直出 ⇒ 同一对象在任何机器/时区/Python 小版本上产出**同字节**字符串。

    为什么审计需要它：`json.dump(indent=2)` 的输出随键序/空格/编码策略漂移，
    两份内容相同的审计记录会得到不同字节 ⇒ 哈希不可作为"同一份记录"的证据。
    """
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def audit_digest(audit: dict) -> str:
    """审计记录的 **SHA256 摘要**（确定性序列化后）——"同一份修复"的可核对锚。

    ⚠ 摘要**不含**三类随落盘漂移的字段（实测抓到：不剔除会让同一修复的两份记录摘要不同）：
    * `timestamp_utc` —— 墙钟；
    * `dest` / `dest_sha256` —— 目标路径与**产物字节哈希**（FLOAT WAV 的 PEAK 块带
      创建时间戳 ⇒ 跨秒渲染的 dest_sha256 必然不同，这不是修复内容的一部分）；
    * `source` —— 按给定路径原样存，同一文件的两个挂载点应同摘要。
    其余字段全部参与（含 `source_sha256` —— **内容**哈希才是"同一份输入"的证据）。
    判据：`test_audit_digest_is_stable_and_path_independent`。
    """
    import hashlib
    skip = ("timestamp_utc", "dest", "dest_sha256", "source")
    d = {k: v for k, v in audit.items() if k not in skip}
    return hashlib.sha256(canonical_json(d).encode("utf-8")).hexdigest()


def kernel_self_digest() -> str:
    """**内核自证**（借鉴 queyi_core）：本模块文件自身的 SHA256。

    用途：两个审计记录里若 `kernel_digest` 相同，就证明**同一份渲染实现**产出了它们
    ——"可复算"不止是"同参数同结果"，还包括"实现没被偷偷改过"（法证口径）。
    """
    import hashlib
    try:
        with open(os.path.abspath(__file__), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def _tool_version() -> str:
    """audiocheck 包版本（导入失败时返回 "unknown"——审计不许因元数据缺失而崩）。"""
    try:
        from importlib import metadata
        return metadata.version("audiocheck")
    except Exception:                                        # noqa: BLE001
        return "unknown"


#: WAV 里随创建时刻漂移的 chunk（内容哈希必须跳过它们，否则"同一份音频"跨秒不同哈希）：
#: `PEAK`（峰值缓存，含 `time_t` 创建时间戳）。其余 chunk（`fmt ` / `data` / `fact` / …）
#: 全部参与内容哈希。
_VOLATILE_WAV_CHUNKS = (b"PEAK",)


def _sha256_file(path: str, *, content_only: bool = False) -> str:
    """文件 SHA256。`content_only=True` 时**跳过随时刻漂移的元数据 chunk**。

    为什么需要两档（2026-10-27 实测抓到）：FLOAT WAV 的 **PEAK chunk 带创建时间戳**
    ——同一份音频在不同秒写出，原始字节哈希必然不同。于是"同一修复跨机核对"会假失败。
    * `content_only=True`  ⇒ 审计的 `source_sha256` 用这档（"是不是同一份**内容**"）；
    * `content_only=False` ⇒ `dest_sha256` 用这档（"产物字节"本身就是可核对的证据）。
    """
    import hashlib
    if content_only and path.lower().endswith((".wav", ".wave")):
        return _wav_content_sha256(path)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 23), b""):
            h.update(chunk)
    return h.hexdigest()


def _wav_content_sha256(path: str) -> str:
    """WAV **内容**哈希：按 RIFF chunk 遍历，跳过 `_VOLATILE_WAV_CHUNKS` 与填充字节。

    chunk 走查容错（非标准容器 ⇒ 退化为整文件哈希，不抛错——审计不该因容器怪而崩）。
    """
    import hashlib

    from ..context import io_path
    try:
        with open(io_path(path), "rb") as fh:
            blob = fh.read()
        if len(blob) < 12 or blob[:4] not in (b"RIFF", b"RIFX", b"RF64"):
            return hashlib.sha256(blob).hexdigest()
        h = hashlib.sha256()
        # ⚠ **只哈希魔数 + 格式标识（8 字节），不能哈希 `blob[:12]`**：
        # 第 4–8 字节是 **RIFF 总大小字段**，它随容器里**任何** chunk 的长度变化
        # （多一个 LIST/INFO/fact/PEAK 都会变）。把它算进"内容哈希"意味着
        # **同一份音频内容、只因容器多带元数据就被判成不同内容**——
        # 直接破坏"跨机核对同一份修复"这个核心承诺。
        # 实测（`tests/test_edit_render_gaps.py`）：同样 fmt+data，中间插一个
        # 16 字节 PEAK ⇒ 总大小 46→70 ⇒ 旧实现两个哈希不同。
        h.update(blob[:4])                       # "RIFF" / "RIFX" / "RF64"
        h.update(blob[8:12])                     # "WAVE"
        # `RIFX` 是**大端** RIFF（罕见但合法：广播/老 Mac 素材）；
        # `RIFF` / `RF64` 是小端。chunk 的 size 字段必须按对应字节序读——
        # 早先无条件 `"little"` ⇒ RIFX 容器的 size 会被读成天文数字、
        # `min(..., len(blob))` 把整个文件当一个大 chunk ⇒ **哈希失去区分力**
        # （不同内容可能撞成同一个值）。RIFX 的实际字节就是大端。
        order = "big" if blob[:4] == b"RIFX" else "little"
        pos = 12
        while pos + 8 <= len(blob):
            cid = blob[pos:pos + 4]
            size = int.from_bytes(blob[pos + 4:pos + 8], order)
            body_end = min(pos + 8 + size, len(blob))
            if cid not in _VOLATILE_WAV_CHUNKS:
                h.update(blob[pos:body_end])
            pos = body_end + (size & 1)          # RIFF 的偶数字节对齐填充
        return h.hexdigest()
    except OSError:
        return ""


def _audit(project: Project, dest: str, domain: str, fs: int, ch: int, sub: str,
           frames: int, claimed: int, st: dict, applied: list,
           tf_points: list | None, measured: list | None = None,
           n_fft: int | None = None, hop: int | None = None,
           op_recs: list | None = None, dither: bool = False) -> dict:
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
        #: **抖动**（TPDF ±1 LSB）：开启时整条目标都被重量化（未编辑处也不再逐位不变）。
        "dither": bool(dither),
        "dither_lsb": (1.0 / (2 ** (_PCM_BITS[sub] - 1)) if dither and sub in _PCM_BITS
                       else None),
        "ops": ops,
        # 证据链（SWGDE 口径：源/产物哈希 + 工具版本；时间戳由 CLI 层补，render 保持纯函数）
        "source_sha256": _sha256_file(os.path.abspath(project.source), content_only=True),
        "dest_sha256": _sha256_file(io_path(dest)),
        "tool_version": _tool_version(),
        # 实现自证：同一份渲染代码 ⇒ 同 digest（"可复算"不止同参数同结果）
        "kernel_digest": kernel_self_digest(),
    }
    # **证据等级表**：本次审计里出现的每个数值字段 → 可信度等级（读者不必猜量具）
    fields = set()
    for rec in audit["ops"]:
        fields |= set(rec)
    fields |= set(audit)
    audit["evidence_levels"] = {f: evidence_level(f) for f in sorted(fields)
                                if f not in ("ops", "stages", "reordered_from",
                                             "evidence_levels", "audit_digest")}
    # 记录自身的确定性摘要（不含 dest/timestamp）——两份相同修复的跨机核对锚
    audit["audit_digest"] = audit_digest(audit)
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
        #: 检测口径（可复算的名字）：两点残差双尺度 MAD + **级联台阶臂**（长窗中值）
        #: 见 `declick.py`；台阶臂关闭时回到 v2 口径名（语义变了名字必须变）。
        any_step = any(getattr(o, "step_repair", False) for o in project.ops
                       if isinstance(o, DeclickOp))
        audit["algorithm"] = ("residual2_mad2scale_step_cascade"
                              if any_step else "residual2_mad2scale_linear")
    if domain == "dehum":
        #: 去嗡口径：零相位陷波级联（Q=10 与谐波集由 `dehum.py` 的漂移表定）
        audit["algorithm"] = "zerophase_notch_cascade"
    if domain == "denoise":
        #: 去噪口径：判决引导先验 SNR 的 Wiener + 从噪声段学谱，见 `denoise.py`；
        #: 任一 denoise 算子开了底噪跟踪 ⇒ 口径名随之（语义变了名字必须变）。
        tracked = any(getattr(o, "track_floor", False) for o in project.ops
                      if isinstance(o, DenoiseOp))
        audit["algorithm"] = "wiener_dd_floor_tracked" if tracked else "wiener_dd_profile"
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


def render_denoise(source: str, dest: str, t0: float, t1: float,
                   noise_t0: float, noise_t1: float,
                   reduction_db: float | None = None, **kw) -> dict:
    """便捷入口：单个**去噪**选区（`render()` 的薄封装；噪声段必须给，见 `DenoiseOp`）。"""
    rd = DEFAULT_REDUCTION_DB if reduction_db is None else reduction_db
    return render(Project(source, [DenoiseOp(t0, t1, noise_t0, noise_t1, rd)]), dest, **kw)
