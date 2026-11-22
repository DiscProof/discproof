"""The rip log: EAC-like plain text (UTF-8), ending in a SHA-256 checksum line over
everything before it, so later edits can be detected with :func:`verify_log`.

Every anomaly the engine reported appears here: suspicious positions, re-read
sectors, silence-filled samples, Test/Copy CRC mismatches, encoding errors.
"""

from __future__ import annotations

import hashlib
import re

from perfect_audio_copy import APP_NAME
from perfect_audio_copy.drive.msf import frames_to_timestamp
from perfect_audio_copy.engine.profile import CacheDefeat, Mode
from perfect_audio_copy.metadata.model import Source
from perfect_audio_copy.output.report import AlbumReport, TrackReport

_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_CHECKSUM = re.compile(r"\n==== Log checksum ([0-9A-F]{64}) ====\n?$")


def _yn(v: bool) -> str:
    return "Yes" if v else "No"


def _when(report: AlbumReport) -> str:
    c = report.created
    return f"{c.day} {_MONTHS[c.month - 1]} {c.year}, {c.hour:02d}:{c.minute:02d}"


def _settings(report: AlbumReport) -> list[str]:
    p, s = report.profile, report.settings
    if p.caches_audio:
        how = "FUA" if p.cache_defeat is CacheDefeat.FUA else "reading elsewhere"
        cache = f"Yes ({how}, {p.cache_sectors} sectors)"
    else:
        cache = "No (drive does not cache audio)"
    mode = "Burst" if s.mode is Mode.BURST else "Secure"
    if s.mode is Mode.SECURE and p.fast_path:
        mode += " (fast: C2-guided)"
    rounds = s.quality.value
    htoa_track = next((t for t in report.tracks if t.number == 0), None)
    first = report.toc.audio_tracks[0]
    if htoa_track is not None:
        htoa = f"Ripped as track 00 ({frames_to_timestamp(htoa_track.sectors)})"
    elif first.number == 1 and first.start > 0:
        htoa = f"Present ({frames_to_timestamp(first.start)}), not ripped"
    else:
        htoa = "None"
    rg = "Album and track" if report.replaygain else "No"
    return [
        f"Read mode               : {mode}",
        f"Utilize accurate stream : {_yn(p.accurate_stream)}",
        f"Defeat audio cache      : {cache}",
        f"Make use of C2 pointers : {_yn(p.use_c2)}",
        "",
        f"Read offset correction                      : {p.read_offset}",
        f"Overread into Lead-In and Lead-Out          : "
        f"{_yn(p.overread_leadin and p.overread_leadout)}",
        "Fill up missing offset samples with silence : Yes",
        "Gap handling                                : Appended to previous track"
        + ("" if any(t.pregap is not None for t in report.tracks) else " (gaps not detected)"),
        f"Error recovery quality                      : {s.quality.name.title()} "
        f"(up to {rounds} round{'s' if rounds > 1 else ''} of {s.reads_per_round} reads)",
        f"Test & Copy                                 : {_yn(s.test_and_copy)}",
        f"Hidden track one audio                      : {htoa}",
        "",
        f"Used output format : FLAC (flac -{report.flac_level} --verify)",
        f"ReplayGain         : {rg}",
    ]


def _ids(report: AlbumReport) -> list[str]:
    r = report.release
    source = {
        Source.MUSICBRAINZ: f"MusicBrainz release {r.release_id}",
        Source.DISCOGS: f"Discogs release {r.release_id}",
        Source.CDTEXT: "CD-Text",
        Source.MANUAL: "Entered manually",
    }[r.source]
    lines = [
        f"MusicBrainz disc ID : {report.mb_disc_id}",
        f"FreeDB/CDDB disc ID : {report.cddb_id:08x}",
    ]
    if report.ar_disc:
        lines.append(f"AccurateRip disc ID : {report.ar_disc}")
    if report.mcn:
        lines.append(f"MCN                 : {report.mcn}")
    lines.append(f"Metadata source     : {source}")
    return lines


def _toc(report: AlbumReport) -> list[str]:
    toc = report.toc
    lines = [
        "     Track |   Start  |  Length  | Start sector | End sector",
        "    ---------------------------------------------------------",
    ]
    for t in toc.tracks:
        length = toc.length(t.number)
        kind = "" if t.is_audio else "  (data)"
        lines.append(
            f"       {t.number:3d} | {frames_to_timestamp(t.start):>8} | "
            f"{frames_to_timestamp(length):>8} | {t.start:12d} | {t.start + length - 1:10d}{kind}"
        )
    return lines


def _ar_line(t: TrackReport, report: AlbumReport) -> str:
    v = t.ar
    if t.number == 0:
        return "AccurateRip: not applicable to the hidden track"
    if report.ar_in_database is None:
        return "AccurateRip: not checked" + (f" ({report.ar_error})" if report.ar_error else "")
    if v is None:
        return "AccurateRip: no result"
    sums = f"[v1 {v.checksums.v1:08X}, v2 {v.checksums.v2:08X}]"
    if not report.ar_in_database:
        return f"Track not present in AccurateRip database  {sums}"
    if v.accurate:
        return f"Accurately ripped (confidence {v.confidence}, {v.versions})  {sums}"
    if v.max_confidence:
        return f"Cannot be verified as accurate (database confidence {v.max_confidence})  {sums}"
    return f"Track not present in AccurateRip database  {sums}"


def _track(t: TrackReport, report: AlbumReport) -> list[str]:
    label = "Hidden track" if t.number == 0 else f"Track {t.number:2d}"
    lines = [label, "", f"     Filename {t.filename}", ""]
    if t.number > 0:
        gap = "unknown (not detected)" if t.pregap is None else frames_to_timestamp(t.pregap)
        if t.pregap and t.pregap_silent is not None:
            gap += " (silent)" if t.pregap_silent else " (contains audio)"
        lines += [f"     Pre-gap length  {gap}", ""]
    rip = t.rip
    if rip is None:
        lines.append("     Not extracted")
    else:
        lines += [
            f"     Peak level {rip.peak_percent:.1f} %",
            f"     Extraction speed {rip.speed_x:.1f} X",
            f"     Re-read sectors {rip.recovered_sectors}",
        ]
        if rip.filled_samples:
            lines.append(f"     Samples filled with silence (offset) {rip.filled_samples}")
        if rip.test_crc32 is not None:
            lines.append(f"     Test CRC {rip.test_crc32:08X}")
        lines.append(f"     Copy CRC {rip.crc32:08X}")
        if rip.crc_mismatch:
            lines.append("     Test and Copy CRCs differ")
        for pos in rip.suspicious:
            lines.append(f"     Suspicious position {pos}")
    lines.append(f"     {_ar_line(t, report)}")
    if t.error:
        lines.append(f"     Error: {t.error}")
    lines.append("     Copy OK" if t.ok else "     Copy finished with errors")
    return lines


def _summary(report: AlbumReport) -> list[str]:
    lines: list[str] = []
    numbered = [t for t in report.tracks if t.number > 0]
    if report.ar_in_database is None:
        msg = "AccurateRip was not checked"
        lines.append(msg + (f": {report.ar_error}" if report.ar_error else ""))
    elif not report.ar_in_database:
        lines.append("None of the tracks are present in the AccurateRip database")
    else:
        accurate = [t for t in numbered if t.ar and t.ar.accurate]
        lines.append("AccurateRip summary")
        lines.append("")
        for t in numbered:
            v = t.ar
            if v and v.accurate:
                state = f"accurately ripped (confidence {v.confidence}, {v.versions})"
            elif v and v.max_confidence:
                state = "cannot be verified as accurate"
            else:
                state = "not present in database"
            lines.append(f"Track {t.number:2d}  {state}")
        lines.append("")
        if len(accurate) == len(numbered):
            lines.append("All tracks accurately ripped")
        else:
            lines.append(
                f"{len(numbered) - len(accurate)} track(s) not verified, "
                f"{len(accurate)} accurately ripped"
            )
        for p in report.pressings:
            tracks = ", ".join(str(n) for n in p.tracks)
            lines.append(
                f"Track(s) {tracks} match a different pressing (offset {p.offset:+d} samples)"
            )
    lines.append("")
    if report.cancelled:
        lines.append("Extraction was cancelled")
    elif report.ok:
        lines.append("No errors occurred")
    else:
        lines.append("There were errors")
    for note in report.notes:
        lines.append(f"Note: {note}")
    return lines


def render_log(report: AlbumReport) -> str:
    r = report.release
    body = [
        f"{APP_NAME} {report.app_version} extraction logfile from {_when(report)}",
        "",
        f"{r.artist} / {r.title}"
        + (f" (disc {r.disc_number} of {r.disc_total})" if r.disc_total > 1 else ""),
        "",
        f"Used drive  : {report.drive}",
        "",
        *_settings(report),
        "",
        *_ids(report),
        "",
        "",
        "TOC of the extracted CD",
        "",
        *_toc(report),
        "",
    ]
    for t in report.tracks:
        body += ["", *_track(t, report), ""]
    body += ["", *_summary(report), "", "End of status report", ""]
    text = "\n".join(body)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
    return f"{text}\n==== Log checksum {digest} ====\n"


def verify_log(text: str) -> bool:
    """True if the log's checksum line matches its content (no edits since)."""
    m = _CHECKSUM.search(text)
    if not m:
        return False
    body = text[: m.start()]
    return hashlib.sha256(body.encode("utf-8")).hexdigest().upper() == m.group(1)
