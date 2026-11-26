# Perfect Audio Copy — Project Plan

## Context
The user wants a modern Linux (Fedora KDE) desktop app, **Perfect Audio Copy (PAC)**, for bit-exact secure ripping of audio CDs to FLAC, with internet metadata and cover art, a verifiable rip log, and output that Plex reads cleanly. This document is the project plan. The companion [BUILD_PROMPT.md](BUILD_PROMPT.md) is a self-contained prompt that a coding agent can follow to build the app phase by phase.

Decisions confirmed with the user:
- Stack: **Python 3.12+ / PySide6 (Qt 6) / QML**, styled to feel native on KDE Plasma.
- Metadata: **MusicBrainz + Cover Art Archive** (primary) and **Discogs** (fallback). No GD3, because it is paid.
- Scope: **CD only in v1**. DVD-Video audio is planned as a future phase.
- Extras: **CUE sheet + pre-gap/HTOA detection** and **ReplayGain tagging**. Not included: CTDB, range copy, WAV editor, CD writing, LP recording.
- Layout: `Artist/Album (Year)/NN - Title.flac` + `cover.jpg` + `.log` + `.cue`. The template can be changed.

## Architecture summary
```
perfect_audio_copy/
  drive/      SG_IO MMC transport (ctypes ioctl), INQUIRY, READ TOC (fmt 0/2/5 CD-Text),
              READ CD 0xBE (+C2, +subchannel Q), READ SUB-CHANNEL (ISRC/MCN), SET CD SPEED,
              cache defeat (FUA READ(12) / overread), udev disc-insert watcher (pyudev)
  engine/     extraction modes (Burst, Secure, Paranoid/Test&Copy), sector comparison/voting,
              offset correction, overread lead-in/out, drive feature detection, gap/index detection,
              VirtualDrive simulator (tests)
  verify/     AccurateRip v1/v2 CRC + disc IDs + DB fetch/parse, offset detection vs AR,
              CRC32 test/copy CRCs, peak level
  metadata/   TOC→MusicBrainz disc ID, MB WS/2 client (1 req/s, UA), Cover Art Archive, Discogs
              client (token), merge/ranking, local cache (SQLite)
  output/     FLAC encode (`flac -8 --verify` subprocess, parallel), tagging (mutagen, Vorbis
              comments + embedded picture), ReplayGain (`metaflac --add-replay-gain`), CUE writer,
              EAC-style log writer + SHA-256 log checksum, path template + sanitizing
  ui/         QML (Qt Quick Controls 2, org.kde.desktop style, Kirigami if available), Python
              backend QObjects/models, worker threads (QThread) with signals
  config/     TOML settings + per-drive profiles (offset, caching, C2, accurate stream)
```

## Phases
0. Scaffolding: pyproject (uv), ruff/mypy/pytest, CI script, `.desktop` file, app icon.
1. Drive layer: SG_IO transport, TOC/CD-Text/ISRC/MCN, READ CD with C2, enumeration.
2. Engine: VirtualDrive simulator first, then Burst → Secure (re-read/vote) → Test & Copy, offset + overread, speed fallback.
3. Verification: AccurateRip IDs, CRC v1/v2, DB lookup, drive offset detection, drive feature wizard.
4. Metadata: MB disc ID, MB release lookup and choosing between multiple matches, CAA art, Discogs fallback, manual edit.
5. Output: FLAC + tags + cover.jpg + ReplayGain + CUE + log. Plex-correct naming, including multi-disc.
6. UI: main disc view, rip progress, results/AR panel, drive wizard, settings, log viewer.
7. Gap/HTOA detection, polish, packaging (RPM spec + `uv tool install`), docs.
8. (Future) DVD-Video LPCM/AC3 chapter extraction.

## Verification
- Unit tests: AR CRC v1/v2 against known vectors, MB disc ID against libdiscid's documented example, CUE output against golden files, path sanitizing, tag mapping.
- VirtualDrive integration tests: injected random errors, jitter, a drive offset, caching, and C2 flags. The secure-mode output must be bit-identical to the source, and unrecoverable sectors must be reported at the correct timestamps.
- Real hardware: rip a known pressed CD and confirm the AccurateRip match (confidence > 0), confirm the same CRCs on a second rip, and confirm Plex scans the folder with correct album, artist, track, and art.
