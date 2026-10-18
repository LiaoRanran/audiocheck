"""判定层的**边界**回归：正好落在阈值上时，判据往哪边倒。

## 为什么单独一个文件

变异测试（`packaging/mutation_probe.py`）把判定层纳入覆盖后，**18 个变异体存活**
——其中 8 个是把 `<=` 改成 `<`、`>=` 改成 `>` 这类**边界翻转**。根因很一致：
原有测试只验"阈值里面一点"和"阈值外面一点"，**从没验过正好等于阈值**。

边界是判据最容易出错的地方（也是用户最容易踩到的地方：真实曲库实测 **57%**
的文件距最近判定线 <3%，见 `docs/real_library.md`）。所以这里逐条钉住
"正好等于时往哪边倒"，并用 `==` 断言而不是 `>=`/`<=` 的近似。

⚠ 这些测试**不改变任何门限**，只钉住现有语义。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.config import Thresholds  # noqa: E402
from audiocheck.core.analyzers.causes import (  # noqa: E402
    cause_candidates,
    usability_matrix,
)
from audiocheck.core.analyzers.verdict import (  # noqa: E402
    axis_bandwidth,
    axis_bitdepth,
    axis_dynamics,
    axis_level,
    bandwidth_margin,
)

TH = Thresholds()


# ---------------------------------------------------------------- 容器分界
def test_container_boundary_44100_class_includes_exact_cut():
    """`fs <= fs_44_container`：**正好等于 45000 Hz** 仍算 44.1k 档。

    改了 `<` 就会把它推到 48k 档，判词从「CD 级满带宽」变成「真 48kHz 带宽」——
    同一份文件因为容器分类不同而得不同结论，且没人看得出为什么。
    """
    lv, txt, _c = axis_bandwidth(int(TH.fs_44_container), 21.5e3, 30.0, TH)
    assert (lv, txt) == ("ok", "CD 级满带宽"), "45000 Hz 应走 44.1k 档，实得 %r/%r" % (lv, txt)


def test_container_boundary_48000_class_includes_exact_cut():
    lv, txt, _c = axis_bandwidth(int(TH.fs_48_container), 23.0e3, 30.0, TH)
    assert (lv, txt) == ("ok", "真 48kHz 带宽"), "50000 Hz 应走 48k 档，实得 %r/%r" % (lv, txt)


def test_just_above_container_boundary_switches_class():
    """比边界高 1 Hz 就必须换档——证明上一条不是"两个分支都这么写"的巧合。"""
    _lv, txt, _c = axis_bandwidth(int(TH.fs_44_container) + 1, 21.5e3, 30.0, TH)
    assert txt != "CD 级满带宽"


# ---------------------------------------------------------------- 带宽阈值
def test_cd_full_boundary_is_inclusive():
    """`k >= cd_full`：正好 21.5 kHz 算满带宽（>=，不是 >）。"""
    assert axis_bandwidth(44100, 21.5e3, 30.0, TH)[1] == "CD 级满带宽"


def test_hi_ok_boundary_is_inclusive_on_the_bad_side():
    """`k <= hi_ok`：正好 24.5 kHz 仍判**伪 Hi-Res**（这是"宁可说它不够"的一侧）。"""
    lv, txt, _c = axis_bandwidth(96000, 24.5e3, 30.0, TH)
    assert lv == "bad" and "伪 Hi-Res" in txt


def test_hi_ratio_boundary_is_inclusive():
    """`cutoff/nyq >= hi_ratio`：正好 0.85 算**真 Hi-Res**。"""
    nyq = 96000 / 2
    lv, txt, _c = axis_bandwidth(96000, nyq * TH.hi_ratio, 30.0, TH)
    assert lv == "ok" and "真 Hi-Res" in txt


def test_margin_uses_same_container_boundaries():
    """`bandwidth_margin` 的分档必须与 `axis_bandwidth` 同界（否则披露的线是错的）。"""
    # 45000 Hz 走 44.1k 档 ⇒ 最近的线里应有「CD 级满带宽」
    m = bandwidth_margin(int(TH.fs_44_container), 21.5e3, TH)
    assert m["line_label"] == "CD 级满带宽" and m["delta_khz"] == 0.0
    # 50000 Hz 走 48k 档
    m2 = bandwidth_margin(int(TH.fs_48_container), 23.0e3, TH)
    assert m2["line_label"] == "真 48kHz" and m2["delta_khz"] == 0.0


# ---------------------------------------------------------------- 位深 / 削波 / 响度
def test_bitdepth_requires_both_padded_and_origin():
    """`padded and origin`：只满足一个**不许**报"伪位深"（那是硬断言）。"""
    assert axis_bitdepth({"bit_padded": True}, TH)[0] != "warn"
    assert axis_bitdepth({"bit_origin": "16"}, TH)[0] != "warn"
    assert axis_bitdepth({"bit_padded": True, "bit_origin": "16", "bits": "PCM_24"}, TH)[0] == "warn"


def test_clip_severe_boundary_is_exclusive():
    """`clip > clip_severe_pct`：**正好 0.5%** 不算"严重"（> 不是 >=），只算 warn。"""
    assert axis_level({"clip_pct": TH.clip_severe_pct}, TH)[0] == "warn"
    assert axis_level({"clip_pct": TH.clip_severe_pct + 1e-6}, TH)[0] == "bad"


def test_loud_war_boundary_is_exclusive():
    """`lufs > loud_war_lufs`：正好 −9.0 LUFS **不**报"响度战"。"""
    assert "响度战" not in axis_dynamics({"lufs": TH.loud_war_lufs}, TH)[1]
    assert "响度战" in axis_dynamics({"lufs": TH.loud_war_lufs + 0.1}, TH)[1]


# ---------------------------------------------------------------- 成因层
def test_mid_slope_requires_neither_hard_nor_gentle():
    """`not hard and not gentle`：**平缓滚降**（gentle）时不许冒"中间带"候选。

    变异体把 `and` 改成 `or` 后，gentle 的素材会凭空多出一个
    「有损转码（中间带：不构成硬证据）」候选——那是把"证据不足"当成了"有证据"。
    """
    r = {"cutoff_hz": 12e3, "brickwall_db_per_oct": 8.0,   # < brick_flat(15) ⇒ gentle
         "bands_pct": {"sub": 5.0, "bass": 10.0}}
    keys = [c["key"] for c in cause_candidates(r, 44100, TH)]
    assert "有损转码" not in keys, "平缓滚降不该出「有损转码」候选：%s" % keys


def test_hard_brickwall_with_low_cut_is_the_hard_branch():
    """硬砖墙 + 低截止 ⇒ 走**硬证据**分支（证据里写"≥24 为硬砖墙"，不是"中间带"）。"""
    r = {"cutoff_hz": 12e3, "brickwall_db_per_oct": 30.0,
         "bands_pct": {"sub": 5.0, "bass": 10.0}}
    c = [c for c in cause_candidates(r, 44100, TH) if c["key"] == "有损转码"]
    assert c, "硬砖墙 + 低截止应出「有损转码」候选"
    ev = "；".join(c[0]["evidence"])
    assert "硬砖墙" in ev and "中间带" not in ev, ev


def test_usability_clip_boundaries_are_exclusive():
    """用途矩阵的削波线（`clip_advice_pct` / `clip_pct`）都是 `>`：正好等于时不触发。

    ⚠ 矩阵里每个用途**恒有一格**（默认给"可用"，理由里写着"**无**削波"），
    所以判据看 `level`（触发后是"受限"），不看子串——"无削波"里也有"削波"两字。
    """
    m_at = usability_matrix({"clip_pct": TH.clip_pct}, [], TH)["大音量失真"]
    m_above = usability_matrix({"clip_pct": TH.clip_pct + 1e-6}, [], TH)["大音量失真"]
    assert m_at["level"] == "可用", "正好等于 clip_pct 不该降级：%s" % m_at
    assert m_above["level"] == "受限", "超过 clip_pct 应降级为受限：%s" % m_above


def test_usability_dynamics_needs_both_missing():
    """`lra is None and dr is None`：只缺一个**不许**判"不可判（无动态统计）"。"""
    only_lra = usability_matrix({"lra": 8.0}, [], TH)["动态与微动态"]
    assert "无动态统计" not in str(only_lra), "只缺 DR 就说『无动态统计』是错的：%s" % only_lra
    both = usability_matrix({}, [], TH)["动态与微动态"]
    assert "无动态统计" in str(both)


def test_low_pct_is_sub_plus_bass_not_difference():
    """`low_pct = sub + bass`：低频占比是**求和**，不是差。

    sub=12、bass=8 ⇒ 和 20%（不该判"低频缺失"）；差只有 4% ⇒ 会被误判成缺失。
    """
    cell = usability_matrix({"bands_pct": {"sub": 12.0, "bass": 8.0}}, [], TH)["低频下潜/控制力"]
    assert "低频缺失" not in str(cell), "把 sub−bass 当成低频占比了：%s" % cell


def test_usability_clip_advice_boundary_is_exclusive():
    """`clip > clip_advice_pct`：正好等于建议线时**不**降级动态用途。

    ⚠ 必须同时给 lra/dr：两者都缺时会先落进"不可判（无动态统计）"那条分支，
    就测不到削波这一条了。
    """
    base = {"lra": 8.0, "dr": 12.0}
    at = usability_matrix({**base, "clip_pct": TH.clip_advice_pct}, [], TH)["动态与微动态"]
    above = usability_matrix({**base, "clip_pct": TH.clip_advice_pct + 1e-6}, [], TH)["动态与微动态"]
    assert at["level"] == "可用", "正好等于 clip_advice_pct 不该降级：%s" % at
    assert above["level"] == "不可用", "超过建议线应降级：%s" % above


# ============ 第二轮（2026-10-18）：把上一轮残留的 12 条边界翻转钉死 ============
#
# 背景：变异探针第二轮仍报 12 条**存活**（`verdict.py` 5 条 + `causes.py` 7 条），
# 全是 `>=` / `<` / `and` 这类**边界翻转**。根因与第一轮相同：**这些阈值没被"正好等于"过**。
# 下面这些测试全部走**判定层的纯函数**（`axis_bandwidth` / `cause_candidates`），
# 直接喂"正好落在阈值上"的测量值。⚠ 不改变任何门限，只钉住现有语义（往哪边倒）。

def _res(**kw) -> dict:
    """一个"只有我要的字段"的结果字典（`cause_candidates` 用 `.get`，缺的键当 None）。"""
    return {"cutoff_hz": None, "brickwall_db_per_oct": None, "bands_pct": {},
            "flatness": None, "bit_padded": False, "clip_pct": 0.0,
            "true_peak_dbfs": None, "lra": None, "corr": None, "width_db": None, **kw}


def _keys(r: dict, fs: int) -> list[str]:
    return [c["key"] for c in cause_candidates(r, fs, TH)]


def test_lossy_threshold_is_inclusive_in_44100_branch():
    """44.1k 档：`k >= th.lossy` ⇒ 16.0 kHz **整** 落"有损"档（不是"强有损"）。"""
    lv, txt, _c = axis_bandwidth(44100, TH.lossy * 1000.0, 30.0, TH)
    assert (lv, txt) == ("bad", "有损溯源（≈192–256kbps 转码）"), (lv, txt)


def test_up44_threshold_is_inclusive():
    """48k 档：`k >= th.up_44` ⇒ 正好 21.0 kHz 就判"疑似 44.1k 升频"，不退化成"带宽偏窄"。"""
    lv, txt, _c = axis_bandwidth(48000, TH.up_44 * 1000.0, 30.0, TH)
    assert (lv, txt) == ("bad", "疑似 44.1kHz 升频（内容止于 21.0kHz）"), (lv, txt)


def test_ltd_threshold_is_inclusive_in_48k_branch():
    """48k 档：`k >= th.ltd` ⇒ 正好 19.0 kHz 走"带宽偏窄"，**不许**再往下掉到有损档。"""
    lv, txt, _c = axis_bandwidth(48000, TH.ltd * 1000.0, 30.0, TH)
    assert (lv, txt) == ("warn", "带宽偏窄（19.0kHz）——48k 容器里有损/带限"), (lv, txt)


def test_lossy_threshold_is_inclusive_in_48k_branch():
    """48k 档：`k >= th.lossy` ⇒ 正好 16.0 kHz 是"有损"档，不是"强有损"档。"""
    lv, txt, _c = axis_bandwidth(48000, TH.lossy * 1000.0, 30.0, TH)
    assert (lv, txt) == ("bad", "有损溯源（≈192–256kbps 转码）"), (lv, txt)


def test_conf_low_boundary_counts_as_strong_evidence():
    """`conf < th.conf_low`：正好等于 `conf_low` 时**算证据够**（走硬判那支）。

    ⚠ 判定层里这**同一条件出现两处**（48k 档的"有损"与"强有损"两条支路），两处都要喂到：
    区间 `[lossy, ltd)` 走第一处、`< lossy` 走第二处；只测一处，另一处照样会漏。

    `conf = (slope − conf_slope_lo)/conf_slope_span`，取 `slope = 8 + 16/2 = 16`（都能被
    浮点精确表示）⇒ `conf` 恰好 0.5。若把 `<` 写成 `<=`，或把 `and` 写成 `or`，
    这两条就会从"bad"掉成"warn（措辞软化）"。
    """
    slope = TH.conf_slope_lo + TH.conf_slope_span / 2.0
    assert (slope - TH.conf_slope_lo) / TH.conf_slope_span == TH.conf_low, "构造不出 conf==conf_low"
    lv, txt, conf = axis_bandwidth(48000, 17.0e3, slope, TH)      # [lossy, ltd)：第一处
    assert conf == TH.conf_low, conf
    assert (lv, txt) == ("bad", "有损溯源（≈192–256kbps 转码）"), (lv, txt)
    lv, txt, conf = axis_bandwidth(48000, 15.0e3, slope, TH)      # < lossy：第二处
    assert conf == TH.conf_low, conf
    assert (lv, txt) == ("bad", "强有损溯源（≤128kbps）"), (lv, txt)


def test_hard_brickwall_threshold_is_inclusive():
    """`slope >= th.brick_db`：正好 24 dB/oct **就是**硬砖墙（不是"中间带"）。

    判据看证据文案与 `ambiguous_with`：退化后会变成"中间带：不构成硬证据"并并列"刻意带限"。
    """
    c = [x for x in cause_candidates(_res(cutoff_hz=18.0e3,
                                          brickwall_db_per_oct=TH.brick_db), 44100, TH)
         if x["key"] == "有损转码"]
    assert c, "低截止 + 正好 24 dB/oct 应出「有损转码」"
    assert "硬砖墙" in c[0]["evidence"][1] and c[0]["ambiguous_with"] == [], c[0]


def test_brick_flat_threshold_is_exclusive():
    """`slope < th.brick_flat_db_per_oct`：正好 15 dB/oct **不算**平缓滚降。

    这一条同时管住 `causes.py` 里的两处（`gentle` 与 `gentle_rolloff`，都在判 `<15`）：
    退化后"有损转码（中间带）"会消失、"磁带转录"会凭空多出来。
    """
    keys = _keys(_res(cutoff_hz=18.0e3, brickwall_db_per_oct=TH.brick_flat_db_per_oct), 44100)
    assert "有损转码" in keys, "正好 15 dB/oct 仍属中间带，应出「有损转码」：%s" % keys
    assert "磁带转录" not in keys, "正好 15 dB/oct 不该算平缓滚降：%s" % keys


def test_noise_like_flatness_threshold_is_inclusive():
    """`flat >= th.noise_like_flatness`：正好 0.5 **算**噪声状 ⇒ 占比类候选被守卫拦住。"""
    r = _res(cutoff_hz=None, bands_pct={"sub": 20.0, "bass": 10.0},
             flatness=TH.noise_like_flatness)
    assert "黑胶转录" not in _keys(r, 44100), "正好 0.5 应算噪声状，低频隆隆占比不算证据"


def test_vinyl_rumble_threshold_is_inclusive():
    """`low_pct >= th.vinyl_rumble_pct`：正好 25% **算**低频隆隆（非噪声状时出黑胶候选）。"""
    r = _res(bands_pct={"sub": 20.0, "bass": 5.0}, flatness=0.1)
    assert "黑胶转录" in _keys(r, 44100), "sub+bass 正好 25% 应算隆隆"


def test_upsample_fs_threshold_is_exclusive():
    """`fs > th.upsample_fs`：容器正好 88.2 kHz **不算**"超 48k"⇒ 不出升频候选。"""
    assert "升频" not in _keys(_res(cutoff_hz=20.0e3), int(TH.upsample_fs)), "88.2k 容器不该出升频"
    assert "升频" in _keys(_res(cutoff_hz=20.0e3), int(TH.upsample_fs) + 2)


def test_clip_pct_threshold_is_exclusive():
    """`clip > th.clip_pct`：正好等于告警线**不**出"母带限幅"（与用途矩阵同口径）。"""
    r = _res(clip_pct=TH.clip_pct, true_peak_dbfs=0.3)
    assert "母带限幅" not in _keys(r, 44100), "正好等于 clip_pct 不该出母带限幅候选"
    assert "母带限幅" in _keys(_res(clip_pct=TH.clip_pct + 1e-6, true_peak_dbfs=0.3), 44100)
