"""C4 判定颗粒度回归：三层输出（现象/成因/用途）+ 时间定位 + 效力边界。

用户 2026-10-13 原话："判决颗粒度一定要根据现实音源复杂情况极致真实丰富，
现在太粗糙了。"本文件钉住 C4 的五条硬规矩：

1. **证据不足不硬判**（C4.6）：砖墙陡度不足时，带限类判定只能 warn 不能 bad；
2. **成因并列不压平**（C4.1）：证据不足时多个候选并列 + "无法区分"标记，
   不许压成一个结论；标"工程估计"的一律带"疑似"；
3. **用途逐格带依据**（C4.2）：十个用途每格非空且带一句依据；结像明确"不可判"；
4. **无总分**（C4.4 / out-of-scope 5）：输出结构里不存在单一综合分数；
5. **时间定位与整轨并列**（C4.3）：问题段定位到段；段数不够时明说不下结论。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.config import Thresholds  # noqa: E402
from audiocheck.core.analyzers.causes import USES, cause_candidates, usability_matrix  # noqa: E402
from audiocheck.core.analyzers.verdict import axis_bandwidth  # noqa: E402
from audiocheck.core.engine import analyze  # noqa: E402

TH = Thresholds()


# ==================================================== C4.6 conf 进入决策
class TestConfEntersDecision:
    """"砖墙陡度不足（conf 低）时不得判 bad，只能 warn"——证据不足不硬判。"""

    def test_soft_rolloff_downgrades_lossy_to_warn(self):
        lv, txt, conf = axis_bandwidth(44100, 17000, 5.0, TH)
        assert lv == "warn", "平缓滚降 + 低截止被判成了 %s：%s" % (lv, txt)
        assert "疑似" in txt and "证据不足" in txt
        assert conf is not None and conf < TH.conf_low

    def test_soft_rolloff_strong_bandlimit_also_warn(self):
        lv, txt, _ = axis_bandwidth(44100, 12000, 5.0, TH)
        assert lv == "warn" and "强带限" in txt

    def test_hard_brickwall_still_judges_bad(self):
        """反向保护：陡砖墙仍然判 bad（C4.6 不是把有损判定全抹掉）。"""
        lv, _, conf = axis_bandwidth(44100, 17000, 113.0, TH)
        assert lv == "bad" and conf is not None and conf >= TH.conf_low

    def test_missing_slope_keeps_old_behavior(self):
        """slope=None（未测到）时**不改旧行为**——最小改动只动"有数据但证据弱"。

        要不要把 None 也降级是另一个决定（"查不到"的处理属 C4.5 的范围），
        这里钉住本轮口径，防止顺手扩大。
        """
        lv, _, conf = axis_bandwidth(44100, 17000, None, TH)
        assert lv == "bad" and conf is None

    def test_natural_rolloff_file_not_judged_bad(self, audio):
        r = analyze(audio["natural_rolloff"])
        assert r["level"] != "bad", "自然滚降被硬判成有损：%s" % r["verdict"]


# ==================================================== C4.1 成因候选
class TestCauseCandidates:
    def test_lossy_candidate_has_enumerable_evidence(self, audio):
        r = analyze(audio["lossy_16k"])
        c = _pick(r["causes"], "有损转码")
        assert any("截止" in e for e in c["evidence"])
        assert any("砖墙" in e for e in c["evidence"])
        assert c["strength"] >= TH.conf_low

    def test_upsample_candidate_on_fake_hires(self, audio):
        r = analyze(audio["fake_hires"])
        keys = {c["key"] for c in r["causes"]}
        assert "升频" in keys, keys

    def test_bit_padded_is_hard_evidence(self):
        r = {"cutoff_hz": 21000, "bit_padded": True, "bit_origin": 16, "bits": "PCM_24"}
        c = _pick(cause_candidates(r, 44100, TH), "位深填充")
        assert c["strength"] >= 0.9
        assert any("16" in e for e in c["evidence"])

    def test_vinyl_and_tape_listed_together_when_undecidable(self):
        """隆隆 + 平缓滚降 + 音乐形谱 ⇒ 黑胶/磁带并列，标记"无法区分"。"""
        r = {"cutoff_hz": 16000, "brickwall_db_per_oct": 9.0, "flatness": 0.05,
             "bands_pct": {"sub": 18.0, "bass": 12.0, "mid": 30.0, "high": 30.0}}
        cands = cause_candidates(r, 44100, TH)
        keys = {c["key"] for c in cands}
        assert {"黑胶转录", "磁带转录"} <= keys, keys
        for c in cands:
            if c["key"] in ("黑胶转录", "磁带转录"):
                assert c["ambiguous"], "并列候选必须标记无法区分"
                assert "工程估计" in c["prov"]

    def test_mid_slope_lists_lossy_and_deliberate_together(self):
        """中间带陡度（15–24）：有损转码与刻意带限并列，谁也不硬判。"""
        r = {"cutoff_hz": 17000, "brickwall_db_per_oct": 19.0, "flatness": 0.05,
             "noise_floor_db": -90.0}
        keys = {c["key"] for c in cause_candidates(r, 44100, TH)}
        assert {"有损转码", "刻意带限"} <= keys, keys
        for c in cause_candidates(r, 44100, TH):
            if c["key"] in ("有损转码", "刻意带限"):
                assert c["ambiguous"]

    def test_noise_like_content_has_no_share_based_candidates(self):
        """类噪守卫：白噪（flatness≈0.99）不许产出**占比类**候选（隆隆/低频缺失）。

        实测踩过：给白噪 demo 判出"疑似磁带转录"——当时候选证据是"底噪抬升"，
        而白噪的"底噪"就是信号本身。C4 修订后底噪证据已整体移除（该度量随
        节目电平缩放、不可靠），守卫收窄到占比类候选；截止/滚降类证据对
        噪声状内容同样成立，**不**守卫（砖墙白噪正是有损判定的标准素材）。
        """
        r = {"cutoff_hz": 19000, "brickwall_db_per_oct": 9.0, "flatness": 0.99,
             "noise_floor_db": -37.0, "bands_pct": {"sub": 0.3, "bass": 1.0}}
        keys = {c["key"] for c in cause_candidates(r, 44100, TH)}
        assert "黑胶转录" not in keys and "广播/电台版" not in keys

    def test_ai_generated_not_listed(self):
        """AI 生成音频未取到出处 ⇒ 不列（诚实：不猜）。"""
        keys = {c["key"] for c in cause_candidates({"cutoff_hz": 21000}, 44100, TH)}
        assert not any("AI" in k or "生成" in k for k in keys)

    def test_candidate_without_evidence_is_absent(self):
        assert cause_candidates({"cutoff_hz": 22050}, 44100, TH) == []


# ==================================================== C4.2 用途矩阵
class TestUsabilityMatrix:
    def test_matrix_complete_with_reasons(self, audio):
        u = analyze(audio["cd_full"])["usability"]
        assert tuple(u) == USES
        for name, cellv in u.items():
            assert cellv["level"] in ("可用", "受限", "不可用", "不可判"), (name, cellv)
            assert cellv["reason"].strip(), "%s 格没有依据" % name

    def test_imaging_is_explicitly_unjudgeable(self, audio):
        cellv = analyze(audio["cd_full"])["usability"]["结像"]
        assert cellv["level"] == "不可判" and "HRTF" in cellv["reason"]

    def test_transient_is_limited_by_window(self, audio):
        cellv = analyze(audio["cd_full"])["usability"]["瞬态"]
        assert cellv["level"] == "受限" and "93" in cellv["reason"]

    def test_hf_cell_graded_by_cutoff(self):
        def lvl(cut):
            return usability_matrix({"cutoff_hz": cut}, [], TH)["频响延伸/空气感"]["level"]
        assert lvl(21000) == "可用"
        assert lvl(18000) == "受限"
        assert lvl(12000) == "不可用"
        assert lvl(None) == "不可判"

    def test_unknown_is_a_formal_value(self):
        """缺数据 ⇒ "不可判"，不许默认成"可用"（C0-4 同型规矩）。"""
        u = usability_matrix({"cutoff_hz": 21000}, [], TH)   # 无 lra/dr/width/bands
        assert u["动态与微动态"]["level"] == "不可判"
        assert u["声场宽度"]["level"] == "不可判"
        assert u["音色平衡"]["level"] == "不可判"

    def test_advice_renders_from_matrix(self, audio):
        """建议与矩阵同源：受限/不可用格的理由必须出现在 advice 里。"""
        r = analyze(audio["clipped"])
        for name, cellv in r["usability"].items():
            if name in ("结像", "瞬态", "音色平衡", "整体可信度"):
                continue
            if cellv["level"] in ("受限", "不可用"):
                assert cellv["reason"] in r["advice"], (name, cellv["reason"])


# ==================================================== C4.3 时间定位
class TestTimeline:
    def test_problem_segment_is_located(self, tmp_path):
        """前 5s 正常 + 后 5s 削波 ⇒ 问题段定位到后段且给出比例。"""
        import soundfile as sf
        fs = 44100
        rng = np.random.default_rng(7)
        clean = (rng.standard_normal(fs * 5) * 0.2).astype(np.float32)
        hot = np.clip(rng.standard_normal(fs * 5) * 6.0, -1.0, 1.0).astype(np.float32)
        p = tmp_path / "halftone.wav"
        sf.write(str(p), np.column_stack([np.concatenate([clean, hot])] * 2),
                 fs, subtype="PCM_16")
        r = analyze(str(p), thresholds=Thresholds(seg_seconds=2.0, seg_overlap=0.5,
                                                  seg_min_count=4))
        probs = r["timeline_problems"]
        assert probs, "后 5s 削波没有被定位出来：%s" % r.get("timeline_note")
        assert all(q["start"] >= 2.5 for q in probs), [q["span"] for q in probs]
        assert any(q["clip_pct"] > 0.5 for q in probs)
        assert r["verdict"], "整轨结论必须保留（与问题段并列，不是替代）"

    def test_short_file_refuses_to_conclude(self, tmp_path):
        """段数少于门限 ⇒ note 明说"不下分段结论"（小样本不许硬判）。"""
        import soundfile as sf
        fs = 44100
        x = (np.random.default_rng(3).standard_normal(fs * 6) * 0.2).astype(np.float32)
        p = tmp_path / "short.wav"
        sf.write(str(p), np.column_stack([x, x]), fs, subtype="PCM_16")
        r = analyze(str(p))          # 默认 10s 窗 ⇒ 一段都取不出
        assert r["timeline"] == [] and "无法分段" in r["timeline_note"]

    def test_timeline_uses_time_domain_only(self, tmp_path):
        """逐段只做时域统计——note 必须如实写"逐段频谱未做"（不许假装测了）。"""
        import soundfile as sf
        fs = 48000
        x = (np.random.default_rng(5).standard_normal(fs * 40) * 0.2).astype(np.float32)
        p = tmp_path / "long.wav"
        sf.write(str(p), np.column_stack([x, x]), fs, subtype="PCM_16")
        r = analyze(str(p))
        assert r["timeline"] and "逐段频谱未做" in r["timeline_note"]


# ==================================================== C4.4/4.5 结构与边界
class TestStructureAndBoundaries:
    def test_three_layers_present(self, audio):
        r = analyze(audio["cd_full"])
        for k in ("phenomena", "causes", "usability", "boundaries", "timeline"):
            assert k in r, "三层输出缺 %s" % k
        ph = r["phenomena"]
        assert isinstance(ph, list) and len(ph) == 5
        for p in ph:
            assert {"axis", "value", "level", "text", "drives_level"} <= set(p)

    def test_no_composite_score(self, audio):
        """无总分：输出结构里不许出现单一综合分数（out-of-scope 第 5 条）。"""
        r = analyze(audio["cd_full"])
        banned = ("score", "总分", "综合分", "rating", "grade")
        for k in r:
            assert not any(b in str(k).lower() for b in banned), k
        for c in r["causes"]:
            assert isinstance(c["strength"], float), "候选必须带强度"

    def test_verdict_line_not_flattened_with_pipe(self, audio):
        """判定行不许用"｜"把多个现象糊成一行（C4.4）。"""
        assert "｜" not in analyze(audio["cd_full"])["verdict"]

    def test_boundaries_present(self, audio):
        b = analyze(audio["cd_full"])["boundaries"]
        assert b and any("结像" in x for x in b)
        assert any("93" in x for x in b)


# ==================================================== 逐声道数值
def test_stereo_width_number_is_correct(tmp_path):
    """L/R 增益差已知 ⇒ 宽度数值正确（M/S 能量比，±量化噪声内）。"""
    import soundfile as sf
    fs = 44100
    x = (np.random.default_rng(11).standard_normal(fs * 4) * 0.2).astype(np.float32)
    p = tmp_path / "half.wav"
    sf.write(str(p), np.column_stack([x, x * 0.5]), fs, subtype="PCM_16")
    r = analyze(str(p))
    # mid = 0.75·x, side = 0.25·x ⇒ width = 10·log10(0.25²/0.75²) = -9.54 dB
    assert abs(r["width_db"] - (-9.54)) < 0.35, r["width_db"]


def _pick(causes, key):
    for c in causes:
        if c["key"] == key:
            return c
    raise AssertionError("候选集里没有 %r：%s" % (key, [c["key"] for c in causes]))
