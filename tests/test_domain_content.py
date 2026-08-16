"""锁定领域类型经 domain 包 lazy-loading 导出，且为 conversation.content 的真实类。"""

import conversation.content as content_module
from conversation import Attachment, MessageKind, ParsedContent


def test_message_kind_values():
    assert MessageKind.TEXT.value == "text"
    assert MessageKind.IMAGE.value == "image"
    assert MessageKind.FILE.value == "file"
    assert MessageKind.AUDIO.value == "audio"
    assert MessageKind.VIDEO.value == "video"
    assert MessageKind("image") is MessageKind.IMAGE  # str, Enum 反查


def test_types_live_in_object_bot_content():
    assert content_module.MessageKind is MessageKind
    assert MessageKind.__module__ == "conversation.content"
    assert ParsedContent.__module__ == "conversation.content"
    assert Attachment.__module__ == "conversation.content"


def test_parsed_content_has_media():
    img = ParsedContent(kind=MessageKind.IMAGE, attachments=[Attachment(type="img", src="x")])
    assert img.has_media is True
    txt = ParsedContent(kind=MessageKind.TEXT)
    assert txt.has_media is False
