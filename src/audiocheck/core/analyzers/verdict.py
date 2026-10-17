"""判定与建议 —— 必须最后跑，因为它读取前面所有分析器的结论。

## 判定哲学（三条）

**一、同一个截止点在不同容器里含义完全不同。**
44.1k 容器的 19.6kHz 是"轻微带限"，48k 容器的 21.4kHz 却是"疑似升频"。

**二、要区分「保真」与「可用性」，这是两个不同的问题。**
  * 保真：这个文件是不是它宣称的东西？（带宽 / 位深 / 采样率对不上容器）
  * 可用性：能拿它来评什么器材？（削波会掩盖细节、响度战会掩盖动态、
    单声道评不了声场、带宽不够评不了高频）

早期版本把这两件事混成"一个等级 + 若干标签"，导致**判定被单轴垄断**：
带宽达标就给 ok，哪怕这文件削波严重、动态压平、还是单声道。
那对"能不能用它评器材"这个产品核心问题等于没答。

**三、C4 起分三层输出，不许压平**（用户 2026-10-13："判决颗粒度一定要根据
现实音源复杂情况极致真实丰富"）：
  * 现象层 `phenomena` / `verdict_axes`：测量值 + 等级（本模块）；
  * 成因层 `causes`：现象 → 候选集（`causes.py`，order=89）；
  * 用途层 `usability`：成因 + 现象 → 每格"可用/受限/不可用/不可判" + 依据。
时间定位 `timeline` 另见 `timeline.py`；三层的效力边界 `boundaries`。
"""
from __future__ import annotations

from ...config import LEVEL_CN
from ..registry import Analyzer, register
from .causes import USES, usability_matrix

#: 等级序：越大越差。用于「取最差」聚合。
_RANK = {"ok": 0, "warn": 1, "bad": 2}

#: 未知等级按**最差**处理。
#:
#: ⚠ C0-4：原实现是 `_RANK.get(lv, 0)` —— 未知等级**静默取 0**，也就是"最好"。
#: 后果：将来任何一处写错等级名（或新增等级忘了登记），聚合结果会**悄悄偏向 ok**，
#: 而症状是"明明有问题却判合格"，最难发现的那一类。
#:
#: 为什么不改成"显式抛异常"：`_worst` 在聚合路径上，抛异常会被引擎吞成
#: "分析器告警"，最终表现是**整段判定消失**（用户看到空白，不是警告）——
#: 那比"判成最差"更糟。按最差处理的症状是可见的（没问题却判 bad），会被抓出来。
_RANK_UNKNOWN = 99


def _worst(*levels: str) -> str:
    best, best_rank = "ok", 0
    for lv in levels:
        if not lv:
            continue
        r = _RANK.get(lv, _RANK_UNKNOWN)
        if r > best_rank:
            best, best_rank = lv, r
    return best


def _soft_band_text(k: float, slope: float | None, strong: bool) -> str:
    """C4.6：**砖墙证据不足**时，带限类判定的降级文案。

    "疑似带限"而不是"有损溯源"——后者是对**成因**的硬断言，需要陡砖墙
    作证据。这就是"证据不足不硬判"在等级上的落地（causes 层再给结构化候选）。
    """
    return ("疑似%s（截止 %.1fkHz，滚降平缓 %.0fdB/oct）——砖墙证据不足："
            "无法断定有损转码，也可能是母带/录音链的频响（工程估计，无出处）"
            % ("强带限" if strong else "带限", k, slope or 0.0))


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
        conf = max(0.0, min(1.0,
                   (brickwall_db_per_oct - th.conf_slope_lo) / th.conf_slope_span))

    if fs <= th.fs_44_container:
        if k >= th.cd_full:
            return "ok", "CD 级满带宽", conf
        if k >= th.ltd:
            hard = brickwall_db_per_oct is not None and brickwall_db_per_oct >= th.brick_db
            if hard:
                return "warn", ("轻微带限（截止 %.1fkHz，砖墙 %.0fdB/oct 陡峭）"
                                "——上游低通，≈320kbps 有损或母带即如此" % (k, brickwall_db_per_oct)), conf
            return "warn", ("轻微带限（截止 %.1fkHz，滚降平缓 %.0fdB/oct）"
                            "——可能只是母带本身的频响，不一定是转码"
                            % (k, brickwall_db_per_oct or 0)), conf
        if k >= th.lossy:
            # C4.6：砖墙证据不足（conf 低）⇒ **不得判 bad，只能 warn**。
            # "有损溯源"是对成因的硬断言；滚降平缓时它同样可能是母带频响。
            if conf is not None and conf < th.conf_low:
                return "warn", _soft_band_text(k, brickwall_db_per_oct, strong=False), conf
            return "bad", "有损溯源（≈192–256kbps 转码）", conf
        if conf is not None and conf < th.conf_low:
            return "warn", _soft_band_text(k, brickwall_db_per_oct, strong=True), conf
        return "bad", "强有损溯源（≤128kbps）", conf

    if fs <= th.fs_48_container:
        if k >= th.full_48:
            return "ok", "真 48kHz 带宽", conf
        if k >= th.up_44:
            return "bad", "疑似 44.1kHz 升频（内容止于 %.1fkHz）" % k, conf
        if k >= th.ltd:
            return "warn", "带宽偏窄（%.1fkHz）——48k 容器里有损/带限" % k, conf
        if k >= th.lossy:
            # C4.6：砖墙证据不足（conf 低）⇒ **不得判 bad，只能 warn**。
            # "有损溯源"是对成因的硬断言；滚降平缓时它同样可能是母带频响。
            if conf is not None and conf < th.conf_low:
                return "warn", _soft_band_text(k, brickwall_db_per_oct, strong=False), conf
            return "bad", "有损溯源（≈192–256kbps 转码）", conf
        if conf is not None and conf < th.conf_low:
            return "warn", _soft_band_text(k, brickwall_db_per_oct, strong=True), conf
        return "bad", "强有损溯源（≤128kbps）", conf

    if k <= th.hi_ok:
        return "bad", "伪 Hi-Res（容器 %.1fkHz，内容仅到 %.1fkHz）" % (fs / 1000, k), conf
    if cutoff_hz / nyq >= th.hi_ratio:
        return "ok", ("真 Hi-Res（内容到 %.1fkHz，占奈奎斯特 %.0f%%）"
                      % (k, cutoff_hz / nyq * 100)), conf
    return "warn", "半 Hi-Res（内容 %.1fkHz / 容器 %.1fkHz）" % (k, fs / 1000), conf


# ============================================================ 带宽判定边界余量
def bandwidth_margin(fs: int, cutoff_hz: float | None, th) -> dict | None:
    """带宽轴**离最近一条判定线多远**（kHz 与 %）。

    为什么要有它（2026-10-17 接手时补）：带宽等级是一条 `if k >= 门限` 的链，
    相邻两级之间**没有过渡带**——真实曲库实测：96k 的「伪 Hi-Res / 半 Hi-Res」
    两档在谱上只差约 2 dB（24–30 kHz：−44.4 vs −42.0 dB），判定却跨两级，
    全压在 `hi_ok=24.5` 上。

    **门限一个都没动**（拿一份曲库去调参是不允许的，见 `AGENTS.md` §3.4；
    要做对需要**独立标注**）。这里只做**披露**：把"离分界多远"量出来，
    让"贴着线"的结论自己露出来，而不是假装它很确定。

    返回 None 表示无从计算（没有截止点）。**刻意不设"敏感/不敏感"的二元判据**
    ——那需要一个新的门限，而新门限必须有出处；这里只报测量值。
    """
    if cutoff_hz is None:
        return None
    k = cutoff_hz / 1000.0
    nyq = fs / 2.0
    if fs <= th.fs_44_container:
        lines = [(th.cd_full, "CD 级满带宽"), (th.ltd, "轻微带限"),
                 (th.lossy, "有损溯源")]
    elif fs <= th.fs_48_container:
        lines = [(th.full_48, "真 48kHz"), (th.up_44, "疑似 44.1kHz 升频"),
                 (th.ltd, "带宽偏窄"), (th.lossy, "有损溯源")]
    else:
        lines = [(th.hi_ok, "伪 Hi-Res"), (th.hi_ratio * nyq / 1000.0, "真 Hi-Res")]
    line_khz, label = min(lines, key=lambda kv: abs(k - kv[0]))
    delta = k - line_khz
    return {
        "line_khz": round(line_khz, 2),
        "line_label": label,
        "delta_khz": round(delta, 2),
        "delta_pct": round(100.0 * delta / line_khz, 2) if line_khz else None,
    }


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
        if cl - eff >= th.bit_gap_warn:
            return "warn", ("有效位深 %d bit（容器标 %d）——低位无内容，"
                            "实际动态余量按 %d bit 算" % (eff, cl, eff))
        return "ok", ""
    # C0-4：`bit_effective` 缺失时必须**区分"不适用"与"查不到"**。
    # 分析器的 `bit_verdict` 早就把两者分开写了，这里只是原先没读它：
    #   * "不适用（浮点或 32bit 整数）" ⇒ 位深概念本就不适用，ok 是对的；
    #   * "无法统计" ⇒ 分析器真的没测出来。**返回 ok 就等于"查不到与没问题长得一样"**
    #     ——那是本项目最忌讳的失败方式（`AGENTS.md` §3.6）。用 warn 表达"有疑"。
    if "无法统计" in str(r.get("bit_verdict") or ""):
        return "warn", "位深无法统计——查不到 ≠ 没问题，容器标称未获核对"
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
    if clip > th.clip_severe_pct:
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
    if dr is not None and dr < th.dr_low_db:
        lv = _worst(lv, "warn")
        msgs.append("DR %.1f dB 偏低" % dr)
    if r.get("lufs") is not None and r["lufs"] > th.loud_war_lufs:
        msgs.append("响度战 %.1f LUFS" % r["lufs"])
    return lv, "；".join(msgs)


# ============================================================ 轴五：声道
def axis_channels(r: dict, th) -> tuple[str, str]:
    """声道宽度 + **局部反相**。影响"能不能测声场"。

    ⚠ 2026-10-16：`anti_scope` 此前**只算不判**——`dsp.anti_phase_scan` 的
    docstring 写着"给判定用"，但判定层从没读过它（实测 grep：只出现在
    stereo.py 的指标注册与 dsp.py 的计算里）。这条轴现在消费它。
    反相**不是成因**（它不说明文件从哪来），而是**现象层的立体声缺陷**：
    错线、反相叠加、处理链极性翻转都会留下"只在某频段/某时段反相"。
    """
    msgs, lv = [], "ok"
    wd = r.get("width_db")
    if wd is not None and wd < th.width_narrow_db:
        lv = "warn"
        msgs.append("声道宽度 %.1f dB（近乎单声道）" % wd)
    scope = r.get("anti_scope")
    if scope in ("明显", "严重"):
        lv = _worst(lv, "warn")
        pct = r.get("anti_energy_pct")
        band = r.get("anti_band_khz")
        msgs.append("局部反相 %s（反相能量 %s%%%s）"
                    % (scope, "%.3f" % pct if pct is not None else "?",
                       "，集中在 %.2fkHz" % band if band is not None else ""))
    return lv, "；".join(msgs)


# ============================================================ 交叉核对
def cross_checks(r: dict, fs: int, cutoff_hz: float | None, th) -> list[str]:
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
    if fs > th.upsample_fs and 0 < k <= th.upsample_content_khz:
        out.append("采样率造假：容器 %.1fkHz 但内容止于 %.1fkHz —— "
                   "像是从 44.1k 系升频而来" % (fs / 1000, k))

    # 3) 有损溯源 + 砖墙平缓 = 证据不足，不能下"转码"的硬结论
    bw = r.get("brickwall_db_per_oct")
    if cutoff_hz and k < th.cutoff_low_khz and bw is not None and bw < th.brick_flat_db_per_oct:
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


# ============================================================ 建议（由用途矩阵渲染）
#: advice **不单独判**——只把 usability_matrix 的格子渲染成句子。
#: 好处：建议与矩阵永远是同一套口径（不会"矩阵说受限、建议说可用"，
#: 那正是老实现两处各写一遍 DR 门限差点造成的事故）。
#: `--only verdict` 时 causes/usability 没跑：就地算一份（不报错、不空转）。
#: 这几格在"可用"时也渲染（老口径里的正提示：满带宽/低频突出/齿音素材）。
_ADVICE_POSITIVE = ("频响延伸/空气感", "低频下潜/控制力", "齿音/刺耳度")
#: 静态格/重复格不进 advice（结像与瞬态在 boundaries，音色分布在 CLI 平衡行）
_ADVICE_SKIP = ("结像", "瞬态", "音色平衡", "整体可信度")


def advice(r: dict, th) -> str:
    """「能不能用它来评耳机」——由用途矩阵渲染（C4.2 替代原 7 条粗糙规则）。"""
    u = r.get("usability") or usability_matrix(r, r.get("causes") or [], th)
    parts = []
    for name in USES:
        if name in _ADVICE_SKIP:
            continue
        c = u.get(name)
        if not c or not c.get("reason"):
            continue
        if c["level"] != "可用" or name in _ADVICE_POSITIVE:
            parts.append(c["reason"])
    return " ".join(parts)


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
        # C4.4：分隔符从"｜"改"；"——"｜"把多个现象糊成一行正是本轮要消灭的
        # 形态（成因另起 `causes` 层，用途另起 `usability` 层，都不是一行能装的）。
        head = bw_text
        others = [t for _, lv, t, _ in axes[1:] if t and lv != "ok"]
        if others:
            head += "；" + "；".join(others)
        # 带宽本身没问题、却因保真轴的其它项降级时，说清原因
        if bw_lv == "ok" and level != "ok":
            head = "（带宽无问题，但因其它维度降级）" + head
        # ⚠ C4.6 起这里**不再**追加"证据不足"第二条提示：砖墙不足时
        # axis_bandwidth 已就地降级为 warn 并把理由写进文案。同一件事说两遍
        # 正是 C4.4 要消灭的"糊"。（原提示与 cutoff_hz_lossy 一并删除。）

        # C4.4 现象层的**列表形态**（`verdict_axes` 是 dict，给 UI 按轴取；
        # `phenomena` 给按序渲染/机器消费，各带测量值）。
        vals = {"带宽": r.get("cutoff_khz"), "位深": r.get("bit_effective"),
                "电平": r.get("clip_pct"), "动态": r.get("lra"), "声道": r.get("width_db")}
        phenomena = [{"axis": name, "value": vals.get(name), "level": lv, "text": t,
                      "drives_level": d} for name, lv, t, d in axes]

        out = {
            "verdict": head,
            "level": level,
            "level_cn": LEVEL_CN.get(level, level),
            # 结构化输出：UI 可按轴展示，也便于以后加轴而不动解析。
            # drives_level 一并给出，避免调用方误以为所有轴都能降级。
            "verdict_axes": {name: {"level": lv, "text": t, "drives_level": d}
                             for name, lv, t, d in axes},
            "phenomena": phenomena,              # C4.4：同一份现象层的列表形态
            "bandwidth_confidence": conf,
            # 判定边界余量（2026-10-17）：带宽等级没有过渡带，这里披露"离最近
            # 一条判定线多远"——**门限没动**，动的是可见性。见 bandwidth_margin。
            "bandwidth_margin": bandwidth_margin(ctx.fs, r.get("cutoff_hz"), th),
        }
        cc = cross_checks(r, ctx.fs, r.get("cutoff_hz"), th)
        if cc:
            out["cross_checks"] = cc
        out["advice"] = advice({**r, **out}, th)
        return out
