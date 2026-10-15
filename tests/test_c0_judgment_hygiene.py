"""C0-6 防回潮：分析器里的数值字面量**必须登记在案**。

## 为什么需要它

C0 把 `verdict.py` 里 12 处硬编码阈值收编进了 `config.Thresholds`。
但"收编一次"不等于"不会再长"——下一个人（或下一轮的我）照样可以随手写
`if k >= 20.0:`，而那个 20 会**悄悄变成第二个真值源**。

所以立一条**机器判据**：`core/analyzers/` 里出现的每个数值字面量都必须在
下面的白名单里。不在 = 失败。镜像 `test_ui_grid.py` 扫 QSS 字号的手法。

## 白名单怎么读

每个值是 `{文件: {值: 理由}}`。理由分三类，**不许写"就是需要"**：

* **结构性**：索引 / 步长 / 单位换算 / 浮点零保护——不表达任何判据；
* **标准常数**：来自公开标准（BS.1770 的门限块数等）——**必须写标准名**；
* **数据表**：编码器截止频率表、流媒体响度目标表——是**查表用的数据**，不是判据；
* **算法参数**：窗口比例、检测分数——写清它在算法里的位置。

⚠ **判据性阈值一律不许出现在这里**——它们必须在 `config.Thresholds`。
如果你正想往白名单里加一个"阈值"，先问：它该不该进 config？

⚠ 扫描器用 **AST**，天然不含注释与字符串内容（本项目在"注释被当成违规"
上踩过三次坑，用 AST 从根上避开）。
"""
from __future__ import annotations

import ast
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ANALYZERS = os.path.join(_ROOT, "src", "audiocheck", "core", "analyzers")

def _canon(v) -> str:
    """数值字面量的**规范键**：按数值而不是写法。

    `1e-9` 与 `1e-09`、`0` 与 `0.0`、`1000` 与 `1000.0` 都是同一个数——
    按写法比对会让白名单被"同一个数的另一种拼法"误判（实测：`1e-09` 被报未登记）。
    """
    return "%g" % float(v)


#: 结构性理由（跨文件共用；同一个数在不同文件里含义相同时只写一次）。
_STRUCT = {
    "0": "索引 / 中性起点", "0.0": "索引 / 中性起点",
    "1": "索引 / 步长 / 单位比例", "1.0": "单位比例",
    "2": "索引 / 2 的幂 / 立体声通道数", "3": "索引",
    "4": "索引 / 声道上限", "5": "索引 / 字段数",
    "1e-9": "浮点零保护", "1e-12": "浮点零保护", "1e-20": "log 零保护",
    "100": "百分比换算", "1000": "单位换算 kHz↔Hz", "1000.0": "单位换算 kHz↔Hz",
}

_ALLOWED: dict[str, dict[str, str]] = {
    "__init__.py": {},
    "basic.py": {
        "65536": "读文件块大小 BLOCK",
        "0.2": "TT-DR：只取最响的 20% 块（算法定义）",
        "0.9999": "削波检测门限（|x| ≥ 此值算削平）",
        "3.0": "峰值/统计的保留位数无关常数",
        "10": "dB 换算（20·log10 的分母来自 10 的幂）",
        "20": "dB 换算系数（20·log10）",
        "1e-12": "浮点零保护",
    },
    "bitdepth.py": {
        "0.999": "DETECT_FRAC：判定『有内容』的样本比例",
        "0.25": "LOUD_FRAC：只统计 |x| > 0.25×峰值 的样本（隔离电平）",
        "0.4": "均匀 LSB 窗口下界（0.4–0.6 视为均匀 ⇒ 疑似升位）",
        "0.6": "均匀 LSB 窗口上界",
        "2000000": "MAX_SAMPLES：统计样本上限（性能界）",
        "8": "位深（8 bit）", "15": "调度 order", "16": "位深（16 bit）",
        "20": "位深（20 bit）", "24": "位深（24 bit）", "32": "位深（32 bit）",
        "64": "位深（64 bit 浮点）",
    },
    "codec.py": {
        "0.35": "编码器判定的容差比例",
        "0.97": "编码器判定的匹配度比例",
        "2.0": "倍频程换算", "10": "索引 / 十进制",
        "60": "秒（取样时长）", "2000.0": "Hz 常数", "22050.0": "Hz 常数（44.1k 奈奎斯特）",
        # 下面一串是**编码器截止频率表**（查表数据，不是判据）
        "10.5": "编码器截止表：条目", "11.6": "编码器截止表：条目",
        "13.2": "编码器截止表：条目", "14.3": "编码器截止表：条目",
        "14.8": "编码器截止表：条目", "15.4": "编码器截止表：条目",
        "16.6": "编码器截止表：条目", "17.0": "编码器截止表：条目",
        "17.9": "编码器截止表：条目", "18.5": "编码器截止表：条目",
        "19.2": "编码器截止表：条目", "19.4": "编码器截止表：条目",
        "19.9": "编码器截止表：条目", "20.6": "编码器截止表：条目",
        "8": "位深（8 bit）", "24": "位深（24 bit）", "32": "位深（32 bit）",
    },
    "loudness.py": {
        "40": "BS.1770-4：门限块数相关常数",
        "41": "BS.1770-4：门限块数相关常数",
    },
    "metadata.py": {"4": "索引 / 声道数", "5": "索引 / 字段数"},
    "normalization.py": {
        "45": "调度 order",
        "3.0": "dB 容差",
        # 下面是**流媒体响度目标表**（每项都带出处，见文件内的表）
        "14.0": "响度目标表：Spotify/YouTube 等（表中带出处）",
        "16.0": "响度目标表：Apple Music（表中带出处）",
        "23.0": "响度目标表：EBU R128（标准）",
    },
    "spectrum.py": {
        "1e-20": "log 零保护",
        "1.6": "1/6 倍频程带比（2^(1/3) 的近似，平滑与带宽判定用）",
        "0.04": "可用频段下限比例（上方频段不足 4% 就跳过）",
        "1.0": "单位比例", "2.0": "倍频程", "3": "索引 / 步数",
        "6.0": "1/6 倍频程平滑的分母", "10.0": "十进制", "12": "1/12 倍频程",
        "16": "1/16 倍频程", "30": "Hz（低频起点）", "60.0": "秒（取样时长）",
        "100.0": "Hz 常数",
    },
    "stereo.py": {"42": "调度 order", "60.0": "秒（取样时长）"},
    # C0 收编后，verdict 里只剩结构性数字（判据阈值全部在 config.Thresholds）
    "verdict.py": {"90": "调度 order（不是阈值）", "99": "_RANK_UNKNOWN：未知等级按最差"},
}


class TestNoUnregisteredNumbers(unittest.TestCase):
    """分析器里出现的每个数值字面量都必须在白名单里。"""

    def test_every_number_is_whitelisted(self):
        bad = []
        for fn in sorted(os.listdir(_ANALYZERS)):
            if not fn.endswith(".py"):
                continue
            own = _ALLOWED.get(fn)
            self.assertIsNotNone(own, "%s 不在白名单里——新增分析器要一并登记" % fn)
            allow = {_canon(k) for k in set(own) | set(_STRUCT)}
            src = open(os.path.join(_ANALYZERS, fn), encoding="utf-8").read()
            tree = ast.parse(src)          # AST：天然不含注释与字符串
            seen = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                        and not isinstance(node.value, bool)):
                    seen.add(_canon(node.value))
            for v in sorted(seen - allow):
                bad.append("%s: %s" % (fn, v))
        self.assertEqual(bad, [],
                         "分析器里出现未登记的数值字面量（判据性阈值请进 config.Thresholds）：\n  "
                         + "\n  ".join(bad))

    def test_whitelist_has_no_stale_entries(self):
        """白名单**只增不减**会让它慢慢失真：删掉的数字必须从白名单里也删掉。"""
        stale = []
        # ⚠ 只比**该文件的专属条目**，不带 `_STRUCT`：结构性数字在别的文件里
        # 不出现是正常的（`__init__.py` 一个数字都没有），带进来会把它们全报成陈旧。
        for fn, own in _ALLOWED.items():
            allow = {_canon(k) for k in own}
            src = open(os.path.join(_ANALYZERS, fn), encoding="utf-8").read()
            tree = ast.parse(src)
            seen = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                        and not isinstance(node.value, bool)):
                    seen.add(_canon(node.value))
            for v in sorted(allow - seen):
                stale.append("%s: %s" % (fn, v))
        self.assertEqual(stale, [], "白名单里有已不存在的数字（请删掉，别留着当橡皮图章）：\n  "
                         + "\n  ".join(stale))

    def test_no_threshold_lives_in_the_judgment_layer(self):
        """`verdict.py` 里**不许**再出现判据性阈值——它们必须在 `config.Thresholds`。

        这条是 C0 的核心不变式：白名单可能被慢慢放宽，但"判定层不许自带阈值"
        这条不能松。判据实现方式：verdict 里允许的数字集**只许**是结构性那几个。
        """
        allowed = {_canon(k) for k in _ALLOWED["verdict.py"]}
        structural = {_canon(k) for k in set(_STRUCT) | {"90", "99"}}
        extra = allowed - structural
        self.assertEqual(extra, set(),
                         "verdict.py 的白名单里混进了非结构性数字：%s" % sorted(extra))


if __name__ == "__main__":
    unittest.main(verbosity=2)
