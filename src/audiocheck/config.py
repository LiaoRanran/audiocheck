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

LEVEL_CN = {"ok": "合格", "warn": "有疑", "bad": "不达标"}
