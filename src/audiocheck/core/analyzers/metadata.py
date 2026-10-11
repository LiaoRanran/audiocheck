"""容器内附带的标签信息。

为什么值得单独一层：标签是**可伪造**的（年份、采样率都能改），
但**编码器串**很难伪造——`vendor` 写着 `reference libFLAC 1.2.1` 还是 `Lavf58.76.100`，
往往能反推出真实来路。实测中甚至遇到过注释里直接留着下载源 URL 的文件。
"""
from __future__ import annotations

from collections import OrderedDict

from ..registry import Analyzer, register

#: 统一字段名 → 各容器可能使用的标签键
FIELD_MAP = OrderedDict([
    ("title", ["title", "TIT2", "©nam"]),
    ("artist", ["artist", "TPE1", "©ART"]),
    ("album", ["album", "TALB", "©alb"]),
    ("albumartist", ["albumartist", "album artist", "TPE2", "aART"]),
    ("date", ["date", "year", "TDRC", "TYER", "©day"]),
    ("genre", ["genre", "TCON", "©gen"]),
    ("track", ["tracknumber", "track", "TRCK", "trkn"]),
    ("disc", ["discnumber", "disc", "TPOS", "disk"]),
    ("composer", ["composer", "TCOM", "©wrt"]),
    ("label", ["organization", "label", "publisher", "TPUB", "LABEL", "©pub"]),
    ("isrc", ["isrc", "ISRC"]),
    ("barcode", ["barcode", "upc", "UPC", "BARCODE"]),
    ("copyright", ["copyright", "TCOP", "cprt", "©cpy"]),
    ("encoder", ["encoder", "encoded_by", "TENC", "©too", "ENCODER"]),
    ("comment", ["comment", "COMM", "©cmt", "description", "DESCRIPTION"]),
    ("lyrics", ["lyrics", "LYRICS", "USLT", "©lyr", "UNSYNCEDLYRICS"]),
    ("conductor", ["conductor"]),
    ("performer", ["performer"]),
    ("catalognumber", ["catalognumber", "catalog"]),
    ("replaygain", ["replaygain_track_gain", "REPLAYGAIN_TRACK_GAIN"]),
])

FIELD_CN = {
    "title": "标题", "artist": "艺术家", "album": "专辑", "albumartist": "专辑艺术家",
    "date": "日期", "genre": "流派", "track": "音轨", "disc": "碟号", "composer": "作曲",
    "label": "出版商 / 厂牌", "isrc": "ISRC", "barcode": "条码 / UPC", "copyright": "版权",
    "encoder": "编码器", "comment": "注释", "lyrics": "歌词", "conductor": "指挥",
    "performer": "演奏", "catalognumber": "目录号", "replaygain": "ReplayGain",
}

LONG_FIELDS = {"comment", "lyrics"}


def _flat(v):
    if isinstance(v, (list, tuple)):
        return " / ".join(str(x) for x in v)
    return str(v)


def read_tags(path: str) -> dict:
    """读取标签，返回 {fields, vendor, pictures, tags_total, error?}。"""
    out: dict = {"fields": OrderedDict(), "vendor": None, "pictures": 0, "tags_total": 0}
    try:
        import mutagen
    except ImportError:
        out["error"] = "未安装 mutagen（pip install mutagen）"
        return out

    try:
        f = mutagen.File(path)
    except Exception as e:                                        # noqa: BLE001
        out["error"] = "读取失败: %s" % e
        return out
    if f is None:
        out["error"] = "无标签信息"
        return out

    if type(f).__name__ == "FLAC":
        try:
            out["vendor"] = f.tags.vendor
        except Exception:                                          # noqa: BLE001
            pass
    out["pictures"] = len(getattr(f, "pictures", []) or [])

    tags = getattr(f, "tags", None) or {}
    raw: dict = {}
    for k in list(tags.keys()):
        try:
            raw[str(k).lower()] = tags[k]
        except Exception:                                          # noqa: BLE001
            continue
    out["tags_total"] = len(tags)

    for canon, keys in FIELD_MAP.items():
        for k in keys:
            if k.lower() in raw:
                val = _flat(raw[k.lower()])
                if val.strip():
                    out["fields"][canon] = val
                    break
    if not out["vendor"]:
        out["vendor"] = out["fields"].get("encoder")
    return out


def tag_rows(md: dict) -> list[tuple[str, str]]:
    """给界面/报告用的有序 (标签, 值) 列表。"""
    f = md.get("fields") or {}
    rows: list[tuple[str, str]] = []
    if md.get("vendor"):
        rows.append(("编码器串 (vendor)", md["vendor"]))
    for k in FIELD_MAP:
        if k in f:
            rows.append((FIELD_CN.get(k, k), f[k]))
    if md.get("pictures"):
        rows.append(("内嵌封面", "%d 张" % md["pictures"]))
    if md.get("tags_total"):
        rows.append(("标签总数", str(md["tags_total"])))
    if md.get("error"):
        rows.append(("说明", md["error"]))
    return rows


def brief(md: dict) -> str:
    """一行摘要：厂牌 · 年份 · 专辑。"""
    f = md.get("fields") or {}
    year = (f.get("date") or "")[:4] or None
    bits = [f.get("label"), year, f.get("album")]
    return " · ".join(b for b in bits if b) or "—"


@register
class MetadataAnalyzer(Analyzer):
    key = "metadata"
    label = "标签与编码器串"
    order = 5

    def run(self, ctx) -> dict:
        md = read_tags(ctx.path)
        ctx.metric("vendor", "编码器串", group="元数据", primary=True)
        return {"meta": md, "vendor": md.get("vendor")}
