"""判定与建议 —— 必须最后跑，因为它读取前面所有分析器的结论。

## 判定哲学（两条）

**一、同一个截止点在不同容器里含义完全不同。**
44.1k 容器的 19.6kHz 是"轻微带限"，48k 容器的 21.4kHz 却是"疑似升频"。

**二、要区分「保真」与「可用性」，这是两个不同的问题。**
  * 保真：这个文件是不是它宣称的东西？（带宽 / 位深 / 采样率对不上容器）
  * 可用性：能拿它来评什么器材？（削波会掩盖细节、响度战会掩盖动态、
    单声道评不了声场、带宽不够评不了高频）

早期版本把这两件事混成"一个等级 + 若干标签"，导致**判定被单轴垄断**：
带宽达标就给 ok，哪怕这文件削波严重、动态压平、还是单声道。
那对"能不能用它评器材"这个产品核心问题等于没答。
"""
from __future__ import annotations

from ...config import LEVEL_CN
from ..registry import Analyzer, register

#: 等级序：越大越差。用于「取最差」聚合。
_RANK = {"ok": 0, "warn": 1, "bad": 2}


def _worst(*levels: str) -> str:
    best = "ok"
    for lv in levels:
        if lv and _RANK.get(lv, 0) > _RANK.get(best, 0):
            best = lv
    return best


# ============================================================ 轴一：带宽保真
def axis_bandwidth(fs: int, cutoff_hz: float | None, brickwall_db_per_oct: float | None,
                   th) -> tuple[str, str, float | None]:
    """带宽轴。返回 (等级, 文案, 证据强度)。

    证据强度（0–1）：由砖墙陡度推出。**陡的断崖是"上游做了低通"的硬证据；
    缓慢滚降可能只是母带本身如此或麦/话放的频响。**没有这个区分，
    "有损溯源"就只是猜测。
    """
    if cutoff_hz is None:
        # 返回顺序是 (等级, 文案, 证据强度) —— 早先这里写成了 (文案, 等级)，
        # 于是调用方拿到 level="无法判定（无有效内容）"，静默错位。
        return "warn", "无法判定（无有效内容）", None
    k = cutoff_hz / 1000.0
    nyq = fs / 2

    # 证据强度：>24 dB/oct 视为硬砖墙，<8 视为自然滚降
    if brickwall_db_per_oct is None:
        conf = None
    else:
        conf = max(0.0, min(1.0, (brickwall_db_per_oct - 8.0) / 16.0))

    if fs <= 45000:
        if k >= th.cd_full:
            return "ok", "CD 级满带宽", conf
        if k >= th.ltd:
            hard = brickwall_db_per_oct is not None and brickwall_db_per_oct >= 24.0
            if hard:
                return "warn", ("轻微带限（截止 %.1fkHz，砖墙 %.0fdB/oct 陡峭）"
                                "——上游低通，≈320kbps 有损或母带即如此" % (k, brickwall_db_per_oct)), conf
            return "warn", ("轻微带限（截止 %.1fkHz，滚降平缓 %.0fdB/oct）"
                            "——可能只是母带本身的频响，不一定是转码"
                            % (k, brickwall_db_per_oct or 0)), conf
        if k >= th.lossy:
            return "bad", "有损溯源（≈192–256kbps 转码）", conf
        return "bad", "强有损溯源（≤128kbps）", conf

    if fs <= 50000:
        if k >= th.full_48:
            return "ok", "真 48kHz 带宽", conf
        if k >= th.up_44:
            return "bad", "疑似 44.1kHz 升频（内容止于 %.1fkHz）" % k, conf
        if k >= th.ltd:
            return "warn", "带宽偏窄（%.1fkHz）——48k 容器里有损/带限" % k, conf
        if k >= th.lossy:
            return "bad", "有损溯源（≈192–256kbps 转码）", conf
        return "bad", "强有损溯源（≤128kbps）", conf

    if k <= th.hi_ok:
        return "bad", "伪 Hi-Res（容器 %.1fkHz，内容仅到 %.1fkHz）" % (fs / 1000, k), conf
    if cutoff_hz / nyq >= th.hi_ratio:
        return "ok", ("真 Hi-Res（内容到 %.1fkHz，占奈奎斯特 %.0f%%）"
                      % (k, cutoff_hz / nyq * 100)), conf
    return "warn", "半 Hi-Res（内容 %.1fkHz / 容器 %.1fkHz）" % (k, fs / 1000), conf


# ============================================================ 轴二：位深保真
def axis_bitdepth(r: dict, th) -> tuple[str, str]:
    """位深轴。用 codec/bitdepth 的结论核对容器标称。

    **这一轴早期完全没进判定**，于是「24bit 容器装 16bit 内容」这种
    典型的伪 Hi-Res 会被判成"合格"——因为带宽可能确实够。
    """
    padded = r.get("bit_padded")
    origin = r.get("bit_origin")
    claimed = str(r.get("bits") or "").replace("PCM_", "")
    if padded and origin:
        return "warn", "伪位深：容器标 %s，实际 %s bit（%s）" % (claimed, origin, r.get("bit_note") or "")
    if r.get("bit_effective") is not None:
        try:
            eff, cl = int(r["bit_effective"]), int(claimed)
        except (TypeError, ValueError):
            return "ok", ""
        if cl - eff >= 4:
            return "warn", ("有效位深 %d bit（容器标 %d）——低位无内容，"
                            "实际动态余量按 %d bit 算" % (eff, cl, eff))
        return "ok", ""
    return "ok", ""


# ============================================================ 轴三：电平完整性
def axis_level(r: dict, th) -> tuple[str, str]:
    """电平完整性。

    **只有"真削波"驱动等级；"采样间过载"只作提示。** 两者性质不同：

      * 削波：样本本身被截平了，信息真的丢了 —— 文件的问题。
      * 采样间过载（dBTP > 0）：样本都是好的，是 DAC 重建波形时过冲 ——
        播放链的问题。而且现代母带里极其常见，拿它降级会让大半个曲库都"不达标"。

    实测教训：合成测试信号（满刻度正弦）恒有 +0.48 dBTP，
    把它当降级项会让所有合成回归用例从 ok 变 warn，而它们本来没问题。
    """
    clip = r.get("clip_pct") or 0.0
    tp = r.get("true_peak_dbfs")
    msgs, lv = [], "ok"
    if clip > 0.5:
        lv = "bad"
        msgs.append("削波严重 %.3f%%（母带级损坏）" % clip)
    elif clip > th.clip_pct:
        lv = "warn"
        msgs.append("存在削波 %.3f%%" % clip)
    if tp is not None and tp > 0.0:
        msgs.append("采样间过载 %+.2f dBTP（DAC 重建时会削顶，属播放链问题）" % tp)
    return lv, "；".join(msgs)


# ============================================================ 轴四：动态
def axis_dynamics(r: dict, th) -> tuple[str, str]:
    """动态余量。影响的是"能不能拿它测动态"，不一定是文件本身有问题。"""
    lra, dr = r.get("lra"), r.get("dr_db")
    msgs, lv = [], "ok"
    if lra is not None and lra < th.lra_flat:
        lv = "warn"
        msgs.append("LRA %.1f LU（动态压平）" % lra)
    if dr is not None and dr < 6:
        lv = _worst(lv, "warn")
        msgs.append("DR %.1f dB 偏低" % dr)
    if r.get("lufs") is not None and r["lufs"] > th.loud_war_lufs:
        msgs.append("响度战 %.1f LUFS" % r["lufs"])
    return lv, "；".join(msgs)


# ============================================================ 轴五：声道
def axis_channels(r: dict, th) -> tuple[str, str]:
    """声道宽度。影响"能不能测声场"。"""
    wd = r.get("width_db")
    if wd is None:
        return "ok", ""
    if wd < th.width_narrow_db:
        return "warn", "声道宽度 %.1f dB（近乎单声道）" % wd
    return "ok", ""


# ============================================================ 交叉核对
def cross_checks(r: dict, fs: int, cutoff_hz: float | None) -> list[str]:
    """跨分析器的一致性核对 —— 单看任一分析器都发现不了的矛盾。

    这些是"文件自称"与"文件实际"对不上的地方，也是伪 Hi-Res 的主要形态。
    """
    out = []
    k = (cutoff_hz or 0) / 1000.0

    # 1) 24bit 容器 + 16bit 有效位深
    if r.get("bit_padded"):
        claimed = str(r.get("bits") or "").replace("PCM_", "")
        out.append("位深造假：容器标 %s，有效内容只有 %s bit —— 典型伪 Hi-Res 手法"
                   % (claimed, r.get("bit_origin")))

    # 2) 高采样率容器 + 内容止于 44.1k 系
    if fs > 88200 and 0 < k <= 22.5:
        out.append("采样率造假：容器 %.1fkHz 但内容止于 %.1fkHz —— "
                   "像是从 44.1k 系升频而来" % (fs / 1000, k))

    # 3) 有损溯源 + 砖墙平缓 = 证据不足，不能下"转码"的硬结论
    bw = r.get("brickwall_db_per_oct")
    if cutoff_hz and k < 20.0 and bw is not None and bw < 15.0:
        out.append("截止点偏低（%.1fkHz）但滚降平缓（%.0fdB/oct）——"
                   "证据不足以断定有损转码，也可能是母带/录音链的频响" % (k, bw))

    # 4) 编码器串与容器不符（如 FLAC 里写着某有损编码器）
    vendor_raw = (r.get("meta") or {}).get("vendor") or ""
    vendor = vendor_raw.lower().strip()
    fmt = str(r.get("format") or "").lower().strip()
    # 注意两边都要小写比较：早先把 fmt 转成大写再拿 "flac" 去 in，恒为 False，
    # 这条核对等于永不触发（写的时候没跑，测试抓到的）。
    if vendor and fmt and "flac" in fmt and "flac" not in vendor and "libflac" not in vendor:
        out.append("编码器串可疑：容器是 %s，但 vendor 写着「%s」——"
                   "可能是转码时移植了旧标签" % (r.get("format"), vendor_raw))
    return out


# ============================================================ 建议（可用性）
def advice(r: dict, th) -> str:
    """输出「能不能用它来评耳机」——本工具区别于市面产品的地方。"""
    out = []
    cut = (r.get("cutoff_hz") or 0) / 1000.0
    if cut >= 20:
        out.append("高频内容充足（到 %.1fkHz）→ 可用于评估耳机的高频延伸/空气感。" % cut)
    elif cut >= 16:
        out.append("内容止于 %.1fkHz → 评估耳机「高频延伸」不可靠：信息在源头就没了。" % cut)
    elif cut > 0:
        out.append("内容止于 %.1fkHz → 只够评估中频/齿音区，不足以判断高频。" % cut)

    b = r.get("bands_pct") or {}
    if b:
        low = b.get("sub", 0) + b.get("bass", 0)
        if low > 60:
            out.append("低频能量占 %.0f%% → 适合试低频下潜 / 控制力。" % low)
        if b.get("upper_mid", 0) > 15:
            out.append("中高频占比偏高 → 适合试齿音 / 刺耳度。")

    tp = r.get("true_peak_dbfs")
    if tp is not None and tp > 0:
        out.append("真峰值 %+.2f dBTP（超满刻度）→ DAC 重建时可能削顶，别把这点失真算到耳机头上。" % tp)
    if r.get("lufs") is not None and r["lufs"] > th.loud_war_lufs:
        out.append("集成响度 %.1f LUFS（响度战极重）→ 对比时必须先按响度对齐，否则「更响」会被误判成「更好」。"
                   % r["lufs"])
    if r.get("lra") is not None and r["lra"] < th.lra_flat:
        out.append("LRA 仅 %.1f LU（动态被压平）→ 不适合用来判断耳机的动态/微动态表现。" % r["lra"])
    if r.get("dr_db") is not None and r["dr_db"] < 6:
        out.append("DR %.1fdB 偏低 → 做音量匹配盲听时容易把「更响」误判为「更好」。" % r["dr_db"])
    if (r.get("clip_pct") or 0) > 0.01:
        out.append("存在削波 → 大动态段落本身有失真，别算到耳机头上。")
    if r.get("width_db") is not None and r["width_db"] < th.width_narrow_db:
        out.append("声道宽度 %.1f dB（近乎单声道）→ 不适合用它评声场宽度。" % r["width_db"])
    return " ".join(out)


@register
class VerdictAnalyzer(Analyzer):
    key = "verdict"
    label = "判定与建议"
    order = 90
    #: **必须声明真实依赖。** requires 参与拓扑调度（registry 按它挑可跑的），
    #: 早期只写了 basic/spectrum，却读了 loudness/stereo/codec/bitdepth 的字段，
    #: 靠 order=90 恰好最大才没出事——那是运气不是契约。
    #: 谁把 verdict 的 order 调小、或把某个产出者调到 90 之后，判定就会静默降级。
    requires = ("basic", "bitdepth", "spectrum", "loudness", "stereo", "codec", "metadata")

    def run(self, ctx) -> dict:
        if not ctx.deep:           # 浅模式没有频谱，无从判定，也不要谎报"无法判定"
            return {}
        r = ctx.result
        th = ctx.thresholds

        # ---- 五个轴各自判级 ----
        bw_lv, bw_text, conf = axis_bandwidth(
            ctx.fs, r.get("cutoff_hz"), r.get("brickwall_db_per_oct"), th)
        bd_lv, bd_text = axis_bitdepth(r, th)
        lv_lv, lv_text = axis_level(r, th)
        dy_lv, dy_text = axis_dynamics(r, th)
        ch_lv, ch_text = axis_channels(r, th)

        # **轴分两类，聚合规则不同 —— 这是本模块最容易搞错的地方。**
        #
        #   保真轴（fidelity）：文件是不是它宣称的东西。驱动 level。
        #       带宽 / 位深 / 削波
        #   可用性轴（usability）：文件没问题，但能拿它评什么器材受限。**不驱动 level**。
        #       动态 / 响度战 / 声道宽度
        #
        # 早先不分青红皂白对五个轴取最差，结果单声道文件、LRA=0 的合成测试信号
        # 全被判成 warn —— 那些不是"文件不达标"，是"它的用途有限"。
        # 把两件事塞进一个等级，等级就不再表达任何明确的东西。
        # （这个错误是 test_engine 的合成信号用例抓出来的。）
        axes = [("带宽", bw_lv, bw_text, True), ("位深", bd_lv, bd_text, True),
                ("电平", lv_lv, lv_text, True), ("动态", dy_lv, dy_text, False),
                ("声道", ch_lv, ch_text, False)]
        level = _worst(*(lv for _, lv, _, drives in axes if drives))

        # ---- 文案：带宽是主结论，其余各轴只在其"非 ok"时追加 ----
        head = bw_text
        others = [t for _, lv, t, _ in axes[1:] if t and lv != "ok"]
        if others:
            head += "｜" + "｜".join(others)
        # 带宽本身没问题、却因保真轴的其它项降级时，说清原因
        if bw_lv == "ok" and level != "ok":
            head = "（带宽无问题，但因其它维度降级）" + head
        if conf is not None and conf < 0.5 and cutoff_hz_lossy(r):
            head += "｜⚠ 低频段证据不足，结论偏保守"

        out = {
            "verdict": head,
            "level": level,
            "level_cn": LEVEL_CN.get(level, level),
            # 结构化输出：UI 可按轴展示，也便于以后加轴而不动解析。
            # drives_level 一并给出，避免调用方误以为所有轴都能降级。
            "verdict_axes": {name: {"level": lv, "text": t, "drives_level": d}
                             for name, lv, t, d in axes},
            "bandwidth_confidence": conf,
        }
        cc = cross_checks(r, ctx.fs, r.get("cutoff_hz"))
        if cc:
            out["cross_checks"] = cc
        out["advice"] = advice({**r, **out}, th)
        return out


def cutoff_hz_lossy(r: dict) -> bool:
    """截止点是"有损级"的（<20kHz）吗——用于决定要不要提示证据不足。"""
    c = r.get("cutoff_hz")
    return bool(c) and c < 20000
