"""decrackle 判据（**判据先行，结论是"不落地"**，`docs/editor_roadmap.md §14③`）。

真值来源 = `corpus.crackle_train`（位置/幅度**均已知**的密集小脉冲）。

## 这文件在钉什么

`packaging/decrackle_probe.py` 把"破音/噼啪"（密集脉冲）喂给**现有** `DeclickOp`
（两点残差 + 全段 MAD + 插值，只是换了 `(threshold, max_run)`）做了普查，并试了 LPC(64)
残差这条**新量具**。结论（实测，可复算）：

* "decrackle" **不是新算法**：它就是在**更松的设置**下跑 `DeclickOp`——`threshold=3`、
  `max_run=None` 在密集脉冲上 ΔSDR 约 **+3.1 dB**（探针表 "2点3 关"），而默认
  `threshold=5 / max_run=4` 只有约 **+1.4 dB**（"2点5 关/4"）。两者**同一个机制**
  ⇒ 另做一个 `DecrackleOp` 是**重复造轮子**（把同一段插值逻辑再抄一遍）。
* 更松的设置**会误伤真实信号**：`threshold=3 / max_run=None` 在**带限白噪**上检出
  **285** 个样本、在**合法瞬态**上检出 **286** 个，而默认设置在这两类素材上都是 **0**——
  这正是默认取 5/4 的"代价不对称"理由（误报会把信号修坏）。所以"decrackle" 只能当作
  一个**显式可选**的 `DeclickOp` 设置，绝不能当默认，也不该另立算子。
* LPC(64) 残差这条新量具**整体为负**（探针表 LPC64：ΔSDR **−2.6 dB**）⇒ 没有更好的机制，
  也**没有**要落地的独立算子。

所以这文件不测"某个 decrackle 算子"，而是把上面三条**钉成回归关**：
1. 默认 `DeclickOp` 在**干净/白噪/合法瞬态**素材上**0 检出**（不会误伤）；
2. 默认 `DeclickOp` 在**密集破音**上仍**正收益**（证明"噼啪"本就在它的职责内）；
3. 更松的设置**收益更高但会误伤真实信号**（白噪/瞬态检出 >> 0）——
   量化"为什么默认不取它 / 为什么它只是个可选设置"，也防止有人偷偷把默认改松后
   声称"修得更好"。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import DeclickOp, Project, corpus, declick, render  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000
SECONDS = 2.0


def _crackled(rate: float, amp_min: float, amp_max: float, seed: int = 5) -> dict:
    """**精确复刻** `decrackle_probe.py::build`：谐波音 + 已知位置/幅度的密集脉冲。"""
    clean = corpus.harmonic_tone(SECONDS, FS, amp=0.5, seed=3)
    return corpus.crackle_train(clean, rate, FS, amp_min=amp_min, amp_max=amp_max, seed=seed)


def _dsdr(pack, threshold, max_run) -> float:
    """重现探针 `measure` 的主判据（ΔSDR vs 干净真值）。"""
    clean, y = pack["clean"], pack["crackled"]
    out, _ = declick.declick(y, threshold, max_run)
    return sdr_db(out, clean) - sdr_db(y, clean)


def _noise() -> np.ndarray:
    return corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=18000.0, seed=1)


def _transienty() -> np.ndarray:
    """带**合法瞬态**（陡起音）的素材——用来量"会不会误伤"。"""
    x = _noise()[:, 0].copy()
    for i in np.arange(int(0.15 * FS), x.size - int(0.05 * FS), int(0.25 * FS)):
        L = int(0.005 * FS)
        env = np.exp(-np.arange(L) / (0.1 * L))
        x[i:i + L] += 0.8 * env * np.sin(2 * np.pi * 3000.0 * np.arange(L) / FS)
    return (x / np.abs(x).max() * 0.95)[:, None]


def _detected(y, threshold, max_run) -> int:
    _, st = declick.declick(y, threshold, max_run)
    return st["detected_samples"]


def _write(path, x, subtype: str = "FLOAT") -> str:
    sf.write(str(path), x.astype(np.float32), FS, subtype=subtype)
    return str(path)


def _read(path) -> np.ndarray:
    a, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return a


# ----------------------------------------------------------------- 1. 默认不误伤任何真实素材
def test_default_declick_does_not_fire_on_clean_noise_or_transient():
    """默认阈值下，干净谐波音 / 带限白噪 / 合法瞬态 **全是 0 检出** ⇒ 不会误伤真实信号。"""
    clean = corpus.harmonic_tone(SECONDS, FS, amp=0.5, seed=3)
    for label, y in (("clean", clean), ("noise", _noise()), ("transient", _transienty())):
        assert _detected(y, 5.0, 4) == 0, \
            "默认去咔哒在 %s 素材上不应有检出（误伤代价），得到 %d" % (label, _detected(y, 5.0, 4))


def test_default_declick_leaves_clean_audio_bit_identical(tmp_path):
    """默认阈值下，干净谐波音渲染**逐位不变**（0 检出，非破坏硬承诺）。"""
    x = corpus.harmonic_tone(SECONDS, FS, amp=0.5, seed=3)
    src = _write(tmp_path / "in.wav", x)
    dst = str(tmp_path / "out.wav")
    rec = render(Project(src, [DeclickOp(0.0, SECONDS, 5.0, 4)]), dst)
    out = _read(dst)
    src_x = _read(src)            # 源是以 FLOAT(float32) 存的 ⇒ 比较"读回的源"而非原始 float64
    assert rec["ops"][0]["detected_samples"] == 0, rec["ops"][0]
    assert np.array_equal(out, src_x)


# ----------------------------------------------------------------- 2. 密集破音仍在默认算子职责内（正收益）
def test_default_declick_recovers_dense_crackle_positively():
    """密集破音（500 个/秒，中高幅）上默认算子仍有**正** ΔSDR —— "噼啪"本就在去咔哒职责内。"""
    pack = _crackled(500.0, 0.05, 0.5, seed=5)
    d = _dsdr(pack, 5.0, 4)
    # 探针表 "2点5 关/4" 在该密度下约 +1.09 dB；留足容差只断言"确实在修"（不会是负/零）。
    assert d > 0.5, "默认去咔哒在密集破音上应仍有正收益，得到 %.3f dB" % d


# ----------------------------------------------------------------- 3. "decrackle" 的正确姿势 = 默认参数，代价不对称依旧成立
def test_loose_setting_hurts_while_default_recovers():
    """钉死"代价不对称"——**双尺度内核**下的现行实测（2026-10-27）：

    * **默认**（α=5、守卫 4、全局∪局部）在稀疏破音上 **+2.65 dB**、密集上 **+1.44 dB**
      ——双尺度并集已经把旧全局单尺子的稀疏缺口（+1.94）补掉，**不需要**降 α；
    * 降 α（3.0）+ 关守卫在**同一个破音语料**上反而**修坏**（−2.95 dB：局部尺子在 α=3
      下把合法内容也当脉冲），并在白噪/合法瞬态上各误报 **434** 个样本；
    * 默认在这两类素材上仍是 **0 检出**。
    ⇒ "为什么默认不取更松"在新内核下依然成立，且数值更难看——这个判据防的是
    "把默认改松后声称修得更好"。
    """
    pack_sparse = _crackled(10.0, 0.05, 0.5, seed=5)
    pack = _crackled(100.0, 0.05, 0.5, seed=5)
    default_sparse = _dsdr(pack_sparse, 5.0, 4)
    default = _dsdr(pack, 5.0, 4)
    assert default_sparse > 2.4, "默认（双尺度）在稀疏破音上应 +2.65 量级，得到 %.3f dB" % default_sparse
    assert default > 1.2, "默认（双尺度）在密集破音上应 +1.44 量级，得到 %.3f dB" % default

    loose = _dsdr(pack, 3.0, None)
    assert loose < 0.5, "α=3+无守卫在双尺度内核下应修坏（实测 −2.95），得到 %.3f dB" % loose

    n_loose = _detected(_noise(), 3.0, None)
    t_loose = _detected(_transienty(), 3.0, None)
    assert n_loose > 100 and t_loose > 100, \
        "更松设置应在白噪/合法瞬态上大量误报（实测各 434），得到 %d/%d" % (n_loose, t_loose)
    assert _detected(_noise(), 5.0, 4) == 0 and _detected(_transienty(), 5.0, 4) == 0


def test_no_separate_decrackle_operator_exists():
    """结构上确认：没有另立 `DecrackleOp`——密集破音由 `DeclickOp` 同一套机制覆盖。"""
    from audiocheck.core.edit import ops
    assert not hasattr(ops, "DecrackleOp"), "decrackle 已判据先行判为不落地，不应另立算子"
    assert hasattr(ops, "DeclickOp")
