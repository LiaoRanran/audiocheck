"""判定层的**边界与文案**判据 —— 补 2026-10-20 整体变异复跑暴露的 31 条缺口。

这 31 条存活**全部是同一类形态**：`>` / `>=` 的**等号侧**、`or` / `and` 的**短路顺序**、
以及格式化字符串里的 `1000` / `100`（只进文案，但文案是**用户唯一看得到的东西**）。
它们没被测到，是因为既有用例都取在门限**中间**，没有一条落在**恰好等于门限**上。

所以本文件的口径是：**每条判据都取在等号上**（或只差一个最小可表示量），
并在注释里写清"改了会怎样"——这样将来有人动这些比较符，测试会替我们说话。
"""
from __future__ import annotations

from audiocheck.config import Thresholds
from audiocheck.core.analyzers.verdict import (
    advice,
    axis_bandwidth,
    axis_bitdepth,
    axis_channels,
    axis_dynamics,
    axis_level,
    cross_checks,
    usability_matrix,
)

TH = Thresholds()


# ============================================================ 等号侧：各轴门限
def test_bit_gap_warns_exactly_at_the_threshold():
    """`cl - eff >= bit_gap_warn`：**恰好差 4 bit 也就是"有效位深不足"**（等号在 warn 侧）。

    改 `>=` 为 `>` ⇒ 差 4 bit（16 标称 / 12 有效，最常见的"8 位补齐"少 4 位）
    会被判 ok，正是这条判据要防的静默降级。
    """
    gap = TH.bit_gap_warn
    lv, text = axis_bitdepth({"bits": "PCM_16", "bit_effective": 16 - gap}, TH)
    assert lv == "warn" and "有效位深" in text
    lv2, _ = axis_bitdepth({"bits": "PCM_16", "bit_effective": 16 - gap + 1}, TH)
    assert lv2 == "ok", "差 %d bit 已不该告警" % (gap - 1)


def test_clip_pct_threshold_is_strict_at_the_boundary():
    """`clip > clip_pct`：**恰好等于门限不算"存在削波"**（等号在 ok 侧）。

    改 `>` 为 `>=` ⇒ 一份"正好 0.5% 采样被削"的文件会凭空多出一条告警。
    """
    lv, text = axis_level({"clip_pct": TH.clip_pct}, TH)
    assert lv == "ok" and text == ""
    lv2, text2 = axis_level({"clip_pct": TH.clip_pct * 1.0001}, TH)
    assert lv2 == "warn" and "存在削波" in text2


def test_true_peak_at_exactly_zero_does_not_raise_a_message():
    """`tp > 0.0`：**0.00 dBTP 不是"过载"**（等号在无提示侧）。

    改 `>` 为 `>=` ⇒ 每一份采样峰值正好 0 dBFS 的母带都会多一句
    "采样间过载 +0.00 dBTP"——那正是 `docs/theory.md §4` 说的**把播放链问题
    误报成文件问题**。
    """
    _, text = axis_level({"clip_pct": 0.0, "true_peak_dbfs": 0.0}, TH)
    assert "采样间过载" not in text
    _, text2 = axis_level({"clip_pct": 0.0, "true_peak_dbfs": 0.01}, TH)
    assert "采样间过载" in text2


def test_lra_flat_threshold_is_strict():
    """`lra < lra_flat`：**恰好等于 3.0 LU 不算压平**（等号在 ok 侧）。"""
    assert axis_dynamics({"lra": TH.lra_flat}, TH)[0] == "ok"
    assert axis_dynamics({"lra": TH.lra_flat - 0.001}, TH)[0] == "warn"


def test_dr_low_threshold_is_strict():
    """`dr < dr_low_db`：**恰好等于 6.0 dB 不算偏低**（等号在 ok 侧）。"""
    assert axis_dynamics({"dr_db": TH.dr_low_db}, TH)[0] == "ok"
    assert axis_dynamics({"dr_db": TH.dr_low_db - 0.001}, TH)[0] == "warn"


def test_width_narrow_threshold_is_strict():
    """`wd < width_narrow_db`：**恰好 −18.0 dB 不算"近乎单声道"**（等号在 ok 侧）。"""
    assert axis_channels({"width_db": TH.width_narrow_db}, TH)[0] == "ok"
    assert axis_channels({"width_db": TH.width_narrow_db - 0.001}, TH)[0] == "warn"


def test_brick_db_threshold_counts_as_hard_wall():
    """`brickwall_db_per_oct >= brick_db`：**恰好 24 dB/oct 就是硬砖墙**（等号在硬侧）。

    那是 `docs/theory.md §11.2` 给"硬证据"的端点；把等号划错会让端点值
    落进"平缓"文案，等于自我否认定义。
    """
    _, text, _ = axis_bandwidth(44100, 19000, TH.brick_db, TH)
    assert "砖墙" in text and "陡" in text, text
    _, text2, _ = axis_bandwidth(44100, 19000, TH.brick_db - 0.001, TH)
    assert "滚降平缓" in text2


def test_conf_low_threshold_counts_as_strong_evidence():
    """`conf < conf_low`：**恰好等于 0.5 算"证据够"**（等号在强证据侧 ⇒ 可判 bad）。"""
    # conf = 0.5 ⇔ 斜率 = lo + span/2 = 16 dB/oct
    lv, text, conf = axis_bandwidth(44100, 17000, 16.0, TH)
    assert conf == 0.5
    assert lv == "bad" and "有损溯源" in text, "conf==conf_low 被判成了软文案：%r" % text
    lv2, text2, _ = axis_bandwidth(44100, 17000, 15.9, TH)
    assert lv2 == "warn" and "疑似" in text2


def test_soft_band_text_reports_the_actual_slope_value():
    """`slope or 0.0`：**有斜率时文案必须打实测值**（否则用户看不到证据强度）。

    改 `or` 为 `and` ⇒ 恒打 `0dB/oct`（`x and 0.0` 在 x 真值时得 0.0）。
    """
    _, text, _ = axis_bandwidth(44100, 17000, 12.3, TH)
    assert "12dB/oct" in text, text


# ============================================================ 交叉核对：等号侧与除数
def test_upsample_content_threshold_is_inclusive_at_the_top():
    """`0 < k <= upsample_content_khz`：**恰好等于上限也算"内容止于此"**（等号在触发侧）。"""
    k = TH.upsample_content_khz
    assert any("采样率造假" in s for s in cross_checks({"format": "FLAC"}, 96000, k * 1000, TH))
    # 再高 1 Hz 就不该再触发
    assert not any("采样率造假" in s for s in
                   cross_checks({"format": "FLAC"}, 96000, k * 1000 + 1.0, TH))


def test_upsample_container_threshold_is_strict():
    """`fs > upsample_fs`：**恰好 88.2k 容器不算"高采样率升频"**（等号在 ok 侧）。"""
    assert not any("采样率造假" in s
                   for s in cross_checks({"format": "FLAC"}, TH.upsample_fs, 20000.0, TH))
    assert any("采样率造假" in s
               for s in cross_checks({"format": "FLAC"}, TH.upsample_fs + 100, 20000.0, TH))


def test_cutoff_low_and_flat_slope_boundaries_are_strict():
    """两条都是 `<`：**恰好 20.0 kHz / 恰好 15 dB/oct 都不算"证据不足"**。

    ⚠ 两条必须**分开**取在等号上：若同时取等号，任一条被改成 `<=` 时另一条
    仍是 False，条件整体不变 ⇒ 两个变异体都杀不掉（上一版就是这样漏的）。
    """
    r = {"format": "FLAC", "brickwall_db_per_oct": TH.brick_flat_db_per_oct}
    assert not any("证据不足" in s
                   for s in cross_checks(r, 44100, TH.cutoff_low_khz * 1000, TH))
    # 只让"截止点"落在等号上（滚降取平缓值 ⇒ 另一条为真）
    r2 = {"format": "FLAC", "brickwall_db_per_oct": TH.brick_flat_db_per_oct - 5.0}
    assert not any("证据不足" in s
                   for s in cross_checks(r2, 44100, TH.cutoff_low_khz * 1000, TH))
    # 只让"滚降"落在等号上（截止点取低值 ⇒ 另一条为真）
    r3 = {"format": "FLAC", "brickwall_db_per_oct": TH.brick_flat_db_per_oct}
    assert not any("证据不足" in s
                   for s in cross_checks(r3, 44100, (TH.cutoff_low_khz - 1.0) * 1000, TH))
    r4 = {"format": "FLAC", "brickwall_db_per_oct": TH.brick_flat_db_per_oct - 0.1}
    assert any("证据不足" in s
               for s in cross_checks(r4, 44100, (TH.cutoff_low_khz - 0.1) * 1000, TH))


def test_hires_branch_texts_carry_the_numbers():
    """Hi-Res 三条分支的文案：`fs/1000`、`cutoff/nyq*100` 的除数与百分号都要对。

    * 伪 Hi-Res：**96k 容器写 96.0kHz**（÷1000；÷1001 会印 0.1，×1000 会印天文数字）；
    * 真 Hi-Res：41.9k/48k = 87.29% ⇒ "87%"（×101 会变 88%，×/ 会变 0%）；
    * 半 Hi-Res：容器同样按 kHz 渲染。
    """
    level, text, _conf = axis_bandwidth(96000, 20000.0, 113.0, TH)
    assert level == "bad" and "伪 Hi-Res" in text
    assert "96.0kHz" in text and "20.0kHz" in text, text
    level, text, _conf = axis_bandwidth(96000, 41900.0, 113.0, TH)
    assert level == "ok" and "真 Hi-Res" in text
    assert "41.9kHz" in text and "87%" in text, text
    level, text, _conf = axis_bandwidth(96000, 40000.0, 113.0, TH)
    assert level == "warn" and "半 Hi-Res" in text
    assert "40.0kHz" in text and "96.0kHz" in text, text


def test_weak_evidence_gate_is_strict_at_conf_half():
    """`conf < conf_low`：conf **恰好 0.5** 时算"证据够"⇒ 仍判 bad（不是 warn）。

    conf = (slope − 8)/16，取 slope = 16.0 即得逐位 0.5（8/16 是二的幂）。
    截止 15 kHz（< lossy 16）走"强有损"那一档：`<` 与 `<=` 在这里分叉。
    """
    level, text, conf = axis_bandwidth(44100, 15000.0, 16.0, TH)
    assert conf == 0.5
    assert level == "bad" and "强有损溯源" in text, text


def test_advice_tolerates_a_missing_usability_cell():
    """`if not c or not c.get(...)`：**缺格**时必须短路跳过（`and` 会去取 `None["reason"]`）。

    `advice` 允许调用方传入**不完整**的 `usability`（UI 逐格渲染时会这么做），
    所以 `not c` 必须能独立短路。
    """
    assert advice({"usability": {"频响延伸/空气感": None}}, TH) == ""
    # 注意 `{}` 是**假值**：会走 `or usability_matrix(...)` 的兜底，不能当"缺格"用例。


def test_no_axis_text_is_appended_when_every_axis_is_ok():
    """`if t and lv != "ok"` 的两半各有一个变异，且**要两个方向各测一次**。

    ① `and`→`or`：**等级是 ok 但文案非空**的轴会被追加（`axis_dynamics` 的"响度战"
       提示不改变等级 ⇒ 正是这种格子）；
    ② `!=`→`==`：等级**非 ok** 的轴的文案会被丢掉（削波提示消失）。
    只看"全 ok 时没有；；"抓不到这两条（ok 轴的文案通常是空串）。
    """
    from audiocheck.core.analyzers.verdict import VerdictAnalyzer

    def run(**over):
        res = {"peak_dbfs": -3.0, "cutoff_hz": 22000.0, "brickwall_db_per_oct": 113.0,
               "bit_effective": 16, "bits": "PCM_16", "clip_pct": 0.0,
               "lra": 10.0, "dr_db": 12.0, "width_db": -5.0}
        res.update(over)

        class _Ctx:
            deep = True
            fs = 44100
            thresholds = TH
            result = res

        return VerdictAnalyzer().run(_Ctx())

    assert "；" not in run()["verdict"], run()["verdict"]
    # ① 等级仍 ok、但动态轴有"响度战"提示 ⇒ 不许因为等级 ok 就扔掉，也不许无条件追加
    war = run(lufs=-5.0)["verdict"]
    assert "响度战" not in war, war
    # ② 电平轴降级（削波）⇒ 文案**必须**进结论（`!=`→`==` 会吞掉它）
    assert "削波" in run(clip_pct=0.6)["verdict"]


def test_cross_check_khz_conversion_is_pinned_by_a_case_that_straddles_it():
    """`k = cutoff_hz / 1000`：取 **30 kHz**——除以 1500 会变成 20（低于上限 ⇒ 误报升频），
    除以 1000 才是 30（高于上限 ⇒ 正确地沉默）。这一条把除数钉死。"""
    assert not any("采样率造假" in s
                   for s in cross_checks({"format": "FLAC"}, 96000, 30000.0, TH))


def test_cross_check_texts_carry_the_real_numbers():
    """文案里的数字也是口径：`fs/1000`、`k` 必须按 kHz 渲染（改 `/`→`*` 会印出天文数字）。"""
    ups = [s for s in cross_checks({"format": "FLAC"}, 96000, 20000.0, TH) if "升频" in s][0]
    assert "96.0kHz" in ups and "20.0kHz" in ups, ups
    padded = [s for s in cross_checks({"bits": "PCM_24", "bit_padded": True,
                                       "bit_origin": 16}, 44100, 20000.0, TH)
              if "位深造假" in s][0]
    assert "24" in padded and "16" in padded, padded


def test_bitdepth_text_without_a_note_and_without_claimed_bits():
    """`bit_note or ""` 与 `bits or ""`：两者都**不许把 None 印进文案**。"""
    _, text = axis_bitdepth({"bits": "PCM_24", "bit_padded": True, "bit_origin": 16}, TH)
    assert "None" not in text
    _, text2 = axis_bitdepth({"bit_padded": True, "bit_origin": 16}, TH)   # bits 缺失
    assert "None" not in text2, text2


def test_bitdepth_note_is_preferred_when_present():
    """有注释时文案必须带上它（改 `or ""` 为 `and ""` 会把注释吞掉）。"""
    _, text = axis_bitdepth({"bits": "PCM_24", "bit_padded": True, "bit_origin": 16,
                             "bit_note": "低 8 位恒零"}, TH)
    assert "低 8 位恒零" in text, text


# ============================================================ 聚合文案的三处短路
def test_axis_texts_with_empty_text_are_not_appended():
    """`if t and lv != "ok"`：**没有文案的轴不追加**（改 `and`→`or` 会追加空串）。

    口徑用**两条**同时测：ok 轴有文案 ⇒ 不追加；非 ok 轴没文案 ⇒ 也不追加。
    """
    from audiocheck.core.analyzers.verdict import VerdictAnalyzer

    class _Ctx:
        deep = True
        fs = 44100
        thresholds = TH
        result = {"peak_dbfs": -3.0, "cutoff_hz": 22000.0, "brickwall_db_per_oct": 113.0,
                  "bit_effective": 16, "bits": "PCM_16", "clip_pct": 0.0,
                  "lra": 10.0, "dr_db": 12.0, "width_db": -5.0}

    out = VerdictAnalyzer().run(_Ctx())
    assert "；；" not in out["verdict"], out["verdict"]
    assert out["verdict"] == out["verdict"].strip()


def test_bandwidth_ok_prefix_only_when_another_axis_downgrades():
    """`if bw_lv == "ok" and level != "ok"`：三条短路规则各测一个方向。

    * 带宽 ok + 别处降级 ⇒ **必须**有前缀（`!=` → `==` 会让它永不出现）；
    * 带宽 ok + 全都 ok ⇒ **不许**有前缀（`and` → `or` 会让它凭空出现）；
    * 带宽本身非 ok ⇒ 不写"带宽无问题"（`==` → `!=` 会让它反过来出现）。
    """
    from audiocheck.core.analyzers.verdict import VerdictAnalyzer

    def run(**over):
        res = {"peak_dbfs": -3.0, "cutoff_hz": 22000.0, "brickwall_db_per_oct": 113.0,
               "bit_effective": 16, "bits": "PCM_16", "clip_pct": 0.0,
               "lra": 10.0, "dr_db": 12.0, "width_db": -5.0}
        res.update(over)

        class _Ctx:
            deep = True
            fs = 44100
            thresholds = TH
            result = res

        return VerdictAnalyzer().run(_Ctx())

    assert "带宽无问题" in run(clip_pct=0.6)["verdict"]
    assert "带宽无问题" not in run()["verdict"]
    assert "带宽无问题" not in run(cutoff_hz=17000.0)["verdict"]


def test_advice_never_emits_stray_spaces_from_empty_reasons():
    """`if not c or not c.get("reason")`：**没有理由的格子不许产生多余空格**。

    改 `or` 为 `and` ⇒ 空理由的格子会被追加成空串，`" ".join` 就多出连续空格。
    """
    r = {"cutoff_hz": 22000.0, "brickwall_db_per_oct": 113.0, "clip_pct": 0.0,
         "lra": 10.0, "dr_db": 12.0, "width_db": -5.0}
    u = usability_matrix(r, [], TH)
    text = advice({**r, "usability": u}, TH)
    assert "  " not in text and text == text.strip(), repr(text)
