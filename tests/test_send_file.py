"""send_file 纯函数测试：白名单、文件存在性、频道与失败降级。"""

import asyncio

from bot.package.tools.builtin.send_file import send_file


class _FakeSender:
    def __init__(self, result=None, error=None):
        self.result = result if result is not None else {"status": "ok"}
        self.error = error
        self.calls = []

    async def send_file(self, channel_id, path, name):
        self.calls.append((channel_id, path, name))
        if self.error:
            raise self.error
        return self.result


def test_send_file_success(tmp_path):
    sender = _FakeSender()
    path = tmp_path / "chapter.zip"
    path.write_bytes(b"zip")

    result = asyncio.run(send_file(
        str(path), "chapter.zip", "g1",
        file_sender=sender, roots=[tmp_path.resolve()],
    ))

    assert "文件已发送" in result
    assert sender.calls == [("g1", str(path.resolve()), "chapter.zip")]


def test_send_file_missing_path_not_sent(tmp_path):
    sender = _FakeSender()
    result = asyncio.run(send_file(
        str(tmp_path / "missing.zip"), "", "g1",
        file_sender=sender, roots=[tmp_path.resolve()],
    ))
    assert "文件不存在" in result
    assert sender.calls == []


def test_send_file_outside_roots_not_sent(tmp_path):
    sender = _FakeSender()
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    result = asyncio.run(send_file(
        str(outside), "", "g1",
        file_sender=sender, roots=[tmp_path.resolve()],
    ))
    assert "不在允许发送的根目录内" in result
    assert sender.calls == []


def test_send_file_requires_channel_id(tmp_path):
    sender = _FakeSender()
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")

    result = asyncio.run(send_file(
        str(path), "", "",
        file_sender=sender, roots=[tmp_path.resolve()],
    ))
    assert "缺少当前频道信息" in result
    assert sender.calls == []


def test_send_file_rejects_path_like_name(tmp_path):
    sender = _FakeSender()
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")

    result = asyncio.run(send_file(
        str(path), "dir/a.txt", "g1",
        file_sender=sender, roots=[tmp_path.resolve()],
    ))
    assert "不能包含" in result
    assert sender.calls == []


def test_send_file_reports_upstream_error(tmp_path):
    sender = _FakeSender(result={"status": "failed", "message": "too large"})
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")

    result = asyncio.run(send_file(
        str(path), "", "g1",
        file_sender=sender, roots=[tmp_path.resolve()],
    ))
    assert "文件发送失败" in result
    assert "too large" in result


def test_send_file_degrades_on_exception(tmp_path):
    sender = _FakeSender(error=RuntimeError("boom"))
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")

    result = asyncio.run(send_file(
        str(path), "", "g1",
        file_sender=sender, roots=[tmp_path.resolve()],
    ))
    assert result == "文件发送失败。"
