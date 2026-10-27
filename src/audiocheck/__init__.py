"""audiocheck —— 音源质量检测与音乐特性分析。

看内容，不看标签。

模块划分
--------
core        分析引擎（可插拔分析器 + 标准级 DSP）
ui          Qt 桌面界面（可选依赖）
cli         命令行入口
summarize   批量结果汇总

快速开始
--------
>>> from audiocheck import analyze
>>> r = analyze("song.flac")
>>> r["verdict"]
'伪 Hi-Res（容器 96.0kHz，内容仅到 19.0kHz）'
"""

__version__ = "1.1.3"
__all__ = ["analyze", "collect_files", "Thresholds", "ANALYZERS", "register", "Analyzer"]

from .config import Thresholds  # noqa: E402
from .core.engine import analyze, collect_files  # noqa: E402
from .core.registry import ANALYZERS, Analyzer, register  # noqa: E402
