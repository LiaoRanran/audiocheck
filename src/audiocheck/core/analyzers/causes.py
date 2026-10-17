"""C4.1 成因候选层 —— 现象 → 成因候选集（每个候选带证据、反证、强度）。

## 为什么单独一个模块（不放 verdict.py）

C0 的白名单判据规定 `verdict.py` 里**只许结构性数字**（`test_no_threshold_
lives_in_the_judgment_layer`）。本模块的候选强度粗档（0.5/0.8/0.9）是
**算法参数**（检测分数性质），按白名单自己的分类落在这里登记，verdict.py 保持干净。

## 三条不可违反的规矩（C 块提示词 C4.1）

1. **证据不足时并列多个候选**并标明"无法区分"，**不许硬判一个**；
2. 标"工程估计"的候选 ⇒ 用户可见处带"疑似"（无出处 ≠ 可当证据）；
3. **AI 生成音频未取到出处 ⇒ 不列**（诚实：不猜）。

## 强度（strength）口径

* 砖墙类证据：直接复用带宽轴的 `conf`（由砖墙陡度映射，可复算）；
* 非 conf 派生的是**粗档工程估计**：0.9 = 位级硬证据 / 0.8 = 容器级失配 /
  0.5 = 统计吻合。粗档不是测量值，文案里永远带"疑似"。
"""
from __future__ import annotations

from ...config import BAND_CN
from ..registry import Analyzer, register

#: 候选的**出处状态**（照 C4.1 表第 4 列）。与 `config.PROVENANCE` 的同名门限
#: 条目一致；用户可见文案必须与这个表一致（标"工程估计"的一律带"疑似"）。
_CAUSE_PROV = {
    "有损转码": "工程估计（编码参数识别有同行评审方法 Kim&Rafii EUSIPCO'18；判据门限是本项目取值）",
    "黑胶转录": "工程估计（无同行评审来源）",
    "磁带转录": "工程估计（无同行评审来源）",
    "刻意带限": "工程估计（无同行评审来源）",
    "升频": "有学术出处（NYCU 2017；学位论文，未独立复现）",
    "位深填充": "位级判据，可作证据",
    "母带限幅": "工程估计（现象有同行评审依据 Croghan JASA 2012；判据门限是本项目取值）",
    "广播/电台版": "工程估计（无同行评审来源）",
    "假立体声": "工程估计（无同行评审来源）",
}


def cause_candidates(r: dict, fs: int, th) -> list[dict]:
    """从扁平结果里挑成因候选。返回 [{key, strength, evidence[], counter[],
    prov, ambiguous_with[], ambiguous}]。

    **没有可枚举证据的候选不出现在输出里**——"可能有"不是证据。
    """
    k = (r.get("cutoff_hz") or 0) / 1000.0
    slope = r.get("brickwall_db_per_oct")
    hard = slope is not None and slope >= th.brick_db             # 硬砖墙
    gentle = slope is not None and slope < th.brick_flat_db_per_oct
    # 中间带（平缓~硬之间）：既非硬证据也非反证 ⇒ 证据不足，并列候选
    mid_slope = slope is not None and not hard and not gentle
    low_cut = 0 < k < th.cutoff_low_khz        # 截止点落在"有损级"区间
    bandlimited = 0 < k < th.cd_full           # 内容够不到 CD 满带宽

    bands = r.get("bands_pct") or {}
    low_pct = float(bands.get("sub", 0.0)) + float(bands.get("bass", 0.0))
    # **类噪守卫**：噪声状频谱（Welch 谱实测 flatness≈0.99）没有音乐式的低音
    # 结构 ⇒ "低频隆隆/低频缺失"这类**占比证据**在它上面名不副实
    # （实测踩过：给白噪 demo 判出"疑似磁带转录"——它的"底噪"就是信号本身）。
    # ⚠ 守卫只管占比类候选；截止/砖墙类证据对噪声状内容同样成立（有损判定的
    #   标准测试素材就是砖墙白噪），**不**守卫。
    flat = r.get("flatness")
    noise_like = flat is not None and flat >= th.noise_like_flatness
    rumble = low_pct >= th.vinyl_rumble_pct and not noise_like
    # 高频平缓滚降（磁带/刻意带限共享的证据）：检出截止 + 滚降平缓 + 无硬砖墙
    gentle_rolloff = bandlimited and slope is not None and not hard and slope < th.brick_flat_db_per_oct
    corr, wd = r.get("corr"), r.get("width_db")

    out: list[dict] = []

    def add(key, strength, evidence, counter=(), ambiguous_with=()):
        out.append({"key": key, "strength": round(float(strength), 2),
                    "evidence": list(evidence), "counter": list(counter),
                    "prov": _CAUSE_PROV.get(key, "工程估计"),
                    "ambiguous_with": list(ambiguous_with)})

    # 1) 有损转码：截止落有损区间 **且** 有硬砖墙（= 上游低通的硬证据）
    if low_cut and hard:
        conf = max(0.0, min(1.0, (slope - th.conf_slope_lo) / th.conf_slope_span))
        add("有损转码", conf,
            ["截止 %.1fkHz 落在有损区间（<%.1fkHz）" % (k, th.cutoff_low_khz),
             "砖墙 %.0f dB/oct（≥%.0f 为硬砖墙）" % (slope, th.brick_db)])
    elif low_cut and mid_slope:
        # 中间带：与"刻意带限"无法区分 ⇒ 并列候选（C4.1 规矩 1）
        add("有损转码", 0.5,
            ["截止 %.1fkHz 落在有损区间" % k,
             "砖墙 %.0f dB/oct（中间带：不构成硬证据）" % slope],
            ambiguous_with=["刻意带限"])

    # 2) 升频：容器 >48k 而内容止于 44.1k 系（学术出处，但仍是线索级）
    if fs > th.upsample_fs and 0 < k <= th.upsample_content_khz:
        add("升频", 0.8,
            ["容器 %.1fkHz 但内容止于 %.1fkHz（44.1k 系断崖）" % (fs / 1000.0, k)])

    # 3) 位深填充：低位无内容（位级硬证据）
    if r.get("bit_padded"):
        add("位深填充", 0.9,
            ["低位 %s bit 无内容（容器标称 %s bit）"
             % (r.get("bit_origin"), str(r.get("bits") or "").replace("PCM_", ""))])

    # 4) 黑胶转录：低频隆隆（± 高频平缓滚降）。缺爆音/wow 判据 ⇒ 与磁带
    #    并列时标"无法区分"（C4.1 规矩 1：证据不足并列候选，不许硬判一个）
    if rumble:
        ev = ["低频隆隆：sub+bass 占 %.0f%%（≥%.0f%%）" % (low_pct, th.vinyl_rumble_pct)]
        if gentle_rolloff:
            ev.append("高频平缓滚降：内容止于 %.1fkHz（%.0f dB/oct）" % (k, slope))
        add("黑胶转录", 0.5, ev,
            counter=["硬砖墙 %.0f dB/oct（更像转档而非老载体）" % slope] if hard else [],
            ambiguous_with=["磁带转录"])

    # 5) 磁带转录：高频平缓滚降。与黑胶的区分要靠 wow/flutter（抖动）判据——
    #    **本项目测不了** ⇒ 只要滚降证据在，两个候选都必须并列出现。
    if gentle_rolloff:
        ev = ["高频平缓滚降：内容止于 %.1fkHz（%.0f dB/oct）" % (k, slope)]
        if rumble:
            ev.append("低频隆隆：sub+bass 占 %.0f%%" % low_pct)
        add("磁带转录", 0.5, ev, ambiguous_with=["黑胶转录", "刻意带限"])

    # 6) 刻意带限：带宽受限但**没有**转码证据（无硬砖墙）。与磁带共享"滚降"
    #    证据（缺 wow/flutter 判据）⇒ 并列时标无法区分。
    if bandlimited and not hard:
        add("刻意带限", 0.5,
            ["无硬砖墙（滚降 %.0f dB/oct 或未检出）" % (slope or 0.0),
             "内容止于 %.1fkHz 而无转码特征" % k],
            ambiguous_with=["磁带转录"] + (["有损转码"] if low_cut and mid_slope else []))

    # 7) 母带限幅：削波 + 真峰值超满刻度（+动态压平）。与"文件损坏"易混
    clip = r.get("clip_pct") or 0.0
    tp = r.get("true_peak_dbfs")
    lra = r.get("lra")
    if clip > th.clip_pct and tp is not None and tp > 0.0:
        ev = ["削波 %.3f%%（>%.3f%%）" % (clip, th.clip_pct),
              "真峰值 %+.2f dBTP（超满刻度）" % tp]
        if lra is not None and lra < th.lra_flat:
            ev.append("LRA %.1f LU（动态压平）" % lra)
        add("母带限幅", 0.5, ev, counter=["分布形态未检——与文件损坏易混"])

    # 8) 广播/电台版：低频缺失 **且** 宽度被压（C4.1 表：两条证据都要有）。
    #    同样过类噪守卫——平坦谱的 sub+bass 占比天然只有约 1%，那是频谱
    #    形状不是广播低切。
    if bands and not noise_like and low_pct <= th.broadcast_low_cut_pct \
            and wd is not None and wd < th.width_narrow_db:
        ev = ["低频缺失：sub+bass 仅占 %.1f%%（≤%.0f%%）" % (low_pct, th.broadcast_low_cut_pct),
              "立体声宽度被压：%.1f dB" % wd]
        add("广播/电台版", 0.5, ev, ambiguous_with=["刻意带限"])

    # 9) 假立体声（单声道上混）：相关极高 + 侧向能量极低
    if corr is not None and corr >= th.fake_stereo_corr \
            and wd is not None and wd <= th.fake_stereo_width_db:
        add("假立体声", 0.5,
            ["声道相关 %.3f（≥%.2f）" % (corr, th.fake_stereo_corr),
             "侧向能量极低：宽度 %.1f dB（≤%.0f）" % (wd, th.fake_stereo_width_db)])

    # ---- 歧义标记：对方候选也在输出里 ⇒ "无法区分" ----
    keys = {c["key"] for c in out}
    for c in out:
        c["ambiguous"] = any(x in keys for x in c["ambiguous_with"])
    return out


#: 用途矩阵的**行序**（CLI/UI 按这个顺序渲染；新增行加在末尾，不许插队——
#: 顺序即"从器材相关到方法边界"的阅读顺序）。
USES = ("频响延伸/空气感", "低频下潜/控制力", "动态与微动态", "声场宽度", "结像",
        "齿音/刺耳度", "瞬态", "大音量失真", "音色平衡", "整体可信度")


def usability_matrix(r: dict, causes: list[dict], th) -> dict:
    """C4.2 用途矩阵：把"能拿它评什么"按用途逐格给判。

    每格 **可用 / 受限 / 不可用 / 不可判**，且必须带一句**依据**
    （C 块提示词：无依据的格填"不可判"，不许猜）。

    ## 为什么它是护城河

    市面工具只答"是不是假无损"；本项目答"**能拿它评什么**"——而答案必须
    逐格给依据：同一个文件结论对不同用途的意义完全不同。
    """
    m: dict[str, dict] = {}

    def cell(v, reason):
        return {"level": v, "reason": reason}

    k = (r.get("cutoff_hz") or 0) / 1000.0
    bands = r.get("bands_pct") or {}
    low_pct = float(bands.get("sub", 0.0)) + float(bands.get("bass", 0.0))
    clip = r.get("clip_pct") or 0.0
    tp = r.get("true_peak_dbfs")
    lra, dr = r.get("lra"), r.get("dr_db")
    wd = r.get("width_db")
    keys = {c["key"] for c in causes}
    ambiguous = {c["key"] for c in causes if c.get("ambiguous")}

    # 1) 频响延伸 / 空气感：截止频率（+ 来源真伪另由成因层给）
    if k <= 0:
        m["频响延伸/空气感"] = cell("不可判", "未检出有效带宽（静音或无内容）")
    elif k >= th.advice_hf_ok_khz:
        m["频响延伸/空气感"] = cell("可用",
                                    "内容到 %.1fkHz → 可用于评估耳机的高频延伸/空气感" % k)
    elif k >= th.advice_hf_limited_khz:
        m["频响延伸/空气感"] = cell("受限",
                                    "内容止于 %.1fkHz → 评估耳机「高频延伸」不可靠：信息在源头就没了" % k)
    else:
        m["频响延伸/空气感"] = cell("不可用",
                                    "内容止于 %.1fkHz → 只够评估中频/齿音区，不足以判断高频" % k)

    # 2) 低频下潜 / 控制力：频段能量 + 是否广播版低切
    if not bands:
        m["低频下潜/控制力"] = cell("不可判", "无频段能量统计")
    elif low_pct <= th.broadcast_low_cut_pct:
        # 只说事实，**不归属因**（"是不是广播版"是 causes 层的事，用途层不越权）
        m["低频下潜/控制力"] = cell("不可用",
                                    "低频缺失（sub+bass 仅 %.1f%%）→ 下潜信息在源头缺失" % low_pct)
    elif low_pct >= th.advice_low_pct:
        m["低频下潜/控制力"] = cell("可用",
                                    "低频能量占 %.0f%% → 适合试低频下潜 / 控制力" % low_pct)
    else:
        m["低频下潜/控制力"] = cell("可用",
                                    "低频能量占 %.0f%%（正常范围）→ 可用于评估低频下潜 / 控制力" % low_pct)

    # 3) 动态与微动态：LRA + DR + 削波 + 响度战（响度战影响盲听对比的有效性）
    flags = []
    if lra is not None and lra < th.lra_flat:
        flags.append("LRA %.1f LU（动态压平）" % lra)
    if dr is not None and dr < th.dr_low_db:
        flags.append("DR %.1f dB 偏低" % dr)
    if clip > th.clip_advice_pct:
        flags.append("削波 %.3f%%" % clip)
    if r.get("lufs") is not None and r["lufs"] > th.loud_war_lufs:
        flags.append("响度战 %.1f LUFS（对比前必须先按响度对齐）" % r["lufs"])
    if lra is None and dr is None:
        m["动态与微动态"] = cell("不可判", "无动态统计")
    elif flags:
        m["动态与微动态"] = cell("不可用",
                                 "；".join(flags) + " → 不适合用来判断耳机的动态/微动态表现")
    else:
        parts = []
        if lra is not None:
            parts.append("LRA %.1f LU" % lra)
        if dr is not None:
            parts.append("DR %.1f dB" % dr)
        m["动态与微动态"] = cell("可用", " / ".join(parts) + " → 可用于评估动态/微动态")

    # 4) 声场宽度：声道宽度 + 假立体声候选
    if wd is None:
        m["声场宽度"] = cell("不可判", "单声道素材，无声场可言")
    elif wd < th.width_narrow_db:
        why = "声道宽度 %.1f dB（近乎单声道）" % wd
        if "假立体声" in keys:
            why += "，且有假立体声候选（侧向能量极低）"
        m["声场宽度"] = cell("受限", why + " → 不适合用它评声场宽度")
    else:
        m["声场宽度"] = cell("可用", "声道宽度 %.1f dB → 可用于评估声场宽度" % wd)

    # 5) 结像：**本工具不判**（需要双耳数据）——明确写"不可判"
    m["结像"] = cell("不可判", "本工具不判结像——需要双耳（HRTF）数据，文件里没有")

    # 6) 齿音 / 刺耳度：中高频占比（+ 削波另见"大音量失真"格）
    um = bands.get("upper_mid")
    if um is None:
        m["齿音/刺耳度"] = cell("不可判", "无频段能量统计")
    elif um >= th.advice_upper_mid_pct:
        m["齿音/刺耳度"] = cell("可用", "中高频占比 %.1f%%（偏高）→ 适合试齿音 / 刺耳度" % um)
    else:
        m["齿音/刺耳度"] = cell("受限", "中高频占比 %.1f%%（不突出）→ 齿音/刺耳度证据有限" % um)

    # 7) 瞬态：受时间分辨率限制（**方法边界**，不是文件问题）
    m["瞬态"] = cell("受限",
                     "受时间分辨率限制（分析窗约 93 ms）→ 只能反映大动态包络，夹在中间的瞬态细节判不准")

    # 8) 大音量失真：削波 + 真峰值 + 动态余量
    f8 = []
    if clip > th.clip_pct:
        f8.append("削波 %.3f%%" % clip)
    if tp is not None and tp > 0.0:
        f8.append("真峰值 %+.2f dBTP（超满刻度）" % tp)
    if dr is not None and dr < th.dr_low_db:
        f8.append("DR %.1f dB（余量低）" % dr)
    if f8:
        m["大音量失真"] = cell("受限", "；".join(f8) + " → 大音量下的失真可能来自文件而非耳机，别算到器材头上")
    else:
        tps = "真峰值 %+.2f dBTP" % tp if tp is not None else "真峰值未测"
        m["大音量失真"] = cell("可用", "无削波、%s → 可用于评估大音量失真" % tps)

    # 9) 音色平衡：七频段能量分布
    if not bands:
        m["音色平衡"] = cell("不可判", "无频段能量统计")
    else:
        order7 = ("sub", "bass", "low_mid", "mid", "upper_mid", "high", "air")
        dist = " ".join("%s %.0f%%" % (BAND_CN.get(b, b), bands.get(b, 0.0)) for b in order7)
        m["音色平衡"] = cell("可用", "七频段能量分布齐全（%s）→ 可用于对比音色平衡" % dist)

    # 10) 整体可信度：是否存在"成因不明"的现象（**未知项本身是信息**）
    if ambiguous:
        names = "／".join(sorted(ambiguous))
        m["整体可信度"] = cell("受限",
                               "存在无法区分的成因候选（%s）→ 结论按候选集理解，别当成单一事实" % names)
    elif "无法统计" in str(r.get("bit_verdict") or ""):
        m["整体可信度"] = cell("受限", "位深无法统计——存在成因不明的现象（查不到 ≠ 没问题）")
    elif keys:
        m["整体可信度"] = cell("可用",
                               "非 ok 现象均有对应成因候选（%s）→ 可按证据链理解" % "、".join(sorted(keys)))
    else:
        m["整体可信度"] = cell("可用", "未检出指向性成因——现象层结论可直接引用")
    return m


def boundaries() -> list[str]:
    """C4.5 效力边界：这一轮**不能**告诉用户什么。

    "未知"必须是正式输出的一部分（沿用 `engine.py` 的 DISCLAIMER 传统）——
    把工具的**不能**写清楚，用户才不会把"没测"当成"没问题"。
    """
    return [
        "结像/定位：需要双耳（HRTF）数据，文件里没有 ⇒ 本工具不判",
        "瞬态细节：分析窗约 93 ms，只能反映大动态包络",
        "有损码率的精确值：单次有损的参数识别有同行评审方法（Kim&Rafii EUSIPCO'18），但判据门限是工程估计 ⇒ 不给精确码率",
        "黑胶/磁带的区分：缺 wow/flutter（抖动）与爆音判据 ⇒ 只能并列候选",
        "AI 生成音频：未取到出处 ⇒ 本工具不猜、不判",
        "音源真伪/年代：无指纹库，只报可测量现象",
        "重放链（DAC/耳放/耳机）：文件之外的失真本工具看不到",
    ]


@register
class CausesAnalyzer(Analyzer):
    """成因候选 / 用途矩阵 / 效力边界（C4.1–C4.2、C4.5）。

    纯 dict 逻辑，**不做任何计算**（重活都在频谱/响度/立体声分析器里）——
    所以 perf 上没有新增成本。order=89：verdict（90）要读它的输出。
    """

    key = "causes"
    label = "成因与用途"
    order = 89
    #: 与 verdict 同一组依赖：它读所有前置分析器的结论字段。
    requires = ("basic", "bitdepth", "spectrum", "loudness", "stereo", "codec", "metadata")

    def run(self, ctx) -> dict:
        if not ctx.deep:           # 浅模式没有频谱，谈不成因
            return {}
        r = ctx.result
        th = ctx.thresholds
        causes = cause_candidates(r, ctx.fs, th)
        return {
            "causes": causes,
            "usability": usability_matrix(r, causes, th),
            "boundaries": boundaries(),
        }
