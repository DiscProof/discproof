"""FLAC encoding (bit-exact round trip), tags, pictures and ReplayGain."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from mutagen.flac import FLAC

from perfect_audio_copy.metadata.coverart import prepare_cover
from perfect_audio_copy.metadata.model import ReleaseMeta, Source, TrackMeta
from perfect_audio_copy.output.encoder import (
    PARTIAL_SUFFIX,
    EncodeError,
    EncodeJob,
    EncoderPool,
    add_replaygain,
    encode,
)
from perfect_audio_copy.output.tags import build_tags, write_tags


def noise(seconds: float, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    return rng.integers(-8000, 8000, size=int(44100 * seconds) * 2, dtype=np.int16).tobytes()


def decode(path: Path) -> bytes:
    return subprocess.run(
        [
            "flac",
            "-d",
            "-c",
            "-s",
            "--force-raw-format",
            "--endian=little",
            "--sign=signed",
            str(path),
        ],
        capture_output=True,
        check=True,
    ).stdout


def cover():  # type: ignore[no-untyped-def]
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1400, 1400), (10, 20, 30)).save(buf, "PNG")
    return prepare_cover(buf.getvalue())


MB = ReleaseMeta(
    Source.MUSICBRAINZ,
    "Anodyne",
    "Uncle Tupelo",
    [TrackMeta(1, "Slate", "Uncle Tupelo", ("a1",), ("USRE10100695",), "rec-1", "trk-1")],
    artist_ids=("a1",),
    release_id="rel",
    release_group_id="rg",
    date="1993-10-05",
    original_date="1993",
    label="Sire",
    catalog_number="9 45424-2",
    barcode="093624542421",
)


def test_encode_is_bit_exact_and_tagged(tmp_path: Path) -> None:
    pcm = noise(2.5)
    tags = build_tags(MB, MB.tracks[0], disc_id="DISC", ar_confidence=12, ar_in_database=True)
    out = encode(EncodeJob(1, pcm, tmp_path / "a" / "01 - Slate.flac", tags, cover()))
    assert out.path.exists() and not list(tmp_path.rglob(f"*{PARTIAL_SUFFIX}"))
    assert decode(out.path) == pcm
    f = FLAC(str(out.path))
    assert f["TITLE"] == ["Slate"] and f["TRACKNUMBER"] == ["1"] and f["TRACKTOTAL"] == ["1"]
    assert f["COMMENT"] == ["Ripped with Perfect Audio Copy, AccurateRip confidence 12"]
    assert f["MUSICBRAINZ_TRACKID"] == ["rec-1"]  # Picard: recording MBID
    assert f["MUSICBRAINZ_RELEASETRACKID"] == ["trk-1"]
    assert f["MUSICBRAINZ_DISCID"] == ["DISC"] and f["ISRC"] == ["USRE10100695"]
    assert f["MEDIA"] == ["CD"] and f["BARCODE"] == ["093624542421"]
    [pic] = f.pictures
    assert (pic.type, pic.mime, pic.width, pic.height) == (3, "image/jpeg", 1000, 1000)


def test_tag_mapping_variants() -> None:
    discogs = ReleaseMeta(Source.DISCOGS, "X", "Y", [TrackMeta(1, "t", "")], release_id="42")
    tags = build_tags(discogs, discogs.tracks[0], ar_in_database=False)
    assert tags["DISCOGS_RELEASE_ID"] == ["42"] and "MUSICBRAINZ_ALBUMID" not in tags
    assert tags["ARTIST"] == ["Y"]  # falls back to the album artist
    assert tags["COMMENT"] == ["Ripped with Perfect Audio Copy, not in AccurateRip database"]
    htoa = build_tags(MB, TrackMeta(0, "", ""))
    assert htoa["TITLE"] == ["Hidden Track"] and htoa["TRACKNUMBER"] == ["0"]
    assert build_tags(MB, MB.tracks[0])["COMMENT"] == ["Ripped with Perfect Audio Copy"]


def test_rewriting_tags_replaces_everything(tmp_path: Path) -> None:
    out = encode(
        EncodeJob(1, noise(0.5), tmp_path / "t.flac", {"TITLE": ["a"], "OLD": ["x"]}, cover())
    )
    write_tags(out.path, {"TITLE": ["b"]})
    f = FLAC(str(out.path))
    assert f["TITLE"] == ["b"] and "OLD" not in f and not f.pictures


def test_failed_encode_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(EncodeError):
        encode(EncodeJob(1, b"\x00\x01\x02", tmp_path / "bad.flac", {}))  # not whole samples
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(EncodeError):
        encode(EncodeJob(1, noise(0.1), tmp_path / "x.flac", {}), level=9)


def test_pool_and_replaygain(tmp_path: Path) -> None:
    done: list[int] = []
    pool = EncoderPool(3, on_done=lambda e, job, exc: done.append(job.number))
    pcms = {n: noise(1.0, seed=n) for n in range(1, 5)}
    for n, pcm in pcms.items():
        pool.submit(EncodeJob(n, pcm, tmp_path / f"{n:02d}.flac", {"TITLE": [str(n)]}))
    pool.submit(EncodeJob(9, b"\x01", tmp_path / "09.flac", {}))
    ok, failed = pool.wait()
    pool.shutdown()
    assert [e.number for e in ok] == [1, 2, 3, 4]
    assert [n for n, _ in failed] == [9]
    assert sorted(done) == [1, 2, 3, 4, 9]
    for e in ok:
        assert decode(e.path) == pcms[e.number]
    add_replaygain([e.path for e in ok])
    tags = FLAC(str(ok[0].path))
    for key in ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_ALBUM_GAIN", "REPLAYGAIN_TRACK_PEAK"):
        assert key in tags
