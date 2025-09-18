import importlib
import pytest


def _load_chunker(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import app.services.ssml_chunker as chunker

    importlib.reload(chunker)
    return chunker


def _fake_fragment_builder(text: str, break_after: bool = False) -> str:
    processed = text.replace("AI", '<sub alias="A I">AI</sub>').replace(
        "SaaS", '<sub alias="sass">SaaS</sub>'
    )
    if break_after:
        processed = f'{processed} <break time="500ms"/>'
    return f"<speak>{processed}</speak>"


def test_ssml_fragments_stay_within_limit(monkeypatch):
    chunker = _load_chunker(monkeypatch)
    text = "AI SaaS RSS data." * 400

    fragments = chunker.text_to_ssml_fragments(text, _fake_fragment_builder)

    assert fragments
    assert all(
        len(fragment.encode("utf-8")) <= chunker.GOOGLE_TTS_MAX_INPUT_BYTES
        for fragment in fragments
    )


def test_ssml_break_only_on_terminal_fragment(monkeypatch):
    chunker = _load_chunker(monkeypatch)
    text = "This is sentence one. " * 40

    fragments = chunker.text_to_ssml_fragments(
        text, _fake_fragment_builder, break_after=True
    )

    if len(fragments) == 1:
        assert 'break time="500ms"' in fragments[0]
    else:
        assert all('break time="500ms"' not in frag for frag in fragments[:-1])
        assert 'break time="500ms"' in fragments[-1]


@pytest.mark.parametrize("chunk_bytes", ["4800", "2000"])
def test_text_split_into_multiple_fragments(monkeypatch, chunk_bytes):
    chunker = _load_chunker(
        monkeypatch,
        TTS_MAX_CHUNK_BYTES=chunk_bytes,
        TTS_SAFETY_MARGIN_BYTES="400",
    )
    text = " ".join(f"Sentence {i}." for i in range(600))

    fragments = chunker.text_to_ssml_fragments(text, _fake_fragment_builder)

    assert len(fragments) > 1
    assert all(
        len(fragment.encode("utf-8")) <= chunker.GOOGLE_TTS_MAX_INPUT_BYTES
        for fragment in fragments
    )
