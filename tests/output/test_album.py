"""AlbumWriter end to end: VirtualDrive rip -> FLAC files, tags, ReplayGain,
cover.jpg, CUE and log."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from discs import drive, expected_tracks, noise_disc
from mutagen.flac import FLAC
from PIL import Image

from perfect_audio_copy.drive.device import DiscInfo
from perfect_audio_copy.engine.extract import Extractor
from perfect_audio_copy.engine.profile import DriveProfile, ExtractionSettings
from perfect_audio_copy.metadata.coverart import prepare_cover
from perfect_audio_copy.metadata.model import ReleaseMeta, Source, TrackMeta
from perfect_audio_copy.output import album as album_mod
from perfect_audio_copy.output.album import (
    AlbumWriter,
    ExistingPolicy,
    OutputSettings,
    folder_conflict,
)
from perfect_audio_copy.output.log import verify_log
from perfect_audio_copy.verify.accuraterip import ArEntry, ArResponse, ar_checksums, ar_disc_id

LENGTHS = [120, 90, 100]


def release(toc) -> ReleaseMeta:  # type: ignore[no-untyped-def]
    return ReleaseMeta(
        Source.MUSICBRAINZ,
        "Test Album",
        "Test Artist",
        [TrackMeta(t.number, f"Song {t.number}", "Test Artist") for t in toc.audio_tracks],
        release_id="rel-1",
        date="2001-02-03",
    )


def responses(audio: bytes, toc) -> list[ArResponse]:  # type: ignore[no-untyped-def]
    d = ar_disc_id(toc)
    tracks = toc.audio_tracks
    entries = []
    for t in tracks:
        pcm = audio[t.start * 2352 : toc.end(t.number) * 2352]
        s = ar_checksums(pcm, first=t is tracks[0], last=t is tracks[-1])
        entries.append(ArEntry(7, s.v1, 0))
    return [ArResponse(len(tracks), d.id1, d.id2, d.cddb, tuple(entries))]


def cover():  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1600), (1, 2, 3)).save(buf, "PNG")
    return prepare_cover(buf.getvalue())


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


def rip_album(tmp_path: Path, *, in_db: bool = True, existing=ExistingPolicy.SUFFIX, **writer_kw):  # type: ignore[no-untyped-def]
    audio, toc = noise_disc(LENGTHS)
    settings = OutputSettings(tmp_path / "Music", existing=existing, workers=2)
    writer = AlbumWriter(
        settings,
        release(toc),
        DiscInfo(toc, mcn="0075994542423"),
        drive="VIRTUAL DRIVE (firmware 1)",
        profile=DriveProfile(caches_audio=False),
        extraction=ExtractionSettings(),
        cover=cover(),
        ar_responses=responses(audio, toc) if in_db else None,
        ar_in_database=in_db,
        **writer_kw,
    )
    folder = writer.prepare()
    if folder is None:
        return None, audio, toc
    result = Extractor(
        drive(audio, toc), DriveProfile(caches_audio=False), on_track=writer.on_track
    ).run()
    return writer.finish(result), audio, toc


def test_full_album(tmp_path: Path) -> None:
    out, audio, toc = rip_album(tmp_path)
    assert out is not None and out.ok, out.errors
    assert out.folder == tmp_path / "Music" / "Test Artist" / "Test Album (2001)"
    names = sorted(p.name for p in out.folder.iterdir())
    assert names == [
        "01 - Song 1.flac",
        "02 - Song 2.flac",
        "03 - Song 3.flac",
        "Test Album.cue",
        "Test Album.log",
        "cover.jpg",
    ]
    expected = expected_tracks(audio, toc)
    for n, path in out.tracks.items():
        assert decode(path) == expected[n], f"track {n} not bit-identical"
        tags = FLAC(str(path))
        assert tags["COMMENT"] == ["Ripped with Perfect Audio Copy, AccurateRip confidence 7"]
        assert "REPLAYGAIN_ALBUM_GAIN" in tags and tags.pictures
    with Image.open(out.cover) as im:
        assert im.size == (1500, 1500)
    log = out.log.read_text()
    assert verify_log(log)
    assert "All tracks accurately ripped" in log and "No errors occurred" in log
    cue = out.cue.read_text()  # type: ignore[union-attr]
    for n in (1, 2, 3):
        assert f'FILE "0{n} - Song {n}.flac" WAVE' in cue
    assert "CATALOG 0075994542423" in cue


def test_not_in_database(tmp_path: Path) -> None:
    out, _, _ = rip_album(tmp_path, in_db=False)
    assert out is not None
    log = out.log.read_text()
    assert "None of the tracks are present in the AccurateRip database" in log
    tags = FLAC(str(out.tracks[1]))
    assert tags["COMMENT"] == ["Ripped with Perfect Audio Copy, not in AccurateRip database"]


@pytest.mark.parametrize("policy", list(ExistingPolicy))
def test_existing_folder_policies(tmp_path: Path, policy: ExistingPolicy) -> None:
    first, _, toc = rip_album(tmp_path)
    assert first is not None
    settings = OutputSettings(tmp_path / "Music", existing=policy)
    assert folder_conflict(settings, release(toc)) == first.folder
    second, _, _ = rip_album(tmp_path, existing=policy)
    if policy is ExistingPolicy.SKIP:
        assert second is None
    elif policy is ExistingPolicy.SUFFIX:
        assert second is not None and second.folder.name == "Test Album (2001) (2)"
    else:
        assert second is not None and second.folder == first.folder
        assert len(list(first.folder.glob("*.flac"))) == 3


def test_encoding_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = album_mod.EncoderPool.submit

    def flaky(self, job):  # type: ignore[no-untyped-def]
        if job.number == 2:
            from dataclasses import replace

            job = replace(job, pcm=b"\x01")  # flac rejects a partial sample
        return real(self, job)

    monkeypatch.setattr(album_mod.EncoderPool, "submit", flaky)
    out, _, _ = rip_album(tmp_path)
    assert out is not None and not out.ok
    assert sorted(out.tracks) == [1, 3]
    assert out.errors and out.errors[0].startswith("track 2:")
    log = out.log.read_text()
    assert "Error: flac failed for track 2" in log and "There were errors" in log


def test_cancelled_rip_writes_log_but_no_cue(tmp_path: Path) -> None:
    audio, toc = noise_disc(LENGTHS)
    settings = OutputSettings(tmp_path / "Music", workers=1)
    writer = AlbumWriter(
        settings,
        release(toc),
        DiscInfo(toc),
        drive="V",
        profile=DriveProfile(caches_audio=False),
        extraction=ExtractionSettings(),
    )
    writer.prepare()
    ext: Extractor

    def on_track(n: int, pcm: bytes) -> None:
        writer.on_track(n, pcm)
        ext.cancel()

    ext = Extractor(drive(audio, toc), DriveProfile(caches_audio=False), on_track=on_track)
    out = writer.finish(ext.run())
    assert out.cue is None and sorted(out.tracks) == [1]
    assert "Extraction was cancelled" in out.log.read_text()


def two_disc_release(toc, disc: int):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(release(toc), title="Broadcasts Vol. 3", disc_number=disc, disc_total=2)


def rip_disc(tmp_path: Path, disc: int, *, subfolders: bool = True):  # type: ignore[no-untyped-def]
    audio, toc = noise_disc(LENGTHS, seed=disc)
    settings = OutputSettings(tmp_path / "Music", workers=2, disc_subfolders=subfolders)
    writer = AlbumWriter(
        settings,
        two_disc_release(toc, disc),
        DiscInfo(toc),
        drive="V",
        profile=DriveProfile(caches_audio=False),
        extraction=ExtractionSettings(),
        cover=cover(),
    )
    assert writer.prepare() is not None
    result = Extractor(
        drive(audio, toc), DriveProfile(caches_audio=False), on_track=writer.on_track
    ).run()
    return writer.finish(result)


def test_multi_disc_set_uses_a_folder_per_disc(tmp_path: Path) -> None:
    album = tmp_path / "Music" / "Test Artist" / "Broadcasts Vol. 3 (2001)"
    one = rip_disc(tmp_path, 1)
    two_disc_settings = OutputSettings(tmp_path / "Music")
    from perfect_audio_copy.engine.virtual import make_toc

    assert folder_conflict(two_disc_settings, two_disc_release(make_toc(LENGTHS), 2)) is None
    two = rip_disc(tmp_path, 2)
    assert one.folder == album / "Disc 1" and two.folder == album / "Disc 2"
    assert sorted(p.name for p in two.folder.glob("*.flac")) == [
        "01 - Song 1.flac",
        "02 - Song 2.flac",
        "03 - Song 3.flac",
    ]
    assert (two.folder / "Broadcasts Vol. 3 (Disc 2).cue").exists()
    assert (album / "cover.jpg").exists() and (two.folder / "cover.jpg").exists()
    tags = FLAC(str(two.tracks[1]))
    assert tags["DISCNUMBER"] == ["2"] and tags["DISCTOTAL"] == ["2"]


def test_multi_disc_without_subfolders_uses_prefix(tmp_path: Path) -> None:
    out = rip_disc(tmp_path, 2, subfolders=False)
    assert out.folder.name == "Broadcasts Vol. 3 (2001)"
    assert sorted(p.name for p in out.folder.glob("*.flac"))[0] == "2-01 - Song 1.flac"
