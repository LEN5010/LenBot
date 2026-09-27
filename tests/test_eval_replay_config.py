"""Structured replay files and real OneBot ingress validation only."""

import copy
import json
from pathlib import Path

import pytest

from len_bot.eval.cases import (
    AwaitTurnStep, MessageStep, ObserveStep, RestartStep, load_cases,
)


FIXTURE = Path(__file__).parent / "fixtures/eval/replay_cases.json"
SCENE = "group:80001"
BOT = "90001"


def _source() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, source: dict) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(source, ensure_ascii=False, allow_nan=True), encoding="utf-8")
    return path


def test_synthetic_structured_cases_keep_original_onebot_envelopes_and_boundaries():
    source = _source()
    loaded = load_cases(FIXTURE, set_name="coherence", scene=SCENE, bot_qq=BOT)

    assert loaded.format == "structured-development"
    assert loaded.source == source["source"]
    assert [case.id for case in loaded.cases] == ["correction-restart", "ambient-observe"]
    assert [type(step) for step in loaded.cases[0].steps] == [
        MessageStep, AwaitTurnStep, RestartStep, MessageStep, AwaitTurnStep,
    ]
    assert [step.count for step in loaded.cases[0].steps if isinstance(step, AwaitTurnStep)] == [1, 2]
    assert [type(step) for step in loaded.cases[1].steps] == [MessageStep, ObserveStep]
    assert loaded.cases[0].steps[0].event == source["cases"][0]["steps"][0]["event"]
    assert loaded.cases[0].expect == source["cases"][0]["expect"]


@pytest.mark.parametrize("change,field", [
    (lambda data: data.pop("format"), "format"),
    (lambda data: data.update(format="natural-language"), "format"),
    (lambda data: data.update(source=" \n"), "source"),
    (lambda data: data.update(cases=[]), "cases"),
    (lambda data: data["cases"][0].update(id="../escape"), "cases.0.id"),
    (lambda data: data["cases"][0].update(expect=[]), "cases.0.expect"),
    (lambda data: data["cases"][0].update(expect=["  "]), "cases.0.expect"),
    (lambda data: data["cases"][0].update(steps=[]), "cases.0.steps"),
    (lambda data: data["cases"][0]["steps"][0].update(type="unknown"), "steps"),
    (lambda data: data["cases"][0]["steps"][0].update(unexpected=True), "unexpected"),
    (lambda data: data["cases"][0]["steps"][1].update(count=0), "count"),
    (lambda data: data["cases"][0]["steps"][1].update(count=True), "count"),
    (lambda data: data["cases"][1]["steps"][1].update(seconds=0), "seconds"),
    (lambda data: data["cases"][1]["steps"][1].update(seconds=-1), "seconds"),
    (lambda data: data["cases"][1]["steps"][1].update(seconds=float("inf")), "Infinity"),
    (lambda data: data["cases"][1]["steps"][1].update(seconds="0.2"), "seconds"),
])
def test_replay_case_structure_rejects_invalid_values_with_path_and_fragment(tmp_path, change, field):
    source = _source()
    change(source)
    path = _write(tmp_path, source)

    with pytest.raises(ValueError) as failure:
        load_cases(path, set_name="coherence", scene=SCENE, bot_qq=BOT)
    assert str(path) in str(failure.value)
    assert field in str(failure.value)
    assert "raw=" in str(failure.value)


def test_replay_case_ids_are_unique_and_set_matches_selected_name(tmp_path):
    source = _source()
    source["cases"][1]["id"] = source["cases"][0]["id"]
    path = _write(tmp_path, source)
    with pytest.raises(ValueError, match="case-insensitive paths"):
        load_cases(path, set_name="coherence", scene=SCENE, bot_qq=BOT)

    source["cases"][1]["id"] = "second-case"
    source["cases"][1]["set"] = "another-set"
    path = _write(tmp_path, source)
    with pytest.raises(ValueError) as failure:
        load_cases(path, set_name="coherence", scene=SCENE, bot_qq=BOT)
    assert "second-case" in str(failure.value)
    assert "another-set" in str(failure.value)
    assert str(path) in str(failure.value)


def test_case_ids_differing_only_by_case_are_rejected_for_case_insensitive_output(tmp_path):
    source = _source()
    source["cases"][0]["id"] = "A"
    source["cases"][1]["id"] = "a"
    path = _write(tmp_path, source)

    with pytest.raises(ValueError) as failure:
        load_cases(path, set_name="coherence", scene=SCENE, bot_qq=BOT)
    assert str(path) in str(failure.value)
    assert "cases[1]" in str(failure.value)
    assert "'A' and 'a'" in str(failure.value)


@pytest.mark.parametrize("change,fragment", [
    (lambda event: event.update(message="not an array"), "message must be a OneBot segment array"),
    (lambda event: event.pop("sender"), "sender"),
    (lambda event: event["message"].append({"type": "text", "data": {"text": 3}}), "message[2]"),
    (lambda event: event.update(group_id="89999"), "scene/self_id"),
    (lambda event: event.update(self_id="90002"), "scene/self_id"),
])
def test_replay_message_uses_actual_onebot_parser_and_configured_scene(tmp_path, change, fragment):
    source = _source()
    event = source["cases"][0]["steps"][0]["event"]
    change(event)
    original = copy.deepcopy(event)
    path = _write(tmp_path, source)

    with pytest.raises(ValueError) as failure:
        load_cases(path, set_name="coherence", scene=SCENE, bot_qq=BOT)
    assert str(path) in str(failure.value)
    assert "correction-restart" in str(failure.value)
    assert fragment in str(failure.value)
    assert event == original


def test_natural_language_draft_is_not_inferred_as_onebot_steps(tmp_path):
    path = _write(tmp_path, {"cases": [{"question": "自然语言草稿", "expected": "不能猜原事件"}]})
    with pytest.raises(ValueError) as failure:
        load_cases(path, set_name="coherence", scene=SCENE, bot_qq=BOT)
    assert str(path) in str(failure.value)
    assert "format" in str(failure.value)


def test_bad_json_and_nonstandard_constant_report_raw_fragment(tmp_path):
    path = tmp_path / "cases.json"
    path.write_bytes(b'{"format":"structured-development", "cases": [}\n')
    with pytest.raises(ValueError) as failure:
        load_cases(path, set_name="coherence", scene=SCENE, bot_qq=BOT)
    assert str(path) in str(failure.value)
    assert '"cases": [}' in str(failure.value)

    path.write_text('{"format":"structured-development", "source":"synthetic", "cases":NaN}', encoding="utf-8")
    with pytest.raises(ValueError) as failure:
        load_cases(path, set_name="coherence", scene=SCENE, bot_qq=BOT)
    assert str(path) in str(failure.value)
    assert "NaN" in str(failure.value)
