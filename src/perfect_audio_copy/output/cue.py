"""CUE sheet over the separate track files, with pre-gaps appended to the previous
track (EAC's "multiple files with gaps (noncompliant)" layout, which matches how the
tracks are ripped). A track's INDEX 00 therefore sits near the end of the previous
file; its INDEX 01 is the start of its own file.
"""

from __future__ import annotations

import itertools

from perfect_audio_copy import APP_NAME
from perfect_audio_copy.output.report import AlbumReport, TrackReport


def _q(text: str) -> str:
    """CUE strings have no escapes: double quotes become single quotes."""
    return '"' + text.replace('"', "'").replace("\r", " ").replace("\n", " ") + '"'


def _msf(sectors: int) -> str:
    m, rest = divmod(sectors, 60 * 75)
    s, f = divmod(rest, 75)
    return f"{m:02d}:{s:02d}:{f:02d}"


def _track_header(lines: list[str], report: AlbumReport, t: TrackReport) -> None:
    lines.append(f"  TRACK {t.number:02d} AUDIO")
    flags = [f for f, on in (("DCP", t.copy_permitted), ("PRE", t.preemphasis)) if on]
    if flags:
        lines.append(f"    FLAGS {' '.join(flags)}")
    lines.append(f"    TITLE {_q(t.title)}")
    lines.append(f"    PERFORMER {_q(t.artist or report.release.artist)}")
    if t.isrc:
        lines.append(f"    ISRC {t.isrc}")


def render_cue(report: AlbumReport) -> str:
    r = report.release
    lines: list[str] = []
    if r.genre:
        lines.append(f"REM GENRE {_q(r.genre)}")
    if r.date:
        lines.append(f"REM DATE {r.date[:4]}")
    lines.append(f"REM DISCID {report.cddb_id:08X}")
    lines.append(f"REM COMMENT {_q(f'{APP_NAME} {report.app_version}')}")
    if r.disc_total > 1:
        lines.append(f"REM DISCNUMBER {r.disc_number}")
        lines.append(f"REM TOTALDISCS {r.disc_total}")
    if report.mcn:
        lines.append(f"CATALOG {report.mcn}")
    lines.append(f"PERFORMER {_q(r.artist)}")
    lines.append(f"TITLE {_q(r.title)}")

    tracks = [t for t in report.tracks if t.number > 0]
    htoa = next((t for t in report.tracks if t.number == 0), None)
    if not tracks:
        return "\n".join(lines) + "\n"

    first = tracks[0]
    if htoa is not None:
        # The hidden track is track 1's pre-gap, stored in its own file.
        lines.append(f"FILE {_q(htoa.filename)} WAVE")
        _track_header(lines, report, first)
        lines.append("    INDEX 00 00:00:00")
        lines.append(f"FILE {_q(first.filename)} WAVE")
        lines.append("    INDEX 01 00:00:00")
    else:
        lines.append(f"FILE {_q(first.filename)} WAVE")
        _track_header(lines, report, first)
        if first.number == report.toc.audio_tracks[0].number and first.start > 0:
            lines.append(f"    PREGAP {_msf(first.start)}")  # not ripped: silence on burn
        lines.append("    INDEX 01 00:00:00")

    for prev, t in itertools.pairwise(tracks):
        contiguous = t.number == prev.number + 1
        _track_header(lines, report, t)
        if contiguous and t.pregap:
            lines.append(f"    INDEX 00 {_msf(prev.sectors - t.pregap)}")
        lines.append(f"FILE {_q(t.filename)} WAVE")
        lines.append("    INDEX 01 00:00:00")
    return "\n".join(lines) + "\n"
