"""bot/core/utils/content_parser 单元测试：Satori content 分类 + 附件 + 清洗文本。"""

from bot.core.utils import (
    Attachment,
    MessageKind,
    classify_content,
    clean_text,
    parse_attachments,
    parse_content,
    parse_mentions,
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


def test_to_llm_text_renders_at_keeps_text():
    assert to_llm_text(f"{AT} 你好") == "@Bot(bot1) 你好"


def test_to_llm_text_media_only():
    assert to_llm_text(IMG) == "[图片]"


def test_parse_content_combines_fields():
    parsed = parse_content(f"{AT} 看看这张 {IMG}")
    assert parsed.kind == MessageKind.IMAGE
    assert len(parsed.attachments) == 1
    assert isinstance(parsed.attachments[0], Attachment)
    assert parsed.clean_text == "看看这张"
    assert parsed.llm_text == "@Bot(bot1) 看看这张 [图片]"
    assert parsed.mentions == {"Bot": "bot1"}
    assert parsed.has_text is True
    assert parsed.has_media is True


def test_parse_content_media_only_has_no_text():
    parsed = parse_content(IMG)
    assert parsed.kind == MessageKind.IMAGE
    assert parsed.has_text is False
    assert parsed.has_media is True
    assert parsed.clean_text == ""


def test_to_llm_text_link_renders_content_href():
    assert to_llm_text('<a href="https://x.com/a?b=1&amp;c=2">点击</a> 你好') == "点击 (https://x.com/a?b=1&c=2) 你好"


def test_to_llm_text_link_without_href_keeps_inner():
    assert to_llm_text('<a>无链接</a>') == "无链接"


def test_to_llm_text_strips_paired_markup_keeps_text():
    assert to_llm_text('<b>加粗</b>和<i>斜体</i>') == "加粗和斜体"
    assert to_llm_text('<code>code</code>') == "code"


def test_to_llm_text_quote_keeps_quoted_text():
    assert to_llm_text('<quote><at id="u1"/>原消息</quote> 回复') == "@u1原消息 回复"


def test_to_llm_text_strips_emoji_sharp_br():
    assert to_llm_text('<emoji name="smile"/> 哈哈') == "哈哈"
    assert to_llm_text('<sharp id="c1"/> 频道') == "频道"
    assert to_llm_text('第一行<br/>第二行') == "第一行第二行"


def test_to_llm_text_forward_message_keeps_inner():
    assert to_llm_text('<message><author id="u1" name="张三"/>转发内容</message>') == "转发内容"


def test_to_llm_text_strips_comment():
    assert to_llm_text('前<!-- 注释 -->后') == "前后"


def test_clean_text_strips_paired_link_no_closing_leak():
    assert clean_text('<a href="https://x.com">点击</a> 你好') == "点击 你好"


def test_clean_text_strips_quote_and_at():
    assert clean_text('<quote><at id="u1"/>原消息</quote> 回复') == "原消息 回复"


def test_clean_text_strips_comments():
    assert clean_text('前<!-- 注释 -->后') == "前后"


def test_parse_attachments_single_quoted_src():
    assert parse_attachments("<img src='a.png'/>")[0].src == "a.png"


def test_parse_attachments_title_fallback_to_name():
    assert parse_attachments('<file title="报告.pdf" src="b.pdf"/>')[0].name == "报告.pdf"


def test_to_llm_text_link_empty_inner_renders_bare_url():
    assert to_llm_text('<a href="https://x.com"> </a>') == "https://x.com"


def test_to_llm_text_link_adjacent_links():
    assert to_llm_text('<a href="1">A</a><a href="2">B</a>') == "A (1)B (2)"


def test_to_llm_text_link_multiline_inner():
    assert to_llm_text('<a href="x">多行\n文本</a>') == "多行\n文本 (x)"


def test_parse_mentions_collects_names():
    content = '<at id="10001" name="小助手"/><at id="10002" name="张三"/> 大家'
    assert parse_mentions(content) == {"小助手": "10001", "张三": "10002"}


def test_parse_mentions_top_level_only_quote_excluded():
    content = '<quote><at id="10001" name="小助手"/>原消息</quote><at id="10002" name="张三"/>怎么看'
    assert parse_mentions(content) == {"张三": "10002"}


def test_parse_mentions_forward_message_excluded():
    content = '<message><author id="u1" name="张三"/><at id="10001" name="小助手"/>转发内容</message>'
    assert parse_mentions(content) == {}


def test_parse_mentions_nested_message_keeps_top_level():
    content = ('<message forward><message><at id="10001" name="小助手"/>内层</message></message>'
               '<at id="10002" name="张三"/>外层')
    assert parse_mentions(content) == {"张三": "10002"}


def test_parse_mentions_skips_type_all():
    assert parse_mentions('<at type="all"/> 早上好') == {}
    assert parse_mentions('<at type="here"/>') == {}


def test_parse_mentions_id_only_fallback():
    assert parse_mentions('<at id="10001"/>') == {"10001": "10001"}


def test_parse_mentions_empty_no_at():
    assert parse_mentions("纯文本") == {}


def test_to_llm_text_at_without_name_renders_id():
    assert to_llm_text('<at id="u1"/> 你好') == "@u1 你好"


def test_to_llm_text_at_type_all_renders_all_members():
    assert to_llm_text('<at type="all"/> 早上好') == "所有成员 早上好"


def test_to_llm_text_at_type_here_renders_online_members():
    assert to_llm_text('<at type="here"/>') == "在线成员"
