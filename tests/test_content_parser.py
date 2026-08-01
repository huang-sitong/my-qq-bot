"""bot/core/utils/content_parser 单元测试：Satori content 分类 + 附件 + 清洗文本。"""

from bot.core.utils import (
    Attachment,
    MessageKind,
    classify_content,
    clean_text,
    parse_attachments,
    parse_content,
    to_llm_text,
)

IMG = '<img src="https://multimedia.nt.qq.com.cn/download?appid=1407&amp;fileid=abc"/>'
AT = '<at id="bot1" name="Bot"/>'


def test_parse_attachments_text_only():
    assert parse_attachments("你好呀") == []


def test_parse_attachments_unescapes_src():
    attachments = parse_attachments(IMG)
    assert len(attachments) == 1
    assert attachments[0].type == "img"
    assert attachments[0].src == "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=abc"


def test_parse_attachments_mixed_media():
    content = f'<img src="a.png"/>文字<file name="报告.pdf" src="b.pdf"/>'
    attachments = parse_attachments(content)
    assert [a.type for a in attachments] == ["img", "file"]
    assert attachments[1].name == "报告.pdf"


def test_parse_attachments_audio_video():
    assert [a.type for a in parse_attachments('<audio src="v.msilk"/>')] == ["audio"]
    assert [a.type for a in parse_attachments('<video src="v.mp4"/>')] == ["video"]


def test_parse_attachments_records_offsets():
    content = f"开头{IMG}结尾"
    attachments = parse_attachments(content)
    assert attachments[0].start == content.index("<img")
    assert attachments[0].end == content.index("/>") + 2


def test_classify_text():
    assert classify_content("纯文本") == MessageKind.TEXT


def test_classify_by_first_media_tag():
    assert classify_content(IMG) == MessageKind.IMAGE
    assert classify_content('<file name="a.pdf"/>') == MessageKind.FILE
    assert classify_content('<audio src="v.msilk"/>') == MessageKind.AUDIO
    assert classify_content('<video src="v.mp4"/>') == MessageKind.VIDEO


def test_classify_text_plus_image_is_image():
    assert classify_content(f"今天真开心 {IMG}") == MessageKind.IMAGE


def test_classify_mixed_media_uses_first_tag():
    content = f'{IMG}<file name="a.pdf"/>'
    assert classify_content(content) == MessageKind.IMAGE


def test_clean_text_strips_all_tags_and_unescapes():
    content = f"{AT} 你好 &amp; 再见 {IMG}"
    assert clean_text(content) == "你好 & 再见"


def test_clean_text_media_only_is_empty():
    assert clean_text(IMG) == ""


def test_to_llm_text_replaces_media_with_placeholders():
    content = f"这是{IMG}然后"
    assert to_llm_text(content) == "这是[图片]然后"
    assert to_llm_text('<file name="a.pdf"/>') == "[文件]"
    assert to_llm_text('<audio src="v.msilk"/>') == "[语音]"
    assert to_llm_text('<video src="v.mp4"/>') == "[视频]"


def test_to_llm_text_strips_at_keeps_text():
    assert to_llm_text(f"{AT} 你好") == "你好"


def test_to_llm_text_media_only():
    assert to_llm_text(IMG) == "[图片]"


def test_parse_content_combines_fields():
    parsed = parse_content(f"{AT} 看看这张 {IMG}")
    assert parsed.kind == MessageKind.IMAGE
    assert len(parsed.attachments) == 1
    assert isinstance(parsed.attachments[0], Attachment)
    assert parsed.clean_text == "看看这张"
    assert parsed.llm_text == "看看这张 [图片]"
    assert parsed.has_text is True
    assert parsed.has_media is True


def test_parse_content_media_only_has_no_text():
    parsed = parse_content(IMG)
    assert parsed.kind == MessageKind.IMAGE
    assert parsed.has_text is False
    assert parsed.has_media is True
    assert parsed.clean_text == ""
