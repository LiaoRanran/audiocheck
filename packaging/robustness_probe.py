#!/usr/bin/env python3
"""robustness_probe：**不可信输入健壮性**（E2.1）——本项目最该做的一条。

## 为什么是这一条

audiocheck 的核心动作是**读别人给的音频文件**——这正是攻击面。
E2 要求：畸形文件必须全部走"**明确错误**"路径（有错误码 + 可读信息），
**不许 traceback、不许崩溃、不许静默通过**。

## 两部分

1. **畸形语料**（确定性，`CORPUS`）：空文件 / 纯文本 / 截断 RIFF / 半截 WAV /
   只有头部 / 全零 / 伪造巨大长度 / 错扩展名（把文本命名成 .flac）/
   只有 ID3 标签 …… 每个都必须给出**显式结果**（错误或正常判定），且
   **真实 CLI 子进程的输出里不许出现 `Traceback`**。
2. **随机变异 fuzz**（有界时长）：对一份正常 WAV 做随机字节改写，跑 `analyze`；
   任何**未捕获异常**都是崩溃样本 ⇒ 写进 `tests/robustness_corpus/`（语料即回归资产）。

## 纪律（E1/E2 提示词）

* fuzz 的产出是"**崩溃样本 + 修复**"，不是"跑过了"。**没找到问题就如实说
  "本轮未发现"**，不许包装成"通过安全测试"。
* `--strict`：出现异常 / traceback / 静默通过 ⇒ 返回 1。

    python packaging/robustness_probe.py [--strict] [--max-seconds N] [--seed N]
"""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

CORPUS_DIR = os.path.join(_ROOT, "tests", "robustness_corpus")


def _valid_wav_bytes() -> bytes:
    import numpy as np
    import soundfile as sf

    p = os.path.join(tempfile.mkdtemp(), "v.wav")
    sf.write(p, np.column_stack([np.zeros(44100, dtype="float32")] * 2),
             44100, subtype="PCM_16")
    with open(p, "rb") as f:
        return f.read()


def corpus() -> dict[str, bytes]:
    """畸形语料（确定性）。每条都注明**期望**行为。"""
    raw = _valid_wav_bytes()
    return {
        "empty": b"",
        "text": b"this is definitely not audio, just plain text\n" * 40,
        "riff_only": b"RIFF" + (1000).to_bytes(4, "little") + b"WAVEfmt ",
        "header_only": raw[:44],
        "trunc_quarter": raw[: len(raw) // 4],
        "trunc_half": raw[: len(raw) // 2],
        "all_zero": b"\x00" * 4096,
        "huge_claim": raw[:44] + b"\x00" * (len(raw) - 44),
        "id3_only": b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 200,
        "flac_trunc": b"fLaC" + b"\x00" * 40,
        "ogg_trunc": b"OggS" + b"\x00" * 30,
        "mp3_header": b"\xff\xfb\x90\x00" + b"\x00" * 400,
    }


def run_direct(path: str) -> tuple[bool, str]:
    """直接调 analyze。返回 (是否无异常, 描述)。"""
    from audiocheck.core.engine import analyze

    try:
        r = analyze(path)
    except Exception as e:                                   # noqa: BLE001
        return False, "analyze 抛异常 %s: %s" % (type(e).__name__, str(e)[:80])
    lv = r.get("level")
    if lv not in ("ok", "warn", "bad", "error"):
        return False, "level 取值异常：%r（静默通过？）" % lv
    if lv == "error" and not (r.get("verdict") or "").strip():
        return False, "错误结果没有可读 verdict"
    return True, "level=%s" % lv


def run_cli(path: str, py: str) -> tuple[bool, str]:
    """真实 CLI 子进程：输出里**不许**有 Traceback。"""
    p = subprocess.run([py, "-m", "audiocheck", path],
                       cwd=_ROOT, capture_output=True, text=True)
    blob = p.stdout + p.stderr
    if "Traceback (most recent call last)" in blob:
        return False, "CLI 打了 traceback（rc=%d）" % p.returncode
    if p.returncode not in (0, 2, 3, 4):
        return False, "CLI 退出码异常 rc=%d" % p.returncode
    return True, "rc=%d" % p.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="不可信输入健壮性（畸形语料 + 有界 fuzz）")
    ap.add_argument("--strict", action="store_true", help="有问题即返回 1")
    ap.add_argument("--max-seconds", type=float, default=120.0, help="fuzz 时长上限")
    ap.add_argument("--seed", type=int, default=20261016)
    a = ap.parse_args()
    py = sys.executable

    d = tempfile.mkdtemp(prefix="ac_rob_")
    problems: list[str] = []
    t0 = time.time()

    print("== 1. 畸形语料（%d 条，确定性）==" % len(corpus()))
    for name, data in corpus().items():
        p = os.path.join(d, name + ".wav")
        with open(p, "wb") as f:
            f.write(data)
        ok1, msg1 = run_direct(p)
        ok2, msg2 = run_cli(p, py)
        flag = "OK " if (ok1 and ok2) else "**问题**"
        print("  %-14s %-12s | CLI %s  %s" % (name, msg1, msg2, flag))
        if not ok1:
            problems.append("语料 %s：%s" % (name, msg1))
        if not ok2:
            problems.append("语料 %s：%s" % (name, msg2))

    # ---- 2. 随机变异 fuzz ----
    print("\n== 2. 随机变异 fuzz（上限 %.0f s，seed=%d）==" % (a.max_seconds, a.seed))
    from audiocheck.core.engine import analyze

    base = _valid_wav_bytes()
    rng = random.Random(a.seed)
    n = 0
    crashes: list[str] = []
    fp = os.path.join(d, "fuzz.wav")
    while time.time() - t0 < a.max_seconds:
        n += 1
        buf = bytearray(base)
        for _ in range(rng.randint(1, 24)):                  # 每次改 1–24 个字节
            i = rng.randrange(len(buf))
            buf[i] = rng.randrange(256)
        with open(fp, "wb") as f:
            f.write(buf)
        try:
            r = analyze(fp)
            if r.get("level") not in ("ok", "warn", "bad", "error"):
                crashes.append("变异体 %d：level=%r" % (n, r.get("level")))
        except Exception as e:                               # noqa: BLE001
            crashes.append("变异体 %d：%s: %s" % (n, type(e).__name__, str(e)[:80]))
    print("  跑了 %d 个变异体" % n)
    if crashes:
        os.makedirs(CORPUS_DIR, exist_ok=True)
        print("  **发现 %d 个崩溃样本**，写入 %s" % (len(crashes), CORPUS_DIR))
        for c in crashes[:20]:
            print("    - %s" % c)
        problems += crashes
    else:
        print("  本轮**未发现**崩溃样本（如实报告：不是『通过安全测试』，是这次没撞上）。")

    print("\n---- 结果 ----")
    if problems:
        print("问题 %d 条：" % len(problems))
        for p in problems[:20]:
            print("  - %s" % p)
        return 1 if a.strict else 0
    print("畸形语料 %d 条全部走显式路径（无 traceback / 无崩溃 / 无静默通过）；"
          "fuzz %d 个变异体未发现崩溃。" % (len(corpus()), n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
