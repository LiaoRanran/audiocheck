"""从 IBM/plex 全仓 tarball 里只抽出 Sans/Mono 的静态 ttf（streaming 单遍）。

新版仓库已重组：fonts 在 `packages/plex-sans/fonts/complete/ttf/`，
静态文件名为 `IBMPlexSans-Regular.ttf`（无连字符）。只用 stdlib，避开
git-bash tar 对 `C:/` 的 "Cannot connect to C:" 坑。

用法：python prototypes/_extract_plex.py <tarball> <目标目录>
"""
import os
import shutil
import sys
import tarfile

SRC = sys.argv[1]
DST = sys.argv[2]
os.makedirs(DST, exist_ok=True)

JOBS = [("packages/plex-sans/", "IBMPlexSans-"), ("packages/plex-mono/", "IBMPlexMono-")]
WEIGHTS = ("Regular", "Medium", "SemiBold", "Bold")
TOTAL = len(JOBS) * len(WEIGHTS)

done, n = set(), 0
with tarfile.open(SRC, "r:gz") as tf:
    for m in tf:
        if not m.isfile():
            continue
        nm = m.name.replace("\\", "/")
        b = nm.rsplit("/", 1)[-1]
        if not nm.endswith(".ttf") or "Var" in b:        # 跳过可变字体包
            continue
        for pkg, base in JOBS:
            if pkg not in nm or b in done:
                continue
            for w in WEIGHTS:
                if b == base + w + ".ttf":
                    with tf.extractfile(m) as f, open(os.path.join(DST, b), "wb") as o:
                        shutil.copyfileobj(f, o)
                    done.add(b)
                    n += 1
                    print("extracted", b)
                    break
        if len(done) >= TOTAL:
            break

want = {base + w + ".ttf" for _, base in JOBS for w in WEIGHTS}
print("TOTAL %d / %d" % (n, TOTAL))
miss = sorted(want - done)
if miss:
    print("MISSING", miss)
