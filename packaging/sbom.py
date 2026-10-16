#!/usr/bin/env python3
"""sbom：生成 **SBOM**（软件物料清单，E2.2）——离线，不联网。

## 为什么

E2 要求"生成 SBOM（覆盖运行时依赖 + GUI 侧），SBOM 进发布物"。
依据：Python 官方打包文档的 SBOM 专章 / CycloneDX。做法是从**已安装分发的
元数据**（`importlib.metadata`）读名字与版本，不联网、不解析锁文件——
装了什么就报什么，**不猜**。

## 覆盖范围

以本项目声明的依赖为根（`pyproject.toml` 的 `dependencies` + `gui` extra），
沿 `Requires-Dist` 递归收**传递依赖**，输出 CycloneDX 风格的 JSON。

## 依赖审计（pip-audit）

E2 还要求"零已知高危"。`pip-audit` 需要联网查漏洞库，**本机离线**——
所以这里**不假装审计过**：`--audit` 只在装了 `pip-audit` 时调用，否则
明确打印"未做（离线 / 未安装）"，并写进产物。

    python packaging/sbom.py [--out out/sbom.json] [--audit]
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import os
import re
import shutil
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _declared_roots() -> list[str]:
    """从 pyproject 读根依赖名（dependencies + gui extra）。"""
    txt = open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8").read()
    names: list[str] = []
    for block in ("dependencies", "gui"):
        m = re.search(r"^%s\s*=\s*\[(.*?)\]" % block, txt, re.S | re.M)
        if not m:
            continue
        for item in re.findall(r'"([^"]+)"', m.group(1)):
            names.append(re.split(r"[<>=!\[; ]", item.strip())[0].lower())
    return names


def _reqs(dist: md.Distribution) -> list[str]:
    out = []
    for r in dist.requires or []:
        if ";" in r:                       # 跳过环境标记不满足的（如 platform_system）
            cond = r.split(";", 1)[1]
            if "extra ==" in cond:         # extra 依赖只在该 extra 启用时算
                continue
        name = r.split(";")[0].strip()
        out.append(re.split(r"[<>=!\[ ]", name)[0].lower())
    return out


def collect() -> list[dict]:
    by_name = {d.metadata["Name"].lower(): d for d in md.distributions()
               if d.metadata and d.metadata.get("Name")}
    seen: dict[str, dict] = {}
    stack = [n for n in _declared_roots() if n in by_name]
    roots = set(stack)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        d = by_name[n]
        seen[n] = {
            "type": "library",
            "name": d.metadata["Name"],
            "version": d.version,
            "purl": "pkg:pypi/%s@%s" % (d.metadata["Name"].lower(), d.version),
            "properties": [{"name": "audiocheck:root", "value": "true" if n in roots else "false"}],
        }
        for r in _reqs(d):
            if r in by_name and r not in seen:
                stack.append(r)
    return sorted(seen.values(), key=lambda c: c["name"].lower())


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 SBOM（离线，CycloneDX 风格）")
    ap.add_argument("--out", default=os.path.join("out", "sbom.json"))
    ap.add_argument("--audit", action="store_true", help="装了 pip-audit 时跑依赖审计")
    a = ap.parse_args()

    comps = collect()
    try:
        app_ver = md.version("audiocheck")
    except md.PackageNotFoundError:
        app_ver = "unknown"

    audit_note = "未做（本机离线 / 未安装 pip-audit）"
    if a.audit:
        if shutil.which("pip-audit"):
            p = subprocess.run(["pip-audit", "-f", "json"], capture_output=True, text=True)
            audit_note = "已跑 pip-audit（rc=%d）" % p.returncode
            if p.stdout.strip():
                try:
                    vul = json.loads(p.stdout)
                    highs = [v for v in vul.get("dependencies", [])
                             for v in v.get("vulns", []) if v.get("id")]
                    audit_note += "；发现 %d 条漏洞记录" % len(highs)
                except json.JSONDecodeError:
                    audit_note += "；输出无法解析"
        else:
            audit_note = "未做（pip-audit 未安装）"

    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "component": {"type": "application", "name": "audiocheck", "version": app_ver},
            "properties": [
                {"name": "audiocheck:sbom_generator", "value": "packaging/sbom.py"},
                {"name": "audiocheck:audit", "value": audit_note},
                {"name": "audiocheck:python", "value": sys.version.split()[0]},
            ],
        },
        "components": comps,
    }
    out = os.path.join(_ROOT, a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(bom, f, ensure_ascii=False, indent=1)

    print("SBOM：%d 个组件（含传递依赖）→ %s" % (len(comps), a.out))
    for c in comps:
        print("  %-22s %s" % (c["name"], c["version"]))
    print("依赖审计：%s" % audit_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
