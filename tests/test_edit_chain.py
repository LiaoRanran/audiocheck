"""复合操作（链式渲染）的判据。

**为什么可以放开混用**：`packaging/op_order_probe.py` 实测六种排列——
SDR 极差只有 **0.44 dB**（顺序不是生死攸关），但**咔哒残差差 2.2 倍**、
嗡压制差 1.75 dB ⇒ 需要一个**规范顺序**（`render.PHASE`），而不是随便排。

**为什么该顺序**（机制，不是口味）：
* 脉冲先修：去削波的带限投影把孤立咔哒**抹宽**，之后两点预测残差就抓不住；
* 去削波次之：它要看到未被滤波的饱和形状；
* 去嗡再次：LTI 改**所有**样本，先做会污染检测尺子；
* 电平最后：自动削波电平/检测门限都从数据里量，先改电平会让它们读错。
"""
from __future__ import annotations

import glob
import hashlib
import os
import tempfile

import numpy as np
import pytest
import soundfile as sf

from audiocheck.core.edit import DeclickOp, DeclipOp, DehumOp, GainOp, Project, corpus, render
from audiocheck.core.edit.declick import residual
from audiocheck.core.edit.render import canonical_order

FS = 48000
T = 1.5


def _faulty(seed: int = 11):
    """带限白噪 + 削波 + 60 个咔哒 + 50 Hz 嗡（真值 `clean` 一并返回）。"""
    clean = corpus.band_limited_noise(T, FS, 18000.0, amp=0.5, seed=seed)
    ceiling = 0.3 * float(np.abs(clean).max())
    y = np.clip(clean, -ceiling, ceiling)
    rng = np.random.default_rng(seed)
    pos = np.sort(rng.uniform(0.05, T - 0.05, 60))
    idx = [int(round(p * FS)) for p in pos]
    for i in idx:
        y[i, 0] += 0.6
    t = np.arange(y.shape[0]) / FS
    h = sum(np.sin(2 * np.pi * 50 * k * t + 0.7 * k) / k for k in (1, 2, 3))
    h *= (10 ** (-30 / 20.0)) * float(np.abs(clean).max()) / float(np.abs(h).max())
    return clean, y + h[:, None], ceiling, idx


def _write(tmp_path, x, name="src.wav") -> str:
    p = tmp_path / name
    sf.write(str(p), x, FS, subtype="FLOAT")
    return str(p)


def _sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_mixed_stack_is_chained_in_canonical_order(tmp_path):
    """混用不再拒绝：四个算子按**规范顺序**依次渲染，审计逐层可查。"""
    clean, y, ceiling, _ = _faulty()
    src = _write(tmp_path, y)
    ops = [DehumOp(0.0, T, 50.0), GainOp(0.0, T, -1.0),
           DeclipOp(0.0, T, bandwidth=18000.0, ceiling=ceiling), DeclickOp(0.0, T)]
    dst = str(tmp_path / "out.wav")
    audit = render(Project(src, ops), dst)
    assert audit["chained"] is True
    assert [s["op"]["kind"] for s in audit["stages"]] == \
        ["declick", "declip", "dehum", "gain"]
    assert len(audit["ops"]) == 4
    assert audit["source"] == src
    assert "reordered_from" in audit and len(audit["reordered_from"]) == 4
    assert os.path.exists(dst)


def test_canonical_order_is_stable_within_a_phase():
    """同相位的两个电平操作保持**用户给的相对顺序**（稳定排序）。"""
    a, b = GainOp(0.0, 0.2, -3.0), GainOp(0.5, 0.7, -6.0)
    assert canonical_order([b, a]) == [b, a]
    assert canonical_order([GainOp(0.0, 0.2, -3.0), DeclickOp(0.0, T),
                            GainOp(0.5, 0.7, -6.0)])[0].to_dict()["kind"] == "declick"


def test_declick_before_declip_leaves_less_click_residual(tmp_path):
    """★ **顺序的实测依据**：先 declick 再 declip，咔哒残差是反序的 **一半以下**。

    机制：去削波的带限投影把孤立咔哒抹宽 ⇒ 之后的两点预测残差检测抓不住。
    """
    clean, y, ceiling, idx = _faulty()
    src = _write(tmp_path, y)
    work = tempfile.mkdtemp()

    def two(first, second, tag):
        p1 = os.path.join(work, tag + "1.wav")
        p2 = os.path.join(work, tag + "2.wav")
        render(Project(src, [first]), p1, subtype="FLOAT")
        render(Project(p1, [second]), p2, subtype="FLOAT")
        out, _ = sf.read(p2, dtype="float64", always_2d=True)
        return float(np.max(np.abs(residual(out)[idx])))

    dc = lambda: DeclickOp(0.0, T)                      # noqa: E731
    dp = lambda: DeclipOp(0.0, T, bandwidth=18000.0, ceiling=ceiling)  # noqa: E731
    r_ok = two(dc(), dp(), "a_")
    r_bad = two(dp(), dc(), "b_")
    # 实测（1.5 s 素材）：0.2428 vs 0.4753（≈2 倍）；2 s 素材上 0.1538 vs 0.3448（2.2 倍）
    assert r_ok < 0.7 * r_bad, "先 declick 应显著更干净（实得 %.4f vs %.4f）" % (r_ok, r_bad)


def test_regions_outside_every_selection_are_untouched(tmp_path):
    """两条不同域的选区：**两者之外**逐位不变。"""
    t = np.arange(int(1.0 * FS)) / FS
    x = (0.3 * np.sin(2 * np.pi * 440 * t)).copy()
    x[int(0.75 * FS)] += 0.6                             # 一个咔哒，落在 declick 选区里
    src = _write(tmp_path, x)
    dst = str(tmp_path / "out.wav")
    audit = render(Project(src, [GainOp(0.0, 0.2, -6.0), DeclickOp(0.6, 0.9)]), dst)
    assert audit["chained"] is True
    s, _ = sf.read(src, dtype="float64", always_2d=True)
    y, _ = sf.read(dst, dtype="float64", always_2d=True)
    assert np.array_equal(y[int(0.2 * FS):int(0.6 * FS)], s[int(0.2 * FS):int(0.6 * FS)])
    assert np.array_equal(y[int(0.9 * FS):], s[int(0.9 * FS):])


def test_chained_single_op_matches_the_direct_path(tmp_path):
    """链式路径**不许**在只有一个真操作时引入差异（0 dB 增益阶段必须是纯拷贝）。

    FLOAT 中间文件对 PCM16/24 与 FLOAT 源不引入额外量化 ⇒ 与直接路径**逐采样相同**。
    （断言的是**采样本体**——"逐位"在这个项目里指音频采样，不是容器头的字节。）
    """
    clean, y, ceiling, _ = _faulty()
    src = _write(tmp_path, y)
    direct = str(tmp_path / "direct.wav")
    chained = str(tmp_path / "chained.wav")
    render(Project(src, [DeclickOp(0.0, T)]), direct)
    a = render(Project(src, [GainOp(0.0, T, 0.0), DeclickOp(0.0, T)]), chained)
    assert a["chained"] is True
    x, _ = sf.read(direct, dtype="float64", always_2d=True)
    z, _ = sf.read(chained, dtype="float64", always_2d=True)
    assert np.array_equal(x, z)


def test_chaining_leaves_no_temp_directory(tmp_path):
    """临时目录用完即删（成功与失败路径都要删）。"""
    clean, y, ceiling, _ = _faulty()
    src = _write(tmp_path, y)
    pat = os.path.join(tempfile.gettempdir(), "ac_chain_*")
    before = set(glob.glob(pat))
    render(Project(src, [GainOp(0.0, 0.2, -3.0), DeclickOp(0.2, 0.5)]),
           str(tmp_path / "ok.wav"))
    after = set(glob.glob(pat))
    assert after - before == set(), "残留临时目录：%s" % sorted(after - before)
    with pytest.raises(ValueError):
        render(Project(src, [GainOp(0.0, 0.2, -3.0), DeclickOp(0.2, 0.5)]),
               src)                     # 目标=源 ⇒ 先被 _check_dest 拒绝
    assert set(glob.glob(pat)) - before == set()
