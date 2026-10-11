"""元数据层：标签归一化与编码器串提取。"""
from __future__ import annotations

from audiocheck.core.analyzers.metadata import brief, read_tags, tag_rows


def test_reads_common_tags(tagged):
    md = read_tags(tagged)
    f = md["fields"]
    assert f.get("title") == "测试标题"
    assert f.get("artist") == "测试艺术家"
    assert f.get("album") == "测试专辑"
    assert f.get("label") == "测试唱片公司"
    assert f.get("isrc") == "TEST12345678"


def test_vendor_present_for_flac(tagged):
    md = read_tags(tagged)
    assert md["vendor"], "FLAC 的 vendor 串是判断来路的关键，不应为空"
    assert "libFLAC" in md["vendor"] or "Lavf" in md["vendor"]


def test_tag_rows_labels_are_chinese(tagged):
    rows = dict(tag_rows(read_tags(tagged)))
    assert "出版商 / 厂牌" in rows
    assert rows["ISRC"] == "TEST12345678"


def test_brief_is_compact(tagged):
    b = brief(read_tags(tagged))
    assert "测试唱片公司" in b
    assert len(b) < 80


def test_missing_file_is_graceful(tmp_path):
    md = read_tags(str(tmp_path / "nope.flac"))
    assert "error" in md
    assert md["fields"] == {}


def test_brief_on_empty_metadata():
    assert brief({}) == "—"
