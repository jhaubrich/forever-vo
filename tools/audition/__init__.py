"""Audition: a local page for hearing one line in one voice under several
reference clips and Chatterbox settings side by side, and keeping what wins.

    uv run audition                    # serves http://127.0.0.1:8765
    uv run audition --port 9000 --open # and opens the browser

On an AMD GPU the same page uses the ROCm wheel, in its own environment so the
default CUDA one (what the nightly run generates with) stays put:

    UV_PROJECT_ENVIRONMENT=.venv-rocm ./tools/run.sh --no-group tts --group tts-rocm audition

"Write to pack" is refused on that build. The sound index would treat the file
as current, and the CUDA nightly would ship it.

One model instance, loaded on the first take and kept. Nothing here goes around
the pipeline's bookkeeping:

- "keep" writes [tts.voices.<voice>] or a [pronunciations] entry into
  forever-vo.toml through tomlkit, so the comments survive, and the file is
  validated by the same models before the old one is replaced;
- "write to pack" regenerates a pack file only under the configuration as
  saved, and stamps the fingerprint generate.py would compute, so the nightly
  run agrees the file is current instead of redoing it or missing the change.

Takes land under tools/data/audition/<session>/ (gitignored) and are served
from there. The page is index.html beside this file: one HTML file, no build.
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from tools import generate
from tools.config import (
    CONFIG_TOML,
    DATA_DIR,
    SOUND_INDEX,
    SOUNDS_DIR,
    VOICES_DIR,
    Config,
    ConfigError,
    VoiceTuning,
    load_config,
)
from tools.generate import (
    Item,
    Synth,
    Variant,
    VoiceCatalog,
    load_items,
    load_sources,
    sound_path,
)
from tools.textclean import clean

AUDITION_DIR = DATA_DIR / "audition"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


# ----------------------------------------------------------------------------
# forever-vo.toml edits (comments survive: tomlkit round-trips the document)
# ----------------------------------------------------------------------------

def _validated_write(path: Path, doc: tomlkit.TOMLDocument) -> Config:
    """Writes the document only if the models accept it; returns the fresh Config."""
    text = tomlkit.dumps(doc)
    trial = path.with_suffix(".toml.audition")
    trial.write_text(text, encoding="utf-8")
    try:
        load_config.cache_clear()
        load_config(trial)
    except ConfigError as e:
        trial.unlink(missing_ok=True)
        raise HTTPException(400, f"refused, the result would not validate: {e}") from e
    trial.replace(path)
    load_config.cache_clear()
    return load_config(path)


def write_tuning(path: Path, voice: str, exaggeration: float, cfg_weight: float, reference: str | None,
                 tempo: float = 1.0) -> Config:
    """Sets [tts.voices.<voice>]; a tuning equal to the defaults with no reference
    removes the entry instead, so the file only lists what differs."""
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    tts = doc.get("tts")
    if tts is None:
        tts = tomlkit.table()
        doc["tts"] = tts
    defaults = (float(tts.get("exaggeration", 0.45)), float(tts.get("cfg_weight", 0.5)), float(tts.get("tempo", 1.0)))
    voices = tts.get("voices")
    if voices is None:
        voices = tomlkit.table(is_super_table=True)
        tts["voices"] = voices
    if (exaggeration, cfg_weight, tempo) == defaults and not reference:
        if voice in voices:
            del voices[voice]
    else:
        entry = tomlkit.table()
        if reference:
            entry["reference"] = reference
        entry["exaggeration"] = exaggeration
        entry["cfg_weight"] = cfg_weight
        if tempo != defaults[2]:
            entry["tempo"] = tempo
        voices[voice] = entry
    return _validated_write(path, doc)


def write_pronunciation(path: Path, word: str, spoken: str) -> Config:
    """Adds or replaces one [pronunciations] entry; an empty spoken form removes it."""
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    table = doc.get("pronunciations")
    if table is None:
        table = tomlkit.table()
        doc["pronunciations"] = table
    if spoken:
        table[word] = spoken
    elif word in table:
        del table[word]
    return _validated_write(path, doc)


# ----------------------------------------------------------------------------
# The corpus, one row per file base name
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class LineRow:
    base: str            # file base name, e.g. 415-accept, m-170-accept, 5688-19cbe7de
    subfolder: str       # Quests | Gossip
    title: str           # quest title or gossip speaker
    speaker: str
    voice: str
    raw: str             # the text as captured
    spoken: str          # what the model is asked to say (cleaned, respelled)
    level: int
    source: str

    @property
    def haystack(self) -> str:
        return f"{self.base} {self.title} {self.speaker} {self.raw}".lower()


def line_rows(items: list[Item]) -> list[LineRow]:
    rows = []
    for item in items:
        speaker = item.entry.get("name") or (item.npc or {}).get("name") or ""
        title = item.entry.get("title") or speaker
        for variant in item.variants():
            rows.append(LineRow(variant.base, item.subfolder, title, speaker, item.voice, item.raw_text, variant.text,
                                int(item.entry.get("level") or 0), item.entry.get("source") or "capture"))
    return rows


def random_line(rows: list[LineRow], voice: str, rng: random.Random | None = None) -> LineRow | None:
    """A random line spoken in `voice` (the resolved race-gender or npc voice):
    a quest line when the voice has any, else a gossip line, else None."""
    rng = rng or random.Random()
    quests = [row for row in rows if row.voice == voice and row.subfolder == "Quests"]
    pool = quests or [row for row in rows if row.voice == voice]
    return rng.choice(pool) if pool else None


def search(rows: list[LineRow], query: str, limit: int = 40) -> list[LineRow]:
    """Every word of the query must appear in the base name, title, speaker or text.
    Exact base name and title hits sort first, then by level."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    hits = [row for row in rows if all(w in row.haystack for w in words)]
    q = query.lower().strip()
    hits.sort(key=lambda r: (r.base.lower() != q, r.title.lower() != q, q not in r.title.lower(), r.level, r.base))
    return hits[:limit]


# ----------------------------------------------------------------------------
# The studio: one model, one corpus, the config file
# ----------------------------------------------------------------------------

class Studio:
    def __init__(self, config_path: Path = CONFIG_TOML, allow_cpu: bool = False):
        self.config_path = config_path
        self.allow_cpu = allow_cpu
        self.model_lock = threading.Lock()
        self.corpus_lock = threading.Lock()
        self._synth: Synth | None = None
        self._rows: list[LineRow] | None = None
        self._by_base: dict[str, tuple[Item, Variant]] = {}
        self.model_status = "not loaded"
        self.corpus_status = "not loaded"
        threading.Thread(target=self.rows, daemon=True).start()

    def config(self) -> Config:
        load_config.cache_clear()
        return load_config(self.config_path)

    def catalog(self, config: Config | None = None) -> VoiceCatalog:
        return VoiceCatalog(config or self.config())

    def synth(self) -> Synth:
        with self.model_lock:
            if self._synth is None:
                self.model_status = "loading"
                try:
                    self._synth = Synth(self.catalog(), allow_cpu=self.allow_cpu, allow_hip=True)
                except BaseException as e:
                    self.model_status = f"failed: {e}"
                    raise
                self.model_status = "ready"
            return self._synth

    def rows(self) -> list[LineRow]:
        with self.corpus_lock:
            if self._rows is None:
                self.corpus_status = "loading"
                catalog = self.catalog()
                items = load_items(load_sources(), include_progress=True, catalog=catalog)
                self._by_base = {v.base: (item, v) for item in items for v in item.variants()}
                self._rows = line_rows(items)
                self.corpus_status = f"{len(self._rows)} lines"
            return self._rows

    def forget_corpus(self) -> None:
        """After a config change the spoken text or voices may differ; reload lazily."""
        with self.corpus_lock:
            self._rows = None
            self._by_base = {}
        threading.Thread(target=self.rows, daemon=True).start()

    def line(self, base: str) -> tuple[Item, Variant]:
        self.rows()
        try:
            return self._by_base[base]
        except KeyError:
            raise HTTPException(404, f"no line with base name {base!r}") from None

    def voices(self) -> list[str]:
        return sorted(p.stem for p in VOICES_DIR.glob("*.wav"))

    def state(self) -> dict[str, Any]:
        config = self.config()
        catalog = self.catalog(config)
        resolved = {}
        for voice in self.voices():
            r = catalog.resolve(voice)
            resolved[voice] = {"clip": r.clip.name if r.clip else None, "source": r.source,
                               "exaggeration": r.settings.exaggeration, "cfg_weight": r.settings.cfg_weight,
                               "tempo": r.settings.tempo, "reference": r.settings.reference,
                               "tuned": catalog.tuned(voice)}
        return {
            "config_path": str(self.config_path),
            "voices": self.voices(),
            "narrator": config.voices.narrator,
            "defaults": {"exaggeration": config.tts.exaggeration, "cfg_weight": config.tts.cfg_weight,
                         "tempo": config.tts.tempo},
            "overrides": {v: t.model_dump(exclude_none=True) for v, t in config.tts.voices.items()},
            "resolved": resolved,
            "pronunciations": config.pronunciations.root,
            "model": self.model_status,
            "corpus": self.corpus_status,
            "bulk_service": bulk_service_state(),
        }


def bulk_service_state() -> str:
    try:
        return subprocess.run(["systemctl", "--user", "is-active", "forever-vo-bulk.service"],
                              capture_output=True, text=True, check=False, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


# ----------------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str
    reference: str | None = None                 # a clip stem to clone from instead of the voice's own resolution
    exaggeration: list[float] = Field(min_length=1, max_length=6)
    cfg_weight: list[float] = Field(min_length=1, max_length=6)
    tempo: list[float] = Field(default=[1.0], min_length=1, max_length=4)
    takes: int = Field(default=1, ge=1, le=5)


class KeepTuning(BaseModel):
    voice: str
    exaggeration: float
    cfg_weight: float
    tempo: float = 1.0
    reference: str | None = None


class KeepPronunciation(BaseModel):
    word: str = Field(min_length=1)
    spoken: str = ""


class WritePack(BaseModel):
    base: str


def _safe(name: str) -> str:
    if not SAFE_NAME.match(name):
        raise HTTPException(400, f"bad name {name!r}")
    return name


def _under(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404, relative)
    return path


def create_app(studio: Studio, dev: bool = False) -> FastAPI:
    """`dev` re-reads index.html on every request, so page edits show on a browser
    refresh; Python edits still need a restart (main's --reload does that)."""
    app = FastAPI(title="Forever Voiceover audition")
    page_file = resources.files(__package__) / "index.html"
    page = page_file.read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page_file.read_text(encoding="utf-8") if dev else page

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return studio.state()

    def payload(row: LineRow) -> dict[str, Any]:
        exists = sound_path(row.subfolder, row.base).exists()
        return {**row.__dict__, "exists": exists,
                "pack_url": f"/api/pack/{row.subfolder}/{row.base}.mp3" if exists else None}

    @app.get("/api/lines")
    def lines(q: str = Query(min_length=1)) -> list[dict[str, Any]]:
        return [payload(row) for row in search(studio.rows(), q)]

    @app.get("/api/random")
    def random_in_voice(voice: str = Query(min_length=1)) -> dict[str, Any]:
        row = random_line(studio.rows(), _safe(voice))
        if row is None:
            raise HTTPException(404, f"no lines are spoken in {voice}")
        return payload(row)

    @app.get("/api/pack/{subfolder}/{name}")
    def pack_audio(subfolder: str, name: str) -> FileResponse:
        if subfolder not in ("Quests", "Gossip"):
            raise HTTPException(404, subfolder)
        return FileResponse(_under(SOUNDS_DIR, f"{subfolder}/{_safe(name)}"), media_type="audio/mpeg")

    @app.get("/api/audio/{session}/{name}")
    def take_audio(session: str, name: str) -> FileResponse:
        return FileResponse(_under(AUDITION_DIR, f"{_safe(session)}/{_safe(name)}"), media_type="audio/mpeg")

    @app.get("/api/sessions")
    def sessions(limit: int = Query(default=12, ge=1, le=100)) -> list[dict[str, Any]]:
        """Past generate runs, newest first, so a page reload or a server restart
        does not lose the takes: they are still on disk under tools/data/audition/."""
        out = []
        if not AUDITION_DIR.exists():
            return out
        for folder in sorted((p for p in AUDITION_DIR.iterdir() if p.is_dir()), reverse=True)[:limit]:
            takes = []
            for path in sorted(folder.glob("*.mp3")):
                recipe = re.match(r"^(?P<voice>.+)-e(?P<e>[0-9.]+)-c(?P<c>[0-9.]+)(?:-t(?P<t>[0-9.]+))?-take(?P<take>\d+)\.mp3$",
                                  path.name)
                takes.append({"name": path.name, "url": f"/api/audio/{folder.name}/{path.name}",
                              "voice": recipe["voice"] if recipe else None,
                              "exaggeration": float(recipe["e"]) if recipe else None,
                              "cfg_weight": float(recipe["c"]) if recipe else None,
                              "tempo": float(recipe["t"]) if recipe and recipe["t"] else 1.0,
                              "take": int(recipe["take"]) if recipe else None})
            if takes:
                out.append({"session": folder.name, "takes": takes})
        return out

    @app.post("/api/generate")
    def generate_takes(request: GenerateRequest) -> StreamingResponse:
        _safe(request.voice)
        if request.reference:
            _safe(request.reference)
        config = studio.config()
        spoken = clean(request.text, pronunciations=config.pronunciations)
        if not spoken:
            raise HTTPException(400, "nothing to say once the text is cleaned")
        session = time.strftime("%Y%m%d-%H%M%S")
        out_dir = AUDITION_DIR / session
        out_dir.mkdir(parents=True, exist_ok=True)

        def variant_config(exaggeration: float, cfg_weight: float, tempo: float) -> Config:
            tuning = VoiceTuning(reference=request.reference, exaggeration=exaggeration, cfg_weight=cfg_weight,
                                 tempo=tempo)
            tts = config.tts.model_copy(update={"voices": {**config.tts.voices, request.voice: tuning}})
            return config.model_copy(update={"tts": tts})

        def stream() -> Iterator[str]:
            yield json.dumps({"event": "start", "session": session, "spoken": spoken}) + "\n"
            try:
                synth = studio.synth()
            except (SystemExit, RuntimeError, OSError, ImportError) as e:   # no GPU (SystemExit), CUDA or model load trouble
                yield json.dumps({"event": "error", "message": str(e)}) + "\n"
                return
            n = 0
            for exaggeration, cfg_weight, tempo in itertools.product(request.exaggeration, request.cfg_weight,
                                                                     request.tempo):
                catalog = VoiceCatalog(variant_config(exaggeration, cfg_weight, tempo))
                resolved = catalog.resolve(request.voice)
                for take in range(1, request.takes + 1):
                    n += 1
                    name = f"{request.voice}-e{exaggeration}-c{cfg_weight}-t{tempo}-take{take}.mp3"
                    t0 = time.time()
                    with studio.model_lock:
                        synth.catalog = catalog
                        seconds = synth.speak(spoken, request.voice, out_dir / name)
                    yield json.dumps({
                        "event": "take", "n": n, "take": take, "name": name, "url": f"/api/audio/{session}/{name}",
                        "seconds": round(seconds, 1), "elapsed": round(time.time() - t0, 1),
                        "clip": resolved.clip.name if resolved.clip else None, "source": resolved.source,
                        "exaggeration": resolved.settings.exaggeration, "cfg_weight": resolved.settings.cfg_weight,
                        "tempo": resolved.settings.tempo, "reference": request.reference,
                    }) + "\n"
            yield json.dumps({"event": "done", "count": n}) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/keep-tuning")
    def keep_tuning(request: KeepTuning) -> dict[str, Any]:
        _safe(request.voice)
        if request.reference:
            _safe(request.reference)
        write_tuning(studio.config_path, request.voice, request.exaggeration, request.cfg_weight, request.reference,
                     request.tempo)
        studio.forget_corpus()
        return studio.state()

    @app.post("/api/keep-pronunciation")
    def keep_pronunciation(request: KeepPronunciation) -> dict[str, Any]:
        write_pronunciation(studio.config_path, request.word.strip(), request.spoken.strip())
        studio.forget_corpus()
        return studio.state()

    @app.post("/api/write-pack")
    def write_pack(request: WritePack) -> dict[str, Any]:
        """Regenerates one pack file under the saved configuration and records it
        in sound_index.json the way generate.py would."""
        item, variant = studio.line(_safe(request.base))
        catalog = studio.catalog()
        synth = studio.synth()
        if synth.hip:
            raise HTTPException(400, "This is the ROCm build. Write to pack stays on the CUDA wheel: "
                                "a file made here would fingerprint as current and the nightly run would ship it. "
                                "Keep the settings; they are numbers in forever-vo.toml, and the CUDA generator "
                                "restages the voice from them.")
        path = sound_path(item.subfolder, variant.base)
        t0 = time.time()
        with studio.model_lock:
            synth.catalog = catalog
            seconds = synth.speak(variant.text, item.voice, path)
        index = json.loads(SOUND_INDEX.read_text(encoding="utf-8")) if SOUND_INDEX.exists() else {}
        fingerprint = catalog.fingerprint(item.voice, variant.text)
        index[variant.base] = {"d": seconds, "v": item.voice, "t": fingerprint}
        generate.save_sound_index(index, {variant.base})
        return {"base": variant.base, "voice": item.voice, "seconds": round(seconds, 1),
                "elapsed": round(time.time() - t0, 1), "fingerprint": fingerprint,
                "pack_url": f"/api/pack/{item.subfolder}/{variant.base}.mp3?t={int(time.time())}"}

    @app.post("/api/rebuild-tables")
    def rebuild_tables() -> dict[str, Any]:
        code = generate.main(["--tables-only"])
        return {"ok": code == 0}

    return app


def app_from_env() -> FastAPI:
    """uvicorn's --reload re-imports the module in a fresh process, so main() hands
    its options over through the environment and this factory builds the app."""
    import os

    studio = Studio(config_path=Path(os.environ.get("AUDITION_CONFIG", str(CONFIG_TOML))),
                    allow_cpu=os.environ.get("AUDITION_CPU") == "1")
    return create_app(studio, dev=os.environ.get("AUDITION_DEV") == "1")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=CONFIG_TOML, help="the TOML to read and write (default: the repo's)")
    parser.add_argument("--cpu", action="store_true", help="allow generating on the CPU when there is no GPU (very slow)")
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    parser.add_argument("--reload", action="store_true",
                        help="for working on the page: index.html is re-read on every request, and a change to a "
                             ".py file under tools/ restarts the server (which reloads the model, ~30 s, and "
                             "drops a generate in flight)")
    args = parser.parse_args(argv)

    import os

    import uvicorn

    os.environ["AUDITION_CONFIG"] = str(args.config)
    os.environ["AUDITION_CPU"] = "1" if args.cpu else "0"
    os.environ["AUDITION_DEV"] = "1" if args.reload else "0"
    url = f"http://{args.host}:{args.port}"
    print(f"audition: {url}  (config {args.config}{', reloading on edits' if args.reload else ''})")
    if args.open:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run("tools.audition:app_from_env", factory=True, host=args.host, port=args.port, log_level="warning",
                reload=args.reload, reload_dirs=[str(Path(__file__).resolve().parent.parent)] if args.reload else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
