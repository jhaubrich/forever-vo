"""Books, letters and plaques: a page is keyed by its text, read by the narrator
in every narrator voice, and ships in its own Books pack, Classic's pages and Forever's alike."""

from __future__ import annotations

import json
from pathlib import Path

from tools.classicdb import collect_books
from tools.config import load_config
from tools.exportfile import to_capture
from tools.generate import (
    Item,
    VoiceCatalog,
    index_key,
    link_pages,
    rebuild_tables,
    sound_folder,
)
from tools.ingest import Repairs, SourceTexts, backfill, book_key, ingest_file
from tools.release_pack import Classic, pack_of, pack_specs
from tools.textclean import book_display, book_text
from tools.textkey import text_key

CONFIG = load_config()
READERS = CONFIG.readers
LETTER = (
    "Master Carevin,\n\nThe bearer of this note has shown $g himself : herself; to be "
    "upstanding in the Light, capable of battling the undead."
)


def page(text: str = "To the Honorable Headmaster Crillian.", **entry) -> Item:
    return Item(
        "books",
        text_key(text),
        {"event": "page", "text": text, "title": "A Letter", "page": 1, **entry},
        None,
        VoiceCatalog(CONFIG),
    )


def test_chains_are_walked_once_per_page_and_named_by_the_first_source() -> None:
    pages = {
        16: ("To the Honorable Headmaster Crillian,", 17),
        17: ("Yours, Morgan.", 16),  # a loop must not hang the walk
        10: ("Missing Text", 0),
        20: ("   ", 21),
        21: ("A plaque.", 0),
    }
    books = collect_books(
        pages, [(16, "Letter to Crillian"), (17, "Torn Page"), (10, "x"), (20, "Sign")]
    )
    assert {b["text"] for b in books.values()} == {
        "To the Honorable Headmaster Crillian,",
        "Yours, Morgan.",
        "A plaque.",
    }
    second = books[text_key("Yours, Morgan.")]
    assert (second["title"], second["page"], second["event"]) == (
        "Letter to Crillian",
        2,
        "page",
    )
    assert books[text_key("A plaque.")]["page"] == 2  # the blank page still counts
    first = books[text_key("To the Honorable Headmaster Crillian,")]
    assert first["next"] == text_key("Yours, Morgan.")
    assert "next" not in second  # back to page 16, already read: the loop ends


def test_an_exported_page_is_a_book_not_gossip() -> None:
    capture = to_capture(
        {
            "v": 1,
            "addon": "0.1.9",
            "lines": [
                {"k": "book", "x": "A plaque.", "t": "Sign", "p": 1, "g": "m"},
                {"k": "gossip", "x": "Hello.", "n": "288", "s": "Morgan"},
            ],
        },
        "issue-1",
    )
    assert list(capture["books"]) == [text_key("A plaque.")]
    entry = capture["books"][text_key("A plaque.")]
    assert (entry["title"], entry["page"], entry["event"]) == ("Sign", 1, "page")
    assert entry["npc"] is None
    assert list(capture["gossip"]) == [f"288|{text_key('Hello.')}"]


def test_ingest_keys_a_page_by_its_text_and_puts_a_gender_branch_back(
    tmp_path: Path,
) -> None:
    bulk = tmp_path / "bulk"
    bulk.mkdir()
    (bulk / "classic.json").write_text(
        json.dumps(
            {"books": {text_key(LETTER): {"text": LETTER, "title": "Carevin's Note"}}}
        ),
        encoding="utf-8",
    )
    sources = SourceTexts(bulk)
    read = LETTER.replace("$g himself : herself;", "himself")
    export = tmp_path / "issue-1.json"
    export.write_text(
        json.dumps(
            {
                "addon": "0.1.9",
                "origin": "issue-1",
                "books": {
                    text_key(read): {
                        "event": "page",
                        "text": read,
                        "title": "Carevin's Note",
                        "page": 1,
                        "sex": "m",
                        "class": "Paladin",
                        "race": "Human",
                        "source": "community",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    capture = {"quests": {}, "gossip": {}, "books": {}, "npcs": {}, "sources": {}}
    stats = Repairs()
    counts = ingest_file(capture, export, sources, stats, READERS)
    assert counts == (0, 0, 1, 0)
    (key, entry), *_ = capture["books"].items()
    assert "$g himself : herself;" in entry["text"]
    assert key == book_key(entry, READERS) == text_key(LETTER)  # Classic's key
    assert "needs" not in entry  # only quests are asked for again

    # Idempotent, and a page keyed under an old hash moves to its own
    capture["books"] = {"stale": entry}
    assert backfill(capture, sources, stats, READERS)[2] == 1
    assert list(capture["books"]) == [text_key(LETTER)]


def test_a_page_is_the_narrator_under_books() -> None:
    line = page()
    assert line.voice == CONFIG.voices.narrator and line.is_narrator
    assert line.speaker_key is None
    assert line.subfolder == "Books"
    assert line.base_name == f"{text_key(line.raw_text)}-page"
    assert sound_folder(line.base_name) == "Books"
    assert sound_folder("842-accept") == "Quests"
    assert sound_folder("288-1a2b3c4d") == "Gossip"
    assert [v.parts for v in line.variants()] == [[]]  # no parts: it is all narrator
    gendered = page(LETTER)
    assert [v.base for v in gendered.variants()] == [
        f"m-{gendered.base_name}",
        f"f-{gendered.base_name}",
    ]


def test_an_html_page_is_read_as_prose() -> None:
    # Classic's SimpleHTML pages: one break wraps a line, a heading or a blank
    # line ends a sentence, pictures and tags are not read out
    pylon = (
        '<HTML>\n<BODY>\n<H1 align="center">\nChapter 2: THE EASTERN PYLON\n</H1>\n'
        '<BR/>\n<BR/>\n<IMG src="Interface\\Pictures\\11482_crystals_mini_east"/>\n'
        '<P align="right">The Eastern<BR/>\nPylon accepts<BR/>\ncrystals.</P>\n<BR/>\n<BR/>\n'
        "<P>Throm'ka &amp; well met</P></BODY></HTML>"
    )
    assert book_text(pylon) == (
        "Chapter 2: THE EASTERN PYLON. The Eastern Pylon accepts crystals. "
        "Throm'ka & well met."
    )
    picture = (
        '<HTML><BODY><IMG src="Interface\\Pictures\\11733_bldbank_256"/></BODY></HTML>'
    )
    assert book_text(picture) == ""
    assert book_text("Plain <text> stays.") == "Plain <text> stays."
    assert book_display(pylon) == book_text(pylon)
    assert book_display(picture) == ""
    assert book_display("Hello $B$B $n.") == "Hello adventurer."
    assert "$g" in book_display(LETTER)
    variant = page(picture).variants()[0]
    assert variant.text == ""  # is_speakable refuses it, so no file


def test_each_voiced_page_links_to_the_next() -> None:
    classic = "The five dragonflights watch over Azeroth from their temples."
    reword = "The five dragonflights watch over Azeroth from their shrines."
    other = "Bring the crate to the barn behind the inn before nightfall."

    def record(title: str, number: int, text: str) -> dict:
        return {"d": 1.0, "t": text, "b": title, "p": number}

    books = {
        "a1": record("Charge", 1, "Page one of the charge."),
        "a2": record("Charge", 2, reword),
        "a2-old": record("Charge", 2, classic),  # Classic's page 2, reworded
        "a3": record("Charge", 3, "Page three."),
        "l1": record("Letter", 1, "Master Carevin,"),
        "l2": record("Letter", 2, "Yours, Morgan."),
        "l2-other": record("Letter", 2, other),  # another letter of the same name
        "u1": record("Plaque", 1, "Only this plaque."),
        "u2": record("Plaque", 2, "Its second page, worded nothing like a letter."),
        "n1": {"d": 1.0, "t": "no title", "b": None, "p": 1},
    }
    sources = {
        "a1": {
            "source": "classic",
            "next": "a2-old",
            "text": "Page one of the charge.",
        },
        "a2": {"player": "Hepcat", "text": reword},
        "a2-old": {"source": "classic", "next": "a3", "text": classic},
        "l1": {"source": "classic", "next": "l2", "text": "Master Carevin,"},
        "l2-other": {"player": "Jesse", "text": other},
        "u1": {"source": "classic", "next": "missing"},
        "u2": {"player": "Jesse", "text": books["u2"]["t"]},
        "a3": {"source": "classic", "next": "gone"},  # a page with no audio
    }
    link_pages(books, sources)
    assert books["a1"]["x"] == "a2"  # the reword of this page's own next
    assert books["a2-old"]["t"] == classic  # Classic's page is still stored
    assert books["a2"]["x"] == "a3"  # the one page 3
    assert books["l1"]["x"] == "l2"  # not the other letter, though that one was read
    assert books["l2"]["t"] == "Yours, Morgan."
    assert books["l2-other"]["t"] == other
    assert books["u1"]["x"] == "u2"  # the only page of that number
    assert "x" not in books["a3"] and "x" not in books["n1"]
    assert "x" not in books["l2"]


def test_the_tables_carry_each_page_with_its_alternate_narrators(
    tmp_path: Path,
) -> None:
    alternate = CONFIG.voices.narrator_alternates[0]
    line = page()
    base = line.base_name
    sounds = tmp_path / "Sounds"
    (sounds / "Books" / "Narrator" / alternate).mkdir(parents=True)
    (sounds / "Books" / f"{base}.mp3").write_bytes(b"")
    (sounds / "Books" / "Narrator" / alternate / f"{base}.mp3").write_bytes(b"")
    index = {
        base: {"d": 4.5, "v": CONFIG.voices.narrator},
        index_key(base, alternate): {"d": 4.7, "v": alternate},
    }
    data = tmp_path / "Data"
    stats = rebuild_tables(
        [line], dict(index), CONFIG, data, sounds_dir=sounds, write_index=False
    )
    assert stats["books"] == 1
    assert stats["files"] == {base}
    assert stats["narratorFiles"] == {f"Books/Narrator/{alternate}/{base}"}
    books = (data / "Books.lua").read_text(encoding="utf-8")
    assert "pack.books = {" in books
    assert f'["{line.hash}"] = {{ d=4.500,' in books
    assert 'b="A Letter"' in books
    assert 's="To the Honorable Headmaster Crillian."' in books
    assert "n={ [1]=4.700 }" in books
    narrator = (data / "Narrator.lua").read_text(encoding="utf-8")
    assert f'pack.narratorVoices = {{ "{alternate}" }}' in narrator


def test_books_ship_in_the_books_pack_alone() -> None:
    classic_set = Classic({842: 12}, frozenset())
    quest = Item(
        "quests",
        "842-accept",
        {"questID": 842, "event": "accept", "text": "Go north.", "level": 12},
        {"name": "Kargal Battlescar", "sex": 2},
        VoiceCatalog(CONFIG),
    )
    classic, read = page(), page(player="Jesse", source="capture")
    for line in (classic, read):
        assert pack_of(line, classic_set, 40) == "books"
    assert pack_of(quest, classic_set, 40) == "classic_quests"
    assert pack_specs(CONFIG.release)["books"].folder == "ForeverVO_Data_Books"
