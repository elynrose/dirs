from types import SimpleNamespace

from director_api.agents.json_from_model import (
    parse_model_json_loose,
    raw_assistant_text_for_json,
    top_level_brace_spans,
)


def test_top_level_brace_spans_nested():
    s = 'prefix {"a": 1} middle {"b": 2} tail'
    spans = top_level_brace_spans(s)
    assert spans == ['{"a": 1}', '{"b": 2}']


def test_parse_model_json_loose_after_prose():
    raw = 'Some reasoning here.\nThen: {"schema_id": "director-pack/v1", "x": 1}\n'
    data, err = parse_model_json_loose(raw)
    assert err is None
    assert data == {"schema_id": "director-pack/v1", "x": 1}


def test_parse_model_json_loose_prefers_full_parse():
    raw = '{"k": "v"}'
    data, err = parse_model_json_loose(raw)
    assert err is None
    assert data == {"k": "v"}


def test_raw_assistant_text_prefers_content():
    m = SimpleNamespace(content='{"a": 1}', reasoning_content="noise")
    assert raw_assistant_text_for_json(m) == '{"a": 1}'


def test_raw_assistant_text_falls_back_to_reasoning():
    m = SimpleNamespace(content="", reasoning_content='{"b": 2}')
    assert raw_assistant_text_for_json(m) == '{"b": 2}'
