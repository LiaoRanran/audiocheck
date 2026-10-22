"""`causes.py` 的等号侧与粗档强度判据（2026-10-22 变异缺口补全）。

`causes` 的 33 条存活分两类：
* **粗档强度常数**（0.8 容器级失配 / 0.9 位级硬证据 / 0.5 统计吻合）——只进输出字段，
  但它是 UI 上"疑似"的分级依据，写错会把弱证据抬成强证据；
* **门限比较的等号侧**（`>=` / `<=` 各自的方向）——既有用例都取在门限中间。

本文件把每个候选的**触发条件与强度**逐条钉住。素材全部是扁平 dict（`causes`
不做测量，只做 dict 逻辑），所以每个字段都能取在**恰好等于门限**上。
"""
from __future__ import annotations

from audiocheck.config import Thresholds
from audiocheck.core.analyzers.causes import cause_candidates, usability_matrix

TH = Thresholds()


def _r(**kw) -> dict:
    base = {"cutoff_hz": None, "brickwall_db_per_oct": None, "bands_pct": None,
            "flatness": 0.1, "clip_pct": 0.0, "true_peak_dbfs": None, "lra": None,
            "dr_db": None, "width_db": None, "corr": None, "lufs": None}
    base.update(kw)
    return base


def _c(r, fs: int = 44100) -> dict:
    return {c["key"]: c for c in cause_candidates(r, fs, TH)}


def _m(r: dict) -> dict:
    return usability_matrix(r, [], TH)


# ============================================================ 粗档强度
def test_candidate_strength_tiers_are_0809_05():
    """三个粗档：**0.8 = 容器级失配 / 0.9 = 位级硬证据 / 0.5 = 统计吻合**。

    它们是 UI"疑似"分级与并列候选的依据，改一位就把弱证据抬成强证据。
    """
    up = _c(_r(cutoff_hz=20000.0), fs=96000)["升频"]
    assert up["strength"] == 0.8
    assert "96.0kHz" in up["evidence"][0] and "20.0kHz" in up["evidence"][0]

    pad = _c(_r(bit_padded=True, bit_origin=8, bits="PCM_24"))["位深填充"]
    assert pad["strength"] == 0.9
    assert "24" in pad["evidence"][0] and "PCM_" not in pad["evidence"][0]

    assert _c(_r(cutoff_hz=18000.0, brickwall_db_per_oct=18.0))["有损转码"]["strength"] == 0.5
    assert _c(_r(bands_pct={"sub": 10.0, "bass": 20.0}))["黑胶转录"]["strength"] == 0.5
    assert _c(_r(cutoff_hz=18000.0, brickwall_db_per_oct=10.0))["磁带转录"]["strength"] == 0.5
    assert _c(_r(cutoff_hz=18000.0))["刻意带限"]["strength"] == 0.5


def test_mid_slope_marks_lossy_and_deliberate_bandlimit_as_indistinguishable():
    """中间带滚降（15 ≤ slope < 24）⇒ 有损转码与刻意带限**并列**且标"无法区分"。

    `mid_slope` 的两侧都不含：恰好 15 是平缓（gentle）、恰好 24 是硬砖墙（hard）。
    """
    mid = _c(_r(cutoff_hz=18000.0, brickwall_db_per_oct=18.0))
    assert "有损转码" in mid and "刻意带限" in mid
    assert mid["有损转码"]["ambiguous"] is True
    assert "刻意带限" in mid["有损转码"]["ambiguous_with"]
    # 恰 24 ⇒ 已是硬砖墙：走 conf 分支（clamp 到 1.0），不再是 0.5 的中间带
    assert _c(_r(cutoff_hz=18000.0, brickwall_db_per_oct=TH.brick_db))["有损转码"]["strength"] == 1.0
    # 恰 15 仍是**中间带**（`gentle = slope < 15` 是严格的）⇒ 有损转码 0.5、磁带不成立
    edge = _c(_r(cutoff_hz=18000.0, brickwall_db_per_oct=TH.brick_flat_db_per_oct))
    assert edge["有损转码"]["strength"] == 0.5 and "中间带" in edge["有损转码"]["evidence"][1]
    assert "磁带转录" not in edge
    # 14.9 ⇒ 平缓滚降：有损转码不成立，磁带/刻意带限出现
    gentle = _c(_r(cutoff_hz=18000.0,
                   brickwall_db_per_oct=TH.brick_flat_db_per_oct - 0.1))
    assert "有损转码" not in gentle and "磁带转录" in gentle
    # 刻意带限的 `ambiguous_with` 只在**中间带**才并列"有损转码"；
    # `and` 改 `or` 会让"平缓滚降"也把有损转码写进并列集（多一个无法区分的候选）
    assert "有损转码" not in gentle["刻意带限"]["ambiguous_with"]


# ============================================================ 门限等号侧
def test_mastering_candidate_gates_are_strict():
    """`clip > clip_pct` 与 `lra < lra_flat` 都是**严格**的。

    恰好 0.005% 削波 / 恰好 3.0 LU 都不算"母带限幅"的证据项。
    """
    base = dict(true_peak_dbfs=1.0)
    assert "母带限幅" not in _c(_r(cutoff_hz=22000.0, clip_pct=TH.clip_pct,
                                  lra=TH.lra_flat, **base))
    yes = _c(_r(cutoff_hz=22000.0, clip_pct=TH.clip_pct + 0.001,
                lra=TH.lra_flat, **base))["母带限幅"]
    assert yes["strength"] == 0.5
    assert "动态压平" not in "".join(yes["evidence"]), yes["evidence"]
    yes2 = _c(_r(cutoff_hz=22000.0, clip_pct=TH.clip_pct + 0.001,
                 lra=TH.lra_flat - 0.1, **base))["母带限幅"]
    assert "动态压平" in "".join(yes2["evidence"]), yes2["evidence"]
    # 同一行还有第二个 `>`：`tp > 0.0`。恰 0.00 dBTP 不算"超满刻度" ⇒ 不出候选
    assert "母带限幅" not in _c(_r(cutoff_hz=22000.0, clip_pct=TH.clip_pct + 0.001,
                                  lra=TH.lra_flat, true_peak_dbfs=0.0))


def test_broadcast_candidate_includes_the_low_edge_and_excludes_the_width_edge():
    """广播版：`low_pct <= 5.0`（**含**等号）且 `wd < −18.0`（**不含**等号）。

    两条各取一次等号、另一条留出余量，否则两个方向互相掩盖（同 verdict 的老坑）。
    """
    bands = {"sub": 2.0, "bass": 3.0}                  # low_pct = 5.0（恰等）
    c = _c(_r(bands_pct=bands, width_db=-20.0))["广播/电台版"]
    assert c["strength"] == 0.5
    assert "广播/电台版" not in _c(_r(bands_pct=bands, width_db=TH.width_narrow_db))
    assert "广播/电台版" not in _c(_r(bands_pct={"sub": 5.1, "bass": 0.0}, width_db=-20.0))


def test_fake_stereo_candidate_includes_both_edges():
    """假立体声：`corr >= 0.98` 与 `wd <= −30.0` **都含**等号。

    取恰等号、另一条留余量：改 `>=`→`>` 或 `<=`→`<` 都会漏掉这一格。
    """
    c = _c(_r(corr=TH.fake_stereo_corr, width_db=-40.0))
    assert c["假立体声"]["strength"] == 0.5
    assert "假立体声" in _c(_r(corr=0.99, width_db=TH.fake_stereo_width_db))
    assert "假立体声" not in _c(_r(corr=TH.fake_stereo_corr - 0.01, width_db=-40.0))
    assert "假立体声" not in _c(_r(corr=0.99, width_db=TH.fake_stereo_width_db + 0.1))


# ============================================================ 用途矩阵
def test_usability_high_frequency_gates_are_inclusive():
    """`k >= 20.0` 可用、`k >= 16.0` 受限：两条**都含**等号。"""
    assert _m({"cutoff_hz": 20000.0})["频响延伸/空气感"]["level"] == "可用"
    assert _m({"cutoff_hz": 19900.0})["频响延伸/空气感"]["level"] == "受限"
    assert _m({"cutoff_hz": 16000.0})["频响延伸/空气感"]["level"] == "受限"
    assert _m({"cutoff_hz": 15900.0})["频响延伸/空气感"]["level"] == "不可用"


def test_usability_low_frequency_gates():
    """低频：`low_pct <= 5.0` ⇒ 不可用（含等号）；`low_pct >= 60.0` ⇒ "适合试下潜"（含等号）。"""
    assert _m({"bands_pct": {"sub": 2.0, "bass": 3.0}})["低频下潜/控制力"]["level"] == "不可用"
    normal = _m({"bands_pct": {"sub": 3.1, "bass": 2.0}})["低频下潜/控制力"]
    assert normal["level"] == "可用" and "正常范围" in normal["reason"]
    strong = _m({"bands_pct": {"sub": 30.0, "bass": 30.0}})["低频下潜/控制力"]
    assert strong["level"] == "可用" and "适合试低频下潜" in strong["reason"]
    near = _m({"bands_pct": {"sub": 30.0, "bass": 29.9}})["低频下潜/控制力"]
    assert "正常范围" in near["reason"]


def test_usability_width_gate_is_strict():
    """`wd < −18.0` 才是"近乎单声道"（受限）；恰好 −18.0 算可用。"""
    assert _m({"width_db": TH.width_narrow_db})["声场宽度"]["level"] == "可用"
    assert _m({"width_db": TH.width_narrow_db - 0.1})["声场宽度"]["level"] == "受限"


def test_usability_upper_mid_gate_is_inclusive():
    """`um >= 15.0%` ⇒ 适合试齿音（含等号）；14.9% ⇒ 受限。"""
    assert _m({"bands_pct": {"upper_mid": TH.advice_upper_mid_pct}})["齿音/刺耳度"]["level"] == "可用"
    assert _m({"bands_pct": {"upper_mid": TH.advice_upper_mid_pct - 0.1}})["齿音/刺耳度"]["level"] == "受限"


def test_usability_dynamics_flags_are_strict():
    """动态格三条：`lra < 3.0`、`dr < 6.0`、`lufs > −9.0` —— **全部严格**。

    LRA/DR/响度恰好压在门限上时都**不**打"动态压平/余量低/响度战"的标记。
    """
    ok = _m({"lra": TH.lra_flat, "dr_db": TH.dr_low_db, "lufs": TH.loud_war_lufs})
    assert ok["动态与微动态"]["level"] == "可用", ok["动态与微动态"]
    assert _m({"lra": TH.lra_flat - 0.1, "dr_db": 20.0})["动态与微动态"]["level"] == "不可用"
    assert "动态压平" in _m({"lra": TH.lra_flat - 0.1, "dr_db": 20.0})["动态与微动态"]["reason"]
    bottom = _m({"lra": 20.0, "dr_db": TH.dr_low_db - 0.1})["动态与微动态"]
    assert bottom["level"] == "不可用" and "偏低" in bottom["reason"]
    war = _m({"lra": 20.0, "dr_db": 20.0, "lufs": TH.loud_war_lufs + 0.1})["动态与微动态"]
    assert war["level"] == "不可用" and "响度战" in war["reason"]


def test_loudness_axis_treats_zero_true_peak_as_not_over():
    """`tp > 0.0`：**0.00 dBTP 不算超满刻度**（等号在"没问题"侧）。

    改成 `>=` 会把"刚好顶到 0 dBFS"的正常母带打进"受限"。
    """
    ok = _m({"true_peak_dbfs": 0.0, "dr_db": 20.0})
    assert ok["大音量失真"]["level"] == "可用", ok["大音量失真"]
    assert "真峰值 +0.00 dBTP" in ok["大音量失真"]["reason"]
    assert _m({"true_peak_dbfs": 0.01, "dr_db": 20.0})["大音量失真"]["level"] == "受限"
    # 同一个格里 `dr < dr_low_db` 也是严格的：恰 6.0 dB 不算"余量低"
    assert _m({"true_peak_dbfs": -1.0, "dr_db": TH.dr_low_db})["大音量失真"]["level"] == "可用"
    assert _m({"true_peak_dbfs": -1.0,
               "dr_db": TH.dr_low_db - 0.1})["大音量失真"]["level"] == "受限"


def test_overall_trust_reads_the_bit_verdict_when_nothing_is_ambiguous():
    """`elif "无法统计" in str(bit_verdict or "")`：**没有并列候选**时也要降级。

    改 `or`→`and` 会得到 `"无法统计" in ""` ⇒ 恒 False ⇒ "位深无法统计"的文件
    被报成"可直接引用"（`查不到 ≠ 没问题` 那条纪律被绕过）。
    """
    m = _m({"bit_verdict": "无法统计：低 8 位恒零或数据不足"})
    assert m["整体可信度"]["level"] == "受限", m["整体可信度"]
    assert "查不到" in m["整体可信度"]["reason"]
