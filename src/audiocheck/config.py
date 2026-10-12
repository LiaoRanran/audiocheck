"""集中配置：判定阈值与运行偏好。

设计意图：所有"魔法数字"只在这里出现一次，方便调参与回归测试。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields


@dataclass
class Thresholds:
    """判定阈值。单位与含义见 README §三。"""

    # --- 带宽判定（kHz）---
    cd_full: float = 21.5        # 44.1k 容器：≥ 此值算满带宽
    ltd: float = 19.0            # 带限 / 有损分界
    lossy: float = 16.0          # 有损 / 强有损分界
    full_48: float = 23.0        # 48k 容器：≥ 此值算真 48k
    up_44: float = 21.0          # 48k 容器：≥ 此值疑似 44.1k 升频
    hi_ok: float = 24.5          # >48k 容器：≤ 此值算伪 Hi-Res
    hi_ratio: float = 0.85       # >48k 容器：≥ 奈奎斯特此比例算真 Hi-Res

    # --- 频谱分析 ---
    min_drop_db: float = 15.0    # 截止点之上 1/3 倍频程内至少掉这么多 dB，才算"截止"而非自然滚降
    brick_db: float = 40.0       # 截止点之上一个倍频程掉落超过此值判为砖墙

    # --- 电平告警 ---
    clip_pct: float = 0.005      # 削波样本占比告警线（%）
    loud_war_lufs: float = -9.0  # 集成响度高于此值标记"响度战"
    lra_flat: float = 3.0        # LRA 低于此值标记"动态压平"
    width_narrow_db: float = -18.0   # 声道宽度低于此值提示近乎单声道

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
