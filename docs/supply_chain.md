# 供应链与可复现构建（E2.2 / E2.3）

> 本文件记录**实测结果**与**未做项**，不宣称"安全"。每条都给出复算命令。

## 1. SBOM（软件物料清单）

```bash
python packaging/sbom.py --out out/sbom.json
```

- **来源**：从**已安装分发的元数据**（`importlib.metadata`）读名字与版本，
  以 `pyproject.toml` 的 `dependencies` + `gui` extra 为根，沿 `Requires-Dist`
  递归收**传递依赖**。**不联网、不猜**——装了什么就报什么。
- **实测**：本轮 16 个组件（含 `PySide6` / `PySide6_Addons` / `shiboken6` /
  `matplotlib` / `numpy` / `scipy` / `soundfile` / `mutagen` / `pillow` 等）。
- **格式**：CycloneDX 1.5 风格 JSON；`metadata.properties` 里带生成器、Python 版本
  与**审计状态**（见下）。
- **进发布物**：`out/sbom.json` 随 `dist/` 一起交付（D4 发布清单里登记）。

## 2. 依赖审计（`pip-audit`）

**状态：未做（本机离线，且未安装 `pip-audit`）。**

`pip-audit` 需要联网查漏洞库。本文件**不假装审计过**——`sbom.py --audit`
在装了 `pip-audit` 时会真跑并把结论写进 SBOM；没装就明确写"未做"。
⇒ **欠账**：联网环境补跑一次，有高危则修复或显式登记。

## 3. 可复现构建（E2.3）

**结论：默认构建 _不可_ 逐字节复现；加 `SOURCE_DATE_EPOCH` 后 _可以_。**

复算命令：

```bash
python -m pip wheel . --no-deps -w /tmp/rb1 && python -m pip wheel . --no-deps -w /tmp/rb2
# 两次产物 sha256 不同（实测 9ef47792… vs c2b46c8e…）

SOURCE_DATE_EPOCH=1700000000 python -m pip wheel . --no-deps -w /tmp/rb3
SOURCE_DATE_EPOCH=1700000000 python -m pip wheel . --no-deps -w /tmp/rb4
# 两次产物 sha256 相同（实测 bbab5607cf1cab41）
```

### 差异来源（定位到字节）

两个 wheel 大小相同（260244 B），**逐条目内容与条目顺序完全一致**，
差异仅 **12 个字节**，全部落在 zip **局部文件头的 mtime 字段**，且只出现在
**构建期复制生成**的 `audiocheck-1.0.0.dist-info/licenses/LICENSE`
（其余文件沿用源码 mtime，因此稳定）：

```
条目 audiocheck-1.0.0.dist-info/licenses/LICENSE 的 local header：
  构建 A: 504b0304140000000800 3c65 505d …
  构建 B: 504b0304140000000800 4365 505d …
                            ^^^^ DOS 时间（秒）不同
```

⇒ 差异**不是**路径、不是随机性，就是**构建期复制文件的 mtime**。
`SOURCE_DATE_EPOCH`（setuptools ≥ 62 支持）把 mtime 固定后即逐字节一致。

### 意义

reproducible-builds.org 的定位是**验证原语**：它把"信任发布者"变成
"任何人可独立验证"。本轮只做到"**结论 + 差异来源 + 修复开关**"，
**没有**改默认构建流程去强制固定时间戳（那属发布批次 D 的决定）。
