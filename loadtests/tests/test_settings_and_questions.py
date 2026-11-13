"""The mix, think times and question sets a run is configured with."""

import json
from pathlib import Path

import pytest

from needwise_load.questions import QuestionBank, local_part, read_dataset
from needwise_load.settings import (
    DATASETS_DIR,
    DEFAULT_MIX,
    DEMO_USERS,
    Settings,
    SettingsError,
    parse_mix,
    parse_range,
)


def test_a_mix_overrides_only_the_classes_it_names() -> None:
    mix = parse_mix("reader=2, chat=0")

    assert mix == {**DEFAULT_MIX, "reader": 2, "chat": 0}


@pytest.mark.parametrize("text", ["readers=2", "reader", "reader=x", "reader=-1"])
def test_a_wrong_mix_is_refused(text: str) -> None:
    with pytest.raises(SettingsError):
        parse_mix(text)


def test_without_a_model_or_a_collection_those_classes_are_off() -> None:
    plain = Settings().weights
    no_model = Settings(no_model=True, upload_collection="loadtest-uploads").weights

    assert (plain["chat"], plain["agents"], plain["upload"]) == (3, 1, 0)
    assert (no_model["chat"], no_model["agents"], no_model["upload"]) == (0, 0, 1)
    assert no_model["reader"] == DEFAULT_MIX["reader"]


def test_think_times() -> None:
    assert parse_range("3,10") == (3.0, 10.0)
    assert parse_range("5") == (5.0, 5.0)
    with pytest.raises(SettingsError):
        parse_range("10,3")


def test_each_login_asks_its_own_questions_when_it_has_some() -> None:
    bank = QuestionBank.load(DATASETS_DIR)

    bob = bank.for_login("chat", "bob@acme.test")
    erin = bank.for_login("chat", "erin@acme.test")  # no chat questions of her own

    assert bob
    assert {q.user for q in bob} == {"bob"}
    assert erin == bank.sets["chat"]
    assert bank.for_login("agents", "alice@acme.test")


def test_analysts_are_the_logins_with_answerable_insights_questions() -> None:
    bank = QuestionBank.load(DATASETS_DIR)

    analysts = bank.analysts(list(DEMO_USERS))

    assert set(map(local_part, analysts)) == {"carol", "dan", "priya.raman", "hannah.schmidt"}
    carol = bank.insights_for("carol@acme.test")
    assert carol
    assert all(q.sql and q.expect is None for q in carol)


def test_a_dataset_line_without_a_question_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text(json.dumps({"id": "x", "user": "bob"}) + "\n")

    with pytest.raises(ValueError, match="no question"):
        read_dataset(path)
