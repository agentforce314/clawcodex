"""_extract_prompt_content preserves image blocks (multimodal) but flattens text."""

from src.server.agent_server import _extract_prompt_content


def test_string_content():
    assert _extract_prompt_content({"message": {"role": "user", "content": "hi"}}) == "hi"


def test_text_only_list_flattens():
    msg = {"message": {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}
    assert _extract_prompt_content(msg) == "ab"


def test_image_block_preserved():
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}
    msg = {"message": {"role": "user", "content": [{"type": "text", "text": "look"}, img]}}
    out = _extract_prompt_content(msg)
    assert isinstance(out, list)
    assert any(b.get("type") == "image" for b in out)
    assert any(b.get("type") == "text" for b in out)
