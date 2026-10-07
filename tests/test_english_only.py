"""Only English clients contribute: another language's export is skipped, and
the lines it already won give way to an English reading or to Classic's."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import ingest
from tools.ingest import foreign_export, foreign_line

QUEST = "90001-accept"  # Forever's own, so no Classic text stands in


def test_lines_in_other_languages_read_as_foreign() -> None:
    assert foreign_line(
        "¡Esos malditos troggs han tomado Gnomeregan! La situación es grave"
    )
    assert foreign_line(
        "Gute Arbeit, $N, ich wusste, dass Ihr nicht völlig nutzlos seid."
    )
    assert foreign_line("Спасибо, $n. Благодаря тебе архивы наших знаний растут.")
    assert not foreign_line(
        "Great work $N, I knew you weren't useless. Here - have one of these."
    )
    # Forever's own tongue and a bark, met on an English client
    assert not foreign_line("E wirsh ador eynes re an tiras an lo vil va novaedi")
    assert not foreign_line("Woof?")
    assert not foreign_line("Elune's grace... I shall not die... alone...")


def export(lines: list[str], **fields: str) -> dict:
    return {
        **fields,
        "quests": {f"{90000 + i}-accept": {"text": t} for i, t in enumerate(lines)},
    }


def test_an_export_is_judged_as_a_whole() -> None:
    english = "Bring me the scales and you will have your reward."
    russian = "Принес что-то новенькое в нашу коллекцию?"
    # A Russian client showed an untranslated page: still a Russian export
    assert foreign_export(export([russian, russian, russian, english]))
    # One odd line among English ones is not
    assert not foreign_export(export([russian] + [english] * 9))
    # The locale, from addon 0.1.10 on, is believed over the text
    assert foreign_export(export([english], locale="deDE"))
    assert not foreign_export(export([russian], locale="enGB"))
    assert not foreign_export({})


def write(path: Path, text: str, time: int, name: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "source": "community",
                "origin": path.stem,
                "addon": "0.1.8",
                "quests": {
                    QUEST: {
                        "event": "accept",
                        "questID": 90001,
                        "text": text,
                        "npc": "250001",
                        "name": name,
                        "time": time,
                        "class": "Mage",
                        "race": "Human",
                        "sex": "m",
                    }
                },
                "npcs": {"250001": {"name": name, "sex": 2}},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_foreign_export_ingested_before_the_check_gives_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "CAPTURE_JSON", tmp_path / "capture.json")
    monkeypatch.setattr(ingest, "SOURCES_JSON", tmp_path / "capture.sources.json")
    monkeypatch.setattr(ingest, "SourceTexts", lambda: None)
    english = write(
        tmp_path / "issue-1.json",
        "Bring me the scales of the drake and you will have your reward.",
        1000,
        "Keldric Signal",
    )
    spanish = write(
        tmp_path / "issue-2.json",
        "¡Tráeme las escamas del draco y tendrás tu recompensa, que es muy buena!",
        2000,
        "Keldric Señalo",
    )
    files = [str(english), str(spanish)]

    # As the ingest before the check did: the later Spanish reading won
    with monkeypatch.context() as before:
        before.setattr(ingest, "foreign_export", lambda db: False)
        ingest.main(files)
    capture = json.loads((tmp_path / "capture.json").read_text(encoding="utf-8"))
    assert capture["quests"][QUEST]["origin"] == "issue-2"
    # whose stamps did not say whether a file was foreign
    stamps_path = tmp_path / "capture.sources.json"
    stamps = json.loads(stamps_path.read_text(encoding="utf-8"))
    for stamp in stamps.values():
        del stamp["foreign"]
    stamps_path.write_text(json.dumps(stamps), encoding="utf-8")

    ingest.main(files)
    capture = json.loads((tmp_path / "capture.json").read_text(encoding="utf-8"))
    line = capture["quests"][QUEST]
    assert line["origin"] == "issue-1"
    assert line["text"].startswith("Bring me the scales")
    assert "name" not in capture["npcs"]["250001"]  # the Spanish name went
    stamps = json.loads((tmp_path / "capture.sources.json").read_text(encoding="utf-8"))
    assert stamps[str(spanish)]["foreign"] is True
    assert stamps[str(english)]["foreign"] is False

    # Judged once: a third run neither purges nor merges again
    ingest.main(files)
    again = json.loads((tmp_path / "capture.json").read_text(encoding="utf-8"))
    assert again == capture
