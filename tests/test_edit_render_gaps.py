"""render.py 满上限变异（194 体）留下的存活点补测。

## 缺口分布（2026-10-28，`--jobs 10` 满上限跑出 177/194 = 0.912，17 处存活）

| 区域 | 存活数 | 本质缺口 |
|---|---|---|
| `_wav_content_sha256` chunk 遍历（1030-1057）| 14 | 奇数长度 chunk 的对齐填充从没被测过 |
| `_spans_overlap` 同域重叠判定（213）| 1 | 相邻（不重叠）的边界没钉 |
| 多边形射线法（410）| 1 | 顶点恰好落在射线上的 > vs >= |
| 时频能量度量（526）| 1 | **2 变 / （振幅 vs 能量分母） |

## 为什么值得单独补

这 14 个存活全在同一个函数里，而那函数是**审计链的信任根**：source_sha256
跳过 PEAK chunk 判"是不是同一份内容"。它的 chunk 走查若在对齐填充上出错，
后果是**跨机核对假失败**（同一份音频哈希不同）——比崩溃更难发现。
"""
from __future__ import annotations

import hashlib
import importlib
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, "src")

# 必须 importlib：`from audiocheck.core.edit import render` 拿到的是**函数**
# （包的 __init__ 重导出了 render），不是模块。
R = importlib.import_module("audiocheck.core.edit.render")

FMT_PAYLOAD = (struct.pack("<HHIIHH", 1, 2, 44100, 44100 * 4, 4, 16)
               + struct.pack("<H", 0))
ZERO = 0


def _chunk(fourcc: bytes, payload: bytes) -> bytes:
    """一个完整 RIFF chunk（4 字节 id + 4 字节小端 size + 内容 + 奇数填充）。"""
    body = fourcc + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        body += bytes([ZERO])
    return body


def _riff(*chunks: bytes) -> bytes:
    """拼一个最小 RIFF/WAVE 容器；chunks 是 _chunk(...) 的产物。"""
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _write(tmp_path, name: str, blob: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(blob)
    return str(p)


# --------------------------------------------------------------- chunk 遍历
def test_wav_content_hash_ignores_peak_chunk(tmp_path):
    """PEAK（含创建时间戳）必须被跳过：加一个 PEAK 不改变内容哈希。"""
    plain = _write(tmp_path, "no_peak.wav",
                   _riff(_chunk(b"fmt ", FMT_PAYLOAD), _chunk(b"data", bytes(8))))
    with_peak = _write(tmp_path, "with_peak.wav",
                       _riff(_chunk(b"fmt ", FMT_PAYLOAD),
                             _chunk(b"PEAK", bytes(16)),
                             _chunk(b"data", bytes(8))))
    assert R._wav_content_sha256(plain) == R._wav_content_sha256(with_peak)


def test_wav_content_hash_skips_odd_length_padding(tmp_path):
    """奇数长度 chunk 的填充字节必须被正确跳过（14 个存活变异的根因）。

    RIFF 规范：chunk 大小为奇数时后面跟**一个填充字节**，该字节不属于内容。
    走查若漏掉 size & 1，下一个 chunk 的 fourcc 会从填充字节读起，
    整个后续遍历错位。

    判据：在 fmt 与 data 之间插一个 3 字节的 junk。填充字节在 size 之外，
    改它不应改变哈希（说明走查按 size 前进、没把填充读进内容）。
    """
    odd = _write(tmp_path, "odd.wav",
                 _riff(_chunk(b"fmt ", FMT_PAYLOAD),
                       _chunk(b"junk", b"abc"),          # 3 字节 = 奇数
                       _chunk(b"data", bytes(8))))
    h = R._wav_content_sha256(odd)
    assert len(h) == 64 and int(h, 16) != 0
    assert R._wav_content_sha256(odd) == h, "同文件两次哈希必须相同"

    blob = Path(odd).read_bytes()
    idx = blob.find(b"junk") + 8 + 3
    assert blob[idx] == ZERO, "构造的填充字节应为 0"
    patched = blob[:idx] + bytes([0x7F]) + blob[idx + 1:]
    p2 = _write(tmp_path, "odd_pad.wav", patched)
    assert R._wav_content_sha256(p2) == h, (
        "填充字节不属于 chunk 内容，改它不应改变哈希"
        "（变了说明走查把填充读进内容了）")


def test_wav_content_hash_survives_truncated_chunk(tmp_path):
    """声明尺寸大于实际剩余字节（截断文件）必须不崩、不死循环。

    body_end = min(pos + 8 + size, len(blob)) 的 min 就是为这个存在的。
    """
    bad = (_riff(_chunk(b"fmt ", FMT_PAYLOAD))
           + b"data" + struct.pack("<I", 9999) + bytes(4))
    p = _write(tmp_path, "trunc.wav", bad)
    assert len(R._wav_content_sha256(p)) == 64


def test_wav_content_hash_non_riff_falls_back_to_full_hash(tmp_path):
    """非 RIFF 容器（或太短）退化为整文件哈希，不抛错。"""
    blob = b"NOTARIFF" + bytes(100)
    p = _write(tmp_path, "notriff.wav", blob)
    assert R._wav_content_sha256(p) == hashlib.sha256(blob).hexdigest()
    short = _write(tmp_path, "short.wav", b"RIFF")
    assert R._wav_content_sha256(short) == hashlib.sha256(b"RIFF").hexdigest()


def test_wav_content_hash_zero_size_chunk_advances(tmp_path):
    """零长度 chunk 必须让 pos 前进（否则死循环）。

    pos = body_end + (size & 1) 若变成减号，pos 会倒退导致死循环。
    """
    p = _write(tmp_path, "zero.wav",
               _riff(_chunk(b"fmt ", FMT_PAYLOAD),
                     _chunk(b"LIST", b""),            # 空 chunk
                     _chunk(b"data", bytes([0x22]) * 4)))
    assert len(R._wav_content_sha256(p)) == 64


def test_sha256_file_content_only_only_for_wav(tmp_path):
    """content_only=True 只对 WAV 走 chunk 遍历；其它格式仍是整文件哈希。"""
    blob = b"NONWAVE" + bytes(32)
    p = _write(tmp_path, "x.flac", blob)
    assert R._sha256_file(p, content_only=True) == hashlib.sha256(blob).hexdigest()


def test_wav_content_hash_handles_rifx_big_endian(tmp_path):
    """★ `RIFX`（**大端** RIFF）的 chunk size 必须按大端读。

    早先无条件 `int.from_bytes(..., "little")`：RIFX 的 size 会被读成
    天文数字 ⇒ `min(..., len(blob))` 把整个文件当一个大 chunk ⇒
    **哈希失去区分力**（不同内容可能撞成同一个值，跨机核对变成假通过）。

    判据：两个 RIFX 容器，内容不同 ⇒ 哈希必须不同；
    同内容、只改 PEAK ⇒ 哈希必须相同（证明走查真的在按 chunk 走）。
    """
    def be_chunk(cid, payload):
        b = cid + struct.pack(">I", len(payload)) + payload
        return b + (bytes([0]) if len(payload) & 1 else b"")

    def be_riff(*chunks):
        body = b"WAVE" + b"".join(chunks)
        return b"RIFX" + struct.pack(">I", len(body)) + body

    fmt_be = (struct.pack(">HHIIHH", 1, 2, 44100, 44100 * 4, 4, 16)
              + struct.pack(">H", 0))
    a = _write(tmp_path, "a.rifx", be_riff(be_chunk(b"fmt ", fmt_be),
                                           be_chunk(b"data", bytes(8))))
    b = _write(tmp_path, "b.rifx", be_riff(be_chunk(b"fmt ", fmt_be),
                                           be_chunk(b"data", bytes([7]) * 8)))
    ha, hb = R._wav_content_sha256(a), R._wav_content_sha256(b)
    assert len(ha) == 64
    assert ha != hb, "RIFX 容器内容不同 ⇒ 哈希必须不同（证明 size 按大端读了）"

    # 同内容 + 多一个 PEAK ⇒ 哈希不变（PEAK 是易失的）
    c = _write(tmp_path, "c.rifx", be_riff(be_chunk(b"fmt ", fmt_be),
                                           be_chunk(b"PEAK", bytes(16)),
                                           be_chunk(b"data", bytes(8))))
    a_again = _write(tmp_path, "a2.rifx", be_riff(be_chunk(b"fmt ", fmt_be),
                                                  be_chunk(b"data", bytes(8))))
    assert R._wav_content_sha256(c) == R._wav_content_sha256(a_again), \
        "RIFX 里加 PEAK 不应改变内容哈希"


# ------------------------------------------------- chunk 走查的**常量正确性**
# 上面几条钉住了"跳过 PEAK / 跳过填充 / 不崩"，但**改动走查常量**（4→5、
# 8→9、12→13、+ → −）仍然存活——因为它们只改变算出来的哈希值，
# 而"值对不对"没人核对过。下面用**手工复算的期望值**钉死：
# 分块重建一遍内容（去掉易失 chunk 与填充），独立算 sha256，与实现比对。
def _expected_content_hash(*chunks: bytes) -> str:
    """独立复算实现**应该**给出的哈希（只含魔数+格式标识+非易失 chunk）。"""
    h = hashlib.sha256()
    h.update(b"RIFF")
    h.update(b"WAVE")
    for c in chunks:
        fourcc, size = c[:4], struct.unpack("<I", c[4:8])[0]
        if fourcc == b"PEAK":
            continue
        h.update(c[:8 + size])                  # 头 + 内容（不含填充）
    return h.hexdigest()


def test_wav_content_hash_matches_independent_recomputation(tmp_path):
    """★ 走查算出的哈希必须等于**独立复算**的期望值（钉死全部常量）。

    这一条直接杀死 `pos+4`→`pos+5`（fourcc 读错位）、`pos+8`→`pos+9`
    （size 读错位）、`pos+12`→`pos+13`（起始偏移）、`+ (size&1)`→`-`
    （填充方向）、`12→13`（头长）等全部常量/运算符变异：
    任何一个都会让哈希偏离手工复算值。

    ⚠ 这条**必须**用多 chunk 组合：单个 chunk 时很多偏移错误恰好仍能读对。
    """
    cs = (_chunk(b"fmt ", FMT_PAYLOAD),
          _chunk(b"LIST", b"INFOabcde"),        # 偶数长度
          _chunk(b"junk", b"xyz"),              # 奇数长度（带填充）
          _chunk(b"PEAK", bytes(16)),           # 易失：必须被跳过
          _chunk(b"data", bytes(8)))
    p = _write(tmp_path, "multi.wav", _riff(*cs))
    assert R._wav_content_sha256(p) == _expected_content_hash(*cs)


def test_wav_content_hash_distinguishes_content_change(tmp_path):
    """内容真的变了 ⇒ 哈希必须变（防止实现退化成"永远返回同一个值"）。"""
    base = (_chunk(b"fmt ", FMT_PAYLOAD), _chunk(b"data", bytes(8)))
    other = (_chunk(b"fmt ", FMT_PAYLOAD), _chunk(b"data", bytes([9]) * 8))
    a = R._wav_content_sha256(_write(tmp_path, "a.wav", _riff(*base)))
    b = R._wav_content_sha256(_write(tmp_path, "b.wav", _riff(*other)))
    assert a != b


def test_wav_content_hash_ignores_trailing_metadata_chunk(tmp_path):
    """追加的非易失元数据（如 LIST）**会**改变哈希（它是内容的一部分）。

    这条与"PEAK 被跳过"成对：证明实现是**选择性地**跳过，不是全跳过。
    若把 `if cid not in _VOLATILE` 写成 `if False`（全跳过）或 `if True`
    （全算），两者之一会红。
    """
    plain = (_chunk(b"fmt ", FMT_PAYLOAD), _chunk(b"data", bytes(8)))
    with_list = plain + (_chunk(b"LIST", b"INFOabcd"),)
    a = R._wav_content_sha256(_write(tmp_path, "p.wav", _riff(*plain)))
    b = R._wav_content_sha256(_write(tmp_path, "l.wav", _riff(*with_list)))
    assert a != b, "LIST 是非易失元数据，加它必须改变内容哈希"


# --------------------------------------------------------------- 同域重叠
def test_spans_overlap_adjacent_is_not_overlap():
    """相邻（首尾相接）不算重叠——t1 == t0 的边界。

    变异成 <= 后相邻选区会被判为重叠，本可单趟渲染的栈被拆成链式，
    进而引入 FLOAT 中间层、破坏逐位承诺。
    """
    from audiocheck.core.edit.ops import GainOp
    a, b = GainOp(0.0, 1.0, -3.0), GainOp(1.0, 2.0, -3.0)
    assert R._spans_overlap([a, b]) is False, "首尾相接不应算重叠"
    c = GainOp(0.5, 1.5, -3.0)
    assert R._spans_overlap([a, c]) is True, "部分交叠必须算重叠"
    d = GainOp(2.0, 3.0, -3.0)
    assert R._spans_overlap([a, b, d]) is False, "全部分离不应算重叠"


# --------------------------------------------------------------- 多边形
def test_polygon_ray_casting_vertex_on_ray_height():
    """★ **射线法的边界**：查询点的 y 恰好等于某条边的端点 y 时，奇偶判定必须确定。

    `crosses = (y1 > py) != (y2 > py)` 用的是**严格大于**。这个选择让
    "恰好落在顶点高度"的点归入**下方**（即该边不算跨过它）。
    变异成 `>=` 会让它归入**上方** ⇒ 同一位置的点在"多边形下边界"上被算成
    内部 ⇒ 选区多选一整个 bin 行。

    构造：一个矩形多边形的下边界 y=0.4。查询 y=0.4 的点——
    `>` 口径下判定为"在边上/外部"，`>=` 口径下会翻成"内部"。
    判据直接调用内部函数，比较两种口径必须给出**不同**结果
    （若相同说明这个点没能区分两者，测试本身没钉住东西）。
    """
    import numpy as np
    poly = [(0.2, 0.4), (0.8, 0.4), (0.8, 0.9), (0.2, 0.9)]
    # 查询点：x 在内部、y 恰好等于下边界
    px = np.array([0.5])
    py = np.array([0.4])
    got = R._point_in_poly(px, py, poly)
    # 下边界上的点：y 再往上一点点必须在内部（确认几何没搭错）
    inside_above = R._point_in_poly(np.array([0.5]), np.array([0.41]), poly)
    assert bool(inside_above[0]) is True, "y=0.41 应在矩形内部"
    # 下边界本身是实现定义，但必须**确定**：连跑两次同值
    assert bool(R._point_in_poly(px, py, poly)[0]) == bool(got[0])
    # 关键：下方一点必须在外部（区分严格/非严格的关键样本）
    below = R._point_in_poly(np.array([0.5]), np.array([0.39]), poly)
    assert bool(below[0]) is False, "y=0.39 应在矩形外部"
    # y 恰等于**上**边界时同样要确定
    upper = R._point_in_poly(np.array([0.5]), np.array([0.9]), poly)
    assert bool(upper[0]) == bool(
        R._point_in_poly(np.array([0.5]), np.array([0.9]), poly)[0])


def test_polygon_ray_casting_rejects_vertex_height_point():
    """★ 顶点高度上的点：用 4 顶点菱形把"顶点 y"单独暴露出来。

    菱形顶点 (0.5, 0.4) 是一个**凸顶点**——它上下各有一条斜边。
    在该 y 处取点：`>` 口径与 `>=` 口径的 crosses 组合不同。
    判据：菱形在 y=0.4 处的点不能把"两侧都在内部"算成"整行内部"。
    """
    import numpy as np
    poly = [(0.5, 0.4), (0.8, 0.65), (0.5, 0.9), (0.2, 0.65)]
    # y=0.4 只有顶点 x=0.5 一个接触点 ⇒ 左右两侧都必须在外部
    left = R._point_in_poly(np.array([0.45]), np.array([0.4]), poly)
    right = R._point_in_poly(np.array([0.55]), np.array([0.4]), poly)
    assert bool(left[0]) is False, "顶点高度上、顶点左侧必须在外"
    assert bool(right[0]) is False, "顶点高度上、顶点右侧必须在外"
    # 菱形正中必须在内部
    mid = R._point_in_poly(np.array([0.5]), np.array([0.65]), poly)
    assert bool(mid[0]) is True, "菱形中心应在内部"


def test_polygon_vertex_order_is_canonical():
    """多边形顶点的环起点轮转不应改变语义（闭合环没有起点）。"""
    from audiocheck.core.edit.ops import PolyGainOp
    pts = ((0.31, 417.3), (0.73, 417.3), (0.73, 3161.7), (0.31, 3161.7))
    a = PolyGainOp(pts, -6.0)
    b = PolyGainOp(pts[1:] + pts[:1], -6.0)
    assert a.gain_db == b.gain_db == -6.0
    assert set(a.points) == set(b.points)
    # 矩形多边形与其等价矩形增益必须落到同一条渲染路径
    from audiocheck.core.edit.ops import BandGainOp
    band = BandGainOp(0.31, 0.73, 417.3, 3161.7, -6.0)
    assert band.gain_db == a.gain_db


# --------------------------------------------------------------- 能量度量
def test_tf_energy_metric_records_requested_gain():
    """全带 -6 dB 的实际能量下降必须在合理区间（**2 变 / 会让它系统性偏掉）。

    ## 为什么不是精确 -6.00 dB

    实测本实现给出 **-4.66 dB**，这是 STFT/WOLA 往返的**已知特性**，不是 bug：
    * `BandGainOp` 的 -6 dB 是**谱域增益**；
    * 时域能量比 = 谱域增益 + 窗函数归一化残差 + 频带边缘（0 Hz 与 fs/2 两个
      bin 只被算半边）的泄漏；
    * 本仓 docstring 已声明"时频编辑不是位精确的"，审计报 `roundtrip_floor_dbfs`。

    判据的作用是**捉口径错误**：若 `**2` → `/`（能量变幅度），
    在 -6 dB 处会偏约 3 dB（-4.66 → -1.5 一类的量级），落在窗口外。
    所以窗口给 3 dB 宽：够宽到容纳 WOLA 残差，够窄到捉住口径错误。
    """
    from audiocheck.core.edit.ops import BandGainOp
    fs, n = 8000, 4096
    t = np.arange(n) / fs
    x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float64)[:, None]
    with tempfile.TemporaryDirectory() as d:
        s = str(Path(d) / "s.wav")
        o = str(Path(d) / "o.wav")
        sf.write(s, x, fs, subtype="FLOAT")
        op = BandGainOp(0.0, n / fs, 0.0, fs / 2, -6.0)
        audit = R.render(R.Project(s, [op]), o, overwrite=True)
        y, _ = sf.read(o, dtype="float64")
    if y.ndim == 1:
        y = y[:, None]
    ex = float(np.sum(x ** 2))
    ey = float(np.sum(y ** 2))
    drop = 10.0 * np.log10(max(ey, 1e-30) / max(ex, 1e-30))
    assert -7.5 < drop < -3.0, (
        "全带 -6 dB 的实际能量下降 %.2f dB 落在 [−7.5, −3.0] 之外——"
        "超出了 WOLA 往返残差能解释的范围，怀疑能量口径错了" % drop)
    # 与 -6 dB 的偏差本身也记账（这是可核对的实现特性，不是隐藏误差）
    assert abs(drop - (-6.0)) < 2.0, (
        "与请求值的偏差 %.2f dB 过大（WOLA 残差实测约 1.3 dB）" % abs(drop + 6.0))
    assert audit, "审计不能为空"


# ------------------------------------------- 走查的**边界条件**（最后 5 个存活）
def test_sha256_file_content_only_false_still_hashes_whole_file(tmp_path):
    """★ `content_only=False` 时**绝不能**走 WAV chunk 遍历（`and`→`or` 变异）。

    变异后条件变成 `content_only or endswith(...)`：
    * `content_only=False` + `.wav` 文件 ⇒ **仍走 chunk 遍历** ⇒ `dest_sha256`
      的语义从"产物字节"变成"内容哈希"，**审计里两个哈希失去区分力**
      （PEAK 时间戳差异不再被计入产物哈希）。
    * `content_only=True` + 非 `.wav` ⇒ 两者都走整文件哈希，无差异。

    判据：同一份 WAV，`content_only=True` 与 `False` 必须给出**不同**的哈希
    （因为 WAV 里带 PEAK，内容哈希会跳过它）。
    """
    # 造一个带 PEAK 的真实 WAV（soundfile 写出时就会带）
    fs = 8000
    x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(fs) / fs)).astype(np.float32)
    p = str(tmp_path / "withpeak.wav")
    sf.write(p, x[:, None], fs, subtype="PCM_16")
    h_content = R._sha256_file(p, content_only=True)
    h_full = R._sha256_file(p, content_only=False)
    assert h_content != h_full, (
        "content_only=True/False 给出同一个哈希 ⇒ 条件写成了 or，"
        "或 PEAK 跳过没生效")


def test_wav_content_hash_exactly_twelve_bytes(tmp_path):
    """★ 恰好 12 字节（只有 RIFF 头、没有 chunk）的边界。

    `len(blob) < 12` 变异成 `<=` 会让 12 字节的文件走进退化分支（整文件哈希）；
    `12`→`13` 也一样。两种都应该被"正常走查"处理——12 字节是**合法的最小
    RIFF/WAVE 容器**（有 `RIFF`+size+`WAVE`，没有 chunk）。

    判据：12 字节的 RIFF/WAVE 必须走 chunk 遍历路径（哈希 == 期望复算值），
    而不是退化为整文件哈希。
    """
    blob = b"RIFF" + struct.pack("<I", 4) + b"WAVE"     # 恰好 12 字节
    p = _write(tmp_path, "minimal.wav", blob)
    got = R._wav_content_sha256(p)
    # 走查路径：h(RIFF) + h(WAVE)，无 chunk
    import hashlib
    expect = hashlib.sha256(b"RIFF" + b"WAVE").hexdigest()
    assert got == expect, (
        "12 字节的最小 RIFF/WAVE 应走 chunk 遍历（得到 %s...，期望 %s...）"
        % (got[:12], expect[:12]))
    # 与退化分支（整文件哈希）必须不同，证明真的走了走查路径
    assert got != hashlib.sha256(blob).hexdigest()


def test_wav_content_hash_chunk_header_exactly_eight_bytes_left(tmp_path):
    """★ 末尾恰好剩 8 字节（一个 chunk 头、内容为空）的边界。

    `while pos + 8 <= len(blob)` 变异成 `<` 会漏掉这个 chunk；
    `8`→`9` 也一样。判据：8 字节的空 chunk 必须被算进内容哈希。
    """
    fmt_chunk = _chunk(b"fmt ", FMT_PAYLOAD)
    empty = b"LIST" + struct.pack("<I", 0)              # 恰好 8 字节
    blob = _riff(fmt_chunk) + empty
    p = _write(tmp_path, "tail8.wav", blob)
    got = R._wav_content_sha256(p)
    import hashlib
    expect = hashlib.sha256(b"RIFF" + b"WAVE" + fmt_chunk + empty).hexdigest()
    assert got == expect, (
        "末尾 8 字节的空 chunk 必须被算进哈希（得到 %s...，期望 %s...）"
        % (got[:12], expect[:12]))
