"""B10 竞品轮的总入口：出 8 张图 + 三项自检。

## 为什么不用 `python prototypes/skeleton_sX.py` 直接跑

**本机这个调用方式会静默吞输出**（实测：exit 0、stderr 全空；崩溃时
stdout 缓冲随进程丢失）。已验证可用的方式是 `python -u -c "... exec(...)"`
——把源码读进来 exec，`__name__` 显式给成 `__main__`。本脚本就是它的封装。

教训（比 bug 本身更坑）：**这个环境里要看真话，得同时满足三条**——
`-u`（不缓冲）、无输出过滤、以及这种包装调用。前两条我上一轮都违反了，
所以只看到"什么都没发生"。

## 生成模板后必须空跑一次

`qss()` 会被 `%` 格式化，**模板里任何形态不对的占位符都在这一步暴露**
（实测三连：`%({d2})s` / 重建丢文件头 / `%( vs)s` 多一个空格）。
所以本脚本第一件事就是空跑一次 `qss()`——把这类错误堵在"出图之前"。
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTO = os.path.join(ROOT, "prototypes")
sys.path.insert(0, PROTO)

import _kit as K  # noqa: E402


def selfcheck() -> None:
    """① 模板空跑 ② 数据非空非直线 ③ 共享资产在场（字体/判定色）。"""
    css = K.qss()
    assert "None" not in css, "qss 里出现了 None（占位符与键名不匹配）"
    print("[自检] qss() 空跑通过（%d 字符）" % len(css))
    for c in K.cases():
        K.assert_curve(c["curve"])
    print("[自检] 真实曲线 2 条：非空、非直线、无 NaN")
    print("[自检] 字体:", K._fonts())
    print("[自检] 判定色 band:", {lv: K.band(lv) for lv in ("ok", "warn", "bad")})


def run_one(tag: str) -> bool:
    path = os.path.join(PROTO, "skeleton_%s.py" % tag)
    src = open(path, encoding="utf-8").read()
    print("--- %s（%d 行）---" % (tag, src.count("\n")))
    try:
        # __name__ 显式给成 __main__，骨架文件底部的入口才会跑
        exec(compile(src, path, "exec"),
             {"__name__": "__main__", "__file__": path})
        return True
    except Exception:
        traceback.print_exc()
        return False


if __name__ == "__main__":
    K.app()
    selfcheck()
    ok = all([run_one(t) for t in ("s1", "s2", "s3", "s4")])
    shots = sorted(f for f in os.listdir(K.SHOTS)) if os.path.isdir(K.SHOTS) else []
    print("\nshots/b10: %d 张 → %s" % (len(shots), shots))
    sys.exit(0 if ok and len(shots) == 8 else 1)
