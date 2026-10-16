"""集中配置：判定阈值与运行偏好。

设计意图：所有"魔法数字"只在这里出现一次，方便调参与回归测试。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields


@dataclass
class Thresholds:
    """判定阈值。单位与含义见 README §三。

    ## C0 判据层卫生（2026-10-15）

    原先 `verdict.py` 里散着 12 处硬编码数字，与这里的阈值**两套并存**。
    本轮把它们**收编到本表**——**值一律不变**（收编不是调参）。新增字段的注释
    必须写清**语义与用途**，因为"同一个量在不同语境下有不同门限"是这一层的常态
    （典型：削波有告警/严重/建议三条线，不许合并成一个）。
    """

    # --- 带宽判定（kHz）---
    cd_full: float = 21.5        # 44.1k 容器：≥ 此值算满带宽
    ltd: float = 19.0            # 带限 / 有损分界
    lossy: float = 16.0          # 有损 / 强有损分界
    full_48: float = 23.0        # 48k 容器：≥ 此值算真 48k
    up_44: float = 21.0          # 48k 容器：≥ 此值疑似 44.1k 升频
    hi_ok: float = 24.5          # >48k 容器：≤ 此值算伪 Hi-Res
    hi_ratio: float = 0.85       # >48k 容器：≥ 奈奎斯特此比例算真 Hi-Res
    #: **容器分界**（Hz）——判定"这个容器属于哪一档"的边界。
    #: ⚠ C0：审查报告的"12 处魔数"清单**漏了这两条**（它们在 `verdict.py` 里
    #: 以 `fs <= 45000` / `fs <= 50000` 的字面量形式存在）。补收编时一并发现。
    fs_44_container: int = 45000   # ≤ 此值视为 44.1k 档容器
    fs_48_container: int = 50000   # ≤ 此值视为 48k 档容器（之上算高采样率）
    #: 位深差告警门限（bit）：容器标称比有效位深高出这么多 ⇒ 低位无内容。
    bit_gap_warn: int = 4

    # --- 频谱分析 ---
    min_drop_db: float = 15.0    # 截止点之上 1/3 倍频程内至少掉这么多 dB，才算"截止"而非自然滚降
    min_stop_db: float = 20.0    # 截止点之上必须落到"本底"以下多少 dB 才算真截止（C0-3：原在 spectrum.py 默认参数里）
    #: ⚠ 以下两项是**算法参数**（不是判定阈值），但同样必须只有一个出处：
    #: 原先它们只写在 `spectrum.py::detect_cutoff` 的函数签名里，与 config 各存一份
    #: （"双真值源"）。更要紧的是：调用点只传了 `min_drop_db`，**其余三项永远取
    #: 函数默认值** ⇒ 用户在设置里改它们同样"设了没反应"（与 `brick_db` 同型 bug）。
    #: C0-3 已让调用点把四项全部显式传下去。
    cutoff_min_hz: float = 20.0  # 截止点检测的起始频率（Hz）
    cutoff_need: int = 3         # 连续多少步满足"下降"才认定截止
    #: **砖墙陡度门限**（dB/oct）。≥ 此值判为"硬砖墙"——用来区分
    #: 「上游低通（有损转码的特征）」与「母带/录音链的自然滚降」。
    #: ⚠ C0-2：此字段原名 `brick_db=40.0`，**在设置对话框里可调但没有任何代码读它**
    #: （用户设了它什么也不会发生——比死代码更糟）。而 `verdict.py` 真正用的是硬编码 24.0。
    #: 本轮统一：**取真口径 24.0**，并让 verdict 读这里。40→24 是口径变更，
    #: 但因为 40 从未被读，**行为零变化**（CHANGELOG 有记）。
    brick_db: float = 24.0
    #: 滚降"平缓"门限（dB/oct）：低于此值 + 截止点偏低 ⇒ 证据不足以断定有损转码。
    brick_flat_db_per_oct: float = 15.0
    #: 截止点"偏低"门限（kHz）：低于此值才谈得上"上游被限带"。
    cutoff_low_khz: float = 20.0

    # --- 电平告警（削波有**三条线**，语义不同，不许合并）---
    #: 告警线（%）：超过就提示"存在削波"。
    clip_pct: float = 0.005
    #: 严重线（%）：超过判 `bad`——"母带级损坏"。
    clip_severe_pct: float = 0.5
    #: 建议文案线（%）：只影响"别把这点失真算到耳机头上"这句提示。
    clip_advice_pct: float = 0.01
    loud_war_lufs: float = -9.0  # 集成响度高于此值标记"响度战"
    lra_flat: float = 3.0        # LRA 低于此值标记"动态压平"
    #: DR 偏低门限（dB）。⚠ 同一判据原先在 `verdict.py` 里写了两遍（判定轴 + 建议），
    #: 收编为一处，避免将来只改一处造成"判定说偏低、建议不说"。
    dr_low_db: float = 6.0
    width_narrow_db: float = -18.0   # 声道宽度低于此值提示近乎单声道

    # --- 跨轴核对：假 Hi-Res / 升频 ---
    #: 高采样率容器的判定起点（Hz）：容器采样率 > 此值才谈得上"疑似升频"。
    upsample_fs: int = 88200
    #: 内容止于此值（kHz）且容器是高采样率 ⇒ 疑似从 44.1k 系升频。
    upsample_content_khz: float = 22.5

    # --- 建议（可用性文案）门限 ---
    #: 高频内容 ≥ 此值（kHz）⇒ 提示"可用于评估耳机高频延伸"。
    advice_hf_ok_khz: float = 20.0
    #: 高频内容 ≥ 此值（kHz）但 < 上一条 ⇒ 提示"评估高频延伸不可靠"。
    advice_hf_limited_khz: float = 16.0
    #: 低频（sub+bass）能量占比 ≥ 此值（%）⇒ 提示"适合试低频下潜"。
    advice_low_pct: float = 60.0
    #: 中高频占比 ≥ 此值（%）⇒ 提示"适合试齿音/刺耳度"。
    advice_upper_mid_pct: float = 15.0

    # --- 证据强度（conf）---
    #: 证据强度低于此值 + 截止点偏低 ⇒ 追加"结论偏保守"提示。
    conf_low: float = 0.5
    #: conf 的映射端点（dB/oct）：≤ 此值视为自然滚降 ⇒ conf=0。
    conf_slope_lo: float = 8.0
    #: conf 的映射跨度（dB/oct）：`conf = clip((slope - lo) / span, 0, 1)`。
    conf_slope_span: float = 16.0

    # --- C4 成因候选（**全部工程估计，无外部出处**；只作候选证据，不作判据级结论）---
    #: sub+bass 占比 ≥ 此值 ⇒ 记"低频隆隆"证据（黑胶转录候选）。
    vinyl_rumble_pct: float = 25.0
    #: **类噪内容守卫**：谱平坦度 ≥ 此值 ⇒ 内容是噪声状频谱。此时"低频隆隆/
    #: 低频缺失"类**占比证据名不副实**（白噪本身没有音乐式的低音结构），
    #: 一律不作为候选证据。实测：Welch 谱上白噪 flatness ≈ 0.99、音乐 < 0.1。
    #: ⚠ 守卫只用于占比类证据；截止/砖墙类证据（有损/升频/滚降形态）对
    #: 噪声状内容同样成立，不守卫（砖墙白噪正是有损判定的标准测试素材）。
    noise_like_flatness: float = 0.5
    #: sub+bass 占比 ≤ 此值 ⇒ 记"低频缺失"证据（广播/电台版候选）。
    broadcast_low_cut_pct: float = 5.0
    #: 声道相关 ≥ 此值 **且** 宽度 ≤ 下一条 ⇒ 假立体声（单声道上混）候选。
    fake_stereo_corr: float = 0.98
    fake_stereo_width_db: float = -30.0
    #: **局部反相**（`dsp.anti_phase_scan`）的分档门限：反相能量占比低于此值记"无"。
    #: ⚠ C0-3 同型：这三档原先**硬编码在 dsp.anti_phase_scan 里**，
    #: 用户在设置里改不到、也没进普查；现在按"调用点显式传 `ctx.thresholds`"收编。
    #: 分档只决定**文案档位与声道轴是否告警**，不参与成因候选。
    anti_scope_minor: float = 0.01
    anti_scope_major: float = 0.10
    anti_scope_severe: float = 0.50
    # --- C4 时间定位（**算法参数**，不是判定阈值；见 analyzers/timeline.py）---
    seg_seconds: float = 10.0      # 分段窗长（秒）
    seg_overlap: float = 0.5       # 分段重叠比例
    seg_min_count: int = 4         # 少于这么多段不下分段结论（小样本不许硬判）

    # --- 视图渲染质量（**不影响判定**；见 core/views.py 双分辨率 STFT，C7）---
    #: 频谱图低频段双分辨率（16384 窗补 <546Hz 细节）。只改善"看图"，
    #: 不动判定曲线（后者锁死 16384-FFT，与本处无关）。默认开。
    dual_resolution_stft: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Thresholds:
        """从 dict 构造，**逐项类型校验**。

        ## 为什么必须校验（这里踩过一个很难看的坑）

        早期只过滤键名、不校验类型：

            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in d.items() if k in known})

        于是 `{"cd_full": "不是数字"}` 会被 dataclass **照单全收**
        （dataclass 不做运行时类型检查），一直等到判定器里做
        `20.0 >= "不是数字"` 才抛 TypeError。

        而引擎把分析器异常吞成"分析器告警"，最终表现是：

            判定整段消失 ｜ 汇总变成「合格 0 / 有疑 0 / 不达标 0」｜ 退出码仍是 0

        **用户设了阈值、阈值没生效、判定没了、脚本还一切正常。**
        这类"静默降级"比直接报错危险得多——所以配置必须在入口就校验。

        未知键**不报错但会返回在 warnings 里**（便于发现拼写错误，
        又不会因为新版加了字段就让旧配置文件用不了）。
        """
        d = d or {}
        known = {f.name: f for f in fields(cls)}
        bad: list[str] = []
        unknown: list[str] = []
        vals: dict = {}
        for k, v in d.items():
            if k not in known:
                unknown.append(k)
                continue
            ftype = known[k].type
            want_num = ftype in ("float", "int", float, int)
            if want_num:
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    # 尝试把"看起来是数字"的字符串救回来（如 "19.5"），
                    # 救不回来才算错 —— 手写 JSON 常把数字写成字符串。
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        bad.append("%s=%r（应为数字）" % (k, v))
                        continue
                v = int(v) if (ftype in ("int", int) or float(v).is_integer()) else float(v)
            elif ftype == "bool" and not isinstance(v, bool):
                bad.append("%s=%r（应为 true/false）" % (k, v))
                continue
            vals[k] = v
        if bad:
            raise ValueError("配置文件里的阈值类型不对：" + "；".join(bad))
        th = cls(**vals)
        if unknown:
            th.__dict__["_unknown_keys"] = unknown      # 只作提示，不影响行为
        return th

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> Thresholds:
        """读配置文件。

        **不再吞掉解析/校验错误。** 早期 `except (OSError, ValueError): pass`
        会让"文件写坏了"退化成"悄悄用默认值"，用户以为自己的阈值生效了。
        现在：文件不存在 → 用默认值；文件存在但坏 → 抛出，由调用方报给用户。
        """
        if not path or not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as f:
            try:
                d = json.load(f)
            except ValueError as e:
                raise ValueError("配置文件不是合法 JSON：%s（%s）" % (path, e)) from None
        if not isinstance(d, dict):
            raise ValueError("配置文件的顶层必须是对象，实得 %s" % type(d).__name__)
        return cls.from_dict(d)


#: 默认阈值（引擎在不显式传入时使用）
DEFAULT = Thresholds()

#: 支持解析的音频扩展名
AUDIO_EXTS = (".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".aif", ".aiff", ".wma")

#: 七频段定义（Hz），与耳机推荐引擎的 band_def 保持一致
BANDS = (("sub", 20, 60), ("bass", 60, 250), ("low_mid", 250, 500), ("mid", 500, 2000),
         ("upper_mid", 2000, 6000), ("high", 6000, 16000), ("air", 16000, 20000))

BAND_CN = {"sub": "超低", "bass": "低频", "low_mid": "中低", "mid": "中频",
           "upper_mid": "中高", "high": "高频", "air": "空气"}

# --------------------------------------------------------------------- 出处账
#: 阈值**出处登记**（C2 / P0-2）。格式照 `headphone-lab/src/audibility.py` 的先例：
#: 每个数字都要能追到公开标准、或**明说没有出处**。四类，**不许含糊**：
#:
#:   "标准"     —— 可引用的公开标准文本（标准号/条款写进依据）
#:   "一手文档" —— 厂商/平台的官方文档（不是第三方转述）
#:   "二手"     —— 公开但非一手（社区资料、转述）——**可作方向性依据，不可作精确阈值引用**
#:   "工程估计" —— **查不到出处，是本项目的取值** —— 用户可见处要带限定词
#:
#: ⚠ **每条依据必须自足**——不许写"同上"（看单行的人不该需要往上翻），
#: 由 `test_engineering_estimates_say_so_explicitly` 强制（实测抓到过四条"同上"）。
#: ⚠ 判据 `tests/test_c2_provenance.py` 强制：`Thresholds` 的**每个字段都要有登记**，
#: 且类别必须在这四类里；标"标准"/"一手文档"的**必须写出具体来源**（不许写"见某处"）。
PROVENANCE: dict[str, tuple[str, str]] = {
    # --- 带宽 / 容器（标准定"容器规格"，阈值本身是工程取值）---
    "cd_full": ("工程估计",
                "44.1 kHz 采样与 22.05 kHz 奈奎斯特来自 CD 规范（IEC 60908 / Red Book，标准）；"
                "21.5 kHz 是本项目留的约 2.5% 余量，**无外部出处**"),
    "ltd": ("工程估计",
            "想对应'320 kbps 有损的典型截止'。**一手编码器数据已取到**（2026-10-17）："
            "LAME 项目官方 GPSYCHO 页（lame.sourceforge.io/gpsycho.php）称 256 kbps 不做低通、"
            "128 kbps 低通约 15.5 kHz；FFmpeg 源码 `libavcodec/psymodel.h` 的宏 "
            "`AAC_CUTOFF_FROM_BITRATE` 给出 AAC 立体声 192/256/320 kbps ≈ 18.0/20.0/22.0 kHz。"
            "⚠ 但给的是'某码率的典型截止'，**19.0 kHz 这个判据门限本身无外部出处**"
            "（哪个截止算'带限'、哪个算'有损'是本项目取值）"),
    "lossy": ("工程估计",
              "想对应'128–192 kbps 有损的典型截止'。**一手编码器数据已取到**（2026-10-17）："
              "LAME 官方 GPSYCHO 页称 128 kbps 低通约 15.5 kHz、256 kbps 不做低通；"
              "FFmpeg 源码宏 `AAC_CUTOFF_FROM_BITRATE` 给 AAC 立体声 192 kbps ≈ 18.0 kHz。"
              "⚠ **16.0 kHz 这个判据门限本身无外部出处**，是本项目取值（由'典型截止'"
              "反推'强有损'的分界是工程判断）"),
    "full_48": ("工程估计", "48 kHz 采样来自标准；23.0 kHz 是本项目留的余量，**无外部出处**"),
    "up_44": ("工程估计", "判'48k 容器里内容止于 44.1k 系'；44.1 kHz 是标准值，"
                          "21.0 kHz 的门限是本项目取值"),
    "hi_ok": ("工程估计", "判'伪 Hi-Res'的上界；**无外部出处**"),
    "hi_ratio": ("工程估计", "内容占奈奎斯特的比例门限；**无外部出处**"),

    # --- 频谱分析（算法参数，决定检出而非判定）---
    "min_drop_db": ("工程估计", "截止点检出的算法参数；**无外部出处**"),
    "min_stop_db": ("工程估计", "截止点检出的算法参数；**无外部出处**"),
    "cutoff_min_hz": ("工程估计", "截止点检出的起始频率；20 Hz 是音频下限的常用起点，但不是判据，**无外部出处**"),
    "cutoff_need": ("工程估计", "截止点检出的连续步数；**无外部出处**"),
    "brick_db": ("工程估计", "24 dB/oct 作为'硬砖墙'的门限；**无外部出处**"
                             "（'brickwall' 没有标准斜率定义）"),
    "brick_flat_db_per_oct": ("工程估计", "15 dB/oct 作为'平缓滚降'的门限；**无外部出处**"),
    "cutoff_low_khz": ("工程估计", "20 kHz 作为'截止点偏低'的门限；与 CD 奈奎斯特同量级，但取值是工程判断"),

    # --- 电平 / 削波（**三条线语义不同**，各自出处）---
    "clip_pct": ("工程估计", "0.005% 作为削波告警线；**无外部出处**"),
    "clip_severe_pct": ("工程估计", "0.5% 作为'母带级损坏'的严重线；**无外部出处**"),
    "clip_advice_pct": ("工程估计", "0.01% 只用于触发一句提示文案；**无外部出处**"),
    "loud_war_lufs": ("一手文档",
                      "**Spotify 官方支持页《Loudness normalization》**（一手）："
                      "归一化目标 −14 LUFS（依 ITU 1770），Premium 最响档 −11 LUFS。"
                      "−9 LUFS 比该最响档还高 2 LU ⇒ 已超出主流平台的归一化上限，故判'响度战'。"
                      "⚠ 这是**从官方数据推出的判断**，不是平台规定的阈值"),
    "lra_flat": ("工程估计",
                 "LRA 的**定义**来自 EBU Tech 3342（标准，见 `docs/theory.md`）；"
                 "3 LU 这个**门限**是本项目取值，**无外部出处**"),
    "dr_low_db": ("工程估计",
                  "TT-DR 的**算法**由 TT 公开，但**官方未定义判据门限**；"
                  "6 dB 是本项目取值"),
    "width_narrow_db": ("工程估计", "M/S 能量比 −18 dB 作为'近乎单声道'的门限；**无外部出处**"),

    # --- 容器分界 / 位深 ---
    "fs_44_container": ("工程估计", "44.1 kHz 是标准采样率；45000 Hz 这个**分界**是工程取值"
                                    "（留出 44.1k 与 48k 之间的空档）"),
    "fs_48_container": ("工程估计", "48 kHz 是标准采样率；50000 Hz 这个**分界**是工程取值"),
    "bit_gap_warn": ("工程估计",
                     "4 bit 作为'容器标称与有效位深差'的告警线；'24bit 容器装 16bit 内容'"
                     "是已知的伪 Hi-Res 手法，但**门限无外部出处**"),

    # --- 假 Hi-Res / 升频 ---
    "upsample_fs": ("工程估计", "88200 Hz（2×44.1k）作为'高采样率容器'的起点；取值是工程判断"),
    "upsample_content_khz": ("工程估计", "22.5 kHz 作为'内容止于 44.1k 系'的上界；"
                                         "22.05 是标准奈奎斯特，0.45 kHz 余量是本项目取值"),

    # --- 建议（可用性文案）门限 ---
    "advice_hf_ok_khz": ("工程估计", "20 kHz 作为'高频内容充足'的门限；**无外部出处**"),
    "advice_hf_limited_khz": ("工程估计", "16 kHz 作为'高频受限'的门限；**无外部出处**"),
    "advice_low_pct": ("工程估计", "60% 作为'低频能量占比高'的门限；**无外部出处**"),
    "advice_upper_mid_pct": ("工程估计", "15% 作为'中高频占比偏高'的门限；**无外部出处**"),

    # --- 证据强度 ---
    "conf_low": ("工程估计", "conf < 0.5 作为'证据不足'的门限；**无外部出处**"),
    "conf_slope_lo": ("工程估计", "conf 映射的下端点（8 dB/oct = 自然滚降）；**无外部出处**"),
    "conf_slope_span": ("工程估计", "conf 映射的跨度；**无外部出处**"),

    # --- C4 成因候选（C4.1）：门限全是**工程估计**，只作候选证据、不作判据级结论。
    # 出处状态与 `verdict.cause_candidates` 的 _CAUSE_PROV 表一致；用户可见处
    # 一律带"疑似（工程估计）"限定词（C 块提示词 out-of-scope 第 13 条）。
    "vinyl_rumble_pct": ("工程估计",
                         "黑胶隆隆声（sub+bass 能量异常）判据；**未取到同行评审来源** ⇒ 只作候选证据"),
    "noise_like_flatness": ("工程估计",
                            "类噪内容守卫：谱平坦度门限。白噪 Welch 谱实测 ≈0.99、音乐 <0.1，"
                            "0.5 落在两者间的宽隙里；**门限取值无外部出处**"),
    "broadcast_low_cut_pct": ("工程估计",
                              "广播/电台版低频缺失判据（sub+bass 占比下限）；**无外部出处**"),
    "fake_stereo_corr": ("工程估计",
                         "假立体声（单声道上混）的声道相关门限；**无外部出处**"),
    "fake_stereo_width_db": ("工程估计",
                             "假立体声的侧向能量（M/S 宽度）门限；**无外部出处**"),
    "anti_scope_minor": ("工程估计", "反相能量占比 < 1% 记'无'；**无外部出处**"),
    "anti_scope_major": ("工程估计", "反相能量占比 ≥ 10% 记'明显'；**无外部出处**"),
    "anti_scope_severe": ("工程估计", "反相能量占比 ≥ 50% 记'严重'；**无外部出处**"),

    # --- C4 时间定位（算法参数，不出结论）---
    "seg_seconds": ("工程估计", "时间定位分段窗长（算法参数，不影响判定）；**无外部出处**"),
    "seg_overlap": ("工程估计", "分段重叠比例（算法参数）；**无外部出处**"),
    "seg_min_count": ("工程估计", "最少分段数：少于它不下分段结论（防止小样本硬判）；**无外部出处**"),

    # --- 视图渲染质量（算法参数，不影响判定）---
    "dual_resolution_stft": ("工程估计",
                             "频谱图低频双分辨率（16384 窗补 <546Hz 细节）是**视图渲染质量**"
                             "选项、不影响判定；默认开，**无外部出处**"),
}

#: 出处类别的**可信度降级措辞**（C2：用户可见处必须带）。
#: 空串 = 不必加限定词（标准与一手文档可以直接说）。
PROVENANCE_SUFFIX = {
    "标准": "",
    "一手文档": "",
    "二手": "（二手来源）",
    "工程估计": "（工程估计，无出处）",
}


def provenance(field: str) -> tuple[str, str]:
    """取某阈值的 (类别, 依据)。未登记的返回 ('未登记', '')——**不许默认成有出处**。"""
    return PROVENANCE.get(field, ("未登记", ""))


def provenance_suffix(field: str) -> str:
    """该阈值在用户可见文案里要带的**限定词**（标准/一手为空串）。"""
    return PROVENANCE_SUFFIX.get(provenance(field)[0], "（出处未登记）")


def provenance_summary() -> dict:
    """按类别统计 + 列出全部工程估计字段（供 CLI / 报告使用）。"""
    from collections import Counter
    cnt = Counter(c for c, _ in PROVENANCE.values())
    est = sorted(f for f, (c, _) in PROVENANCE.items() if c in ("工程估计", "未登记"))
    return {"counts": dict(cnt), "engineering_estimates": est,
            "total_fields": len(Thresholds().__dict__)}


LEVEL_CN = {"ok": "合格", "warn": "有疑", "bad": "不达标"}
