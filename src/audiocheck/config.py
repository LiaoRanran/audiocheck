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
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> Thresholds:
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return cls.from_dict(json.load(f))
            except (OSError, ValueError):
                pass
        return cls()


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
