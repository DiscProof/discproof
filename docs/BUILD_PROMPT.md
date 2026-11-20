# Perfect Audio Copy — Build Prompt

You are building **Perfect Audio Copy (PAC)**, a Linux desktop application for Fedora KDE Plasma that makes bit-exact, verified copies of audio CDs into FLAC. It follows the ideas of Exact Audio Copy (EAC); read `Exact Audio Copy (EAC).md` in the repo for the behavior to replicate. Work in phases, keep the test suite green at every step, and commit after each phase.

### Product goal
Insert a CD, and PAC identifies it online, shows album info and cover art, rips every track securely, and verifies the result against AccurateRip. It then writes one folder per album:
```
<Library>/<AlbumArtist>/<Album> (<Year>)/
    01 - <Title>.flac ...      (multi-disc: "1-01 - <Title>.flac", DISCNUMBER tag set)
    cover.jpg                  (front cover, ≥ 1000 px when available, also embedded)
    <Album>.cue                (references the FLAC files; gaps appended to previous track)
    <Album>.log                (EAC-style human-readable rip log, SHA-256 self-checksum)
```
The naming template is configurable (default `{albumartist}/{album} ({year})/{disc_prefix}{tracknumber:02} - {title}`). Paths are sanitized for illegal characters and length.

### Tech stack (fixed)
- Python ≥ 3.12, managed with `uv`. PySide6 (Qt 6) with a **QML** UI (Qt Quick Controls 2 using the `org.kde.desktop` style when available, falling back to Fusion; use Kirigami components if `org.kde.kirigami` is importable). Follow the Plasma light/dark color scheme.
- Drive access: our own **SG_IO** MMC transport in pure Python (`fcntl.ioctl` + `ctypes` `sg_io_hdr`) on `/dev/sr*`, opened `O_RDONLY|O_NONBLOCK` (use O_RDWR if needed; Fedora's logind `uaccess` ACL gives the desktop user access). Use NumPy for sector comparison. Do **not** depend on cdparanoia for the secure engine; you may offer it as a fallback for drives that reject READ CD.
- Encoding: the reference `flac` CLI (`-8 --verify`), run in parallel across CPU cores while ripping continues. Tags with `mutagen`. ReplayGain with `metaflac --add-replay-gain` (album + track).
- Networking: `httpx`. Disc-insert detection: `pyudev`. Config: TOML in `~/.config/perfect-audio-copy/`; cache/DB in `~/.local/share/perfect-audio-copy/`.
- Tooling: `ruff`, `mypy --strict` on core packages, `pytest`. Fedora runtime deps: `flac`, `python3-pyside6` or the PySide6 wheel, `kf6-kirigami` (optional).

### Module layout
`drive/`, `engine/`, `verify/`, `metadata/`, `output/`, `ui/`, `config/` (see PLAN.md). The core packages must not import Qt; the UI talks to them through a thin QObject backend layer and worker QThreads that emit progress signals.

### Phase 1: Drive layer (`drive/`)
- `ScsiTransport`: SG_IO with timeouts, sense-data decoding (key/ASC/ASCQ), and retries for UNIT ATTENTION / NOT READY.
- Commands: INQUIRY (vendor/model/revision), TEST UNIT READY, READ TOC/PMA/ATIP format 0 (TOC), format 2 (full TOC: multisession/CD-Extra, data tracks, lead-out), format 5 (CD-Text → album/track titles and performers), READ SUB-CHANNEL (0x42) for MCN/UPC and per-track ISRC, **READ CD (0xBE)** for 2352-byte CD-DA sectors with optional C2 error pointers (294 bytes) and optional formatted Q subchannel, READ(12) with FUA (cache bypass), SET CD SPEED (0xBB), GET CONFIGURATION / MODE SENSE page 2A (capabilities: C2 support, max speed).
- `Drive.enumerate()` lists optical drives with model strings. `TOC` dataclass: tracks (number, start LBA, audio/data, pre-emphasis, copy-permitted), lead-out, session info.
- Read in batches (default 26 sectors per command; configurable) and recover gracefully from transfer-length errors.

### Phase 2: Extraction engine (`engine/`)
Build a **`VirtualDrive`** first, implementing the same interface as the real drive. It serves sectors from a WAV source and can simulate a read offset, jitter (random start misalignment for drives without accurate stream), a cache (repeat reads return identical data unless defeated), random and persistent read errors, and C2 flags that are correct or that lie. All engine tests run against it.

Extraction modes:
1. **Burst**: one pass at max speed with no comparison. Report positions where a READ fails or the stream breaks as "suspicious". Rely on AccurateRip to confirm the result.
2. **Secure** (default):
   - Each batch is read twice, with the cache defeated between reads. Choose per drive: FUA READ, or overreading a region further away than the detected cache size. Accept the batch if both reads are identical and, if C2 is trusted, report no C2 errors.
   - On a mismatch or C2 error, re-read that region in rounds of 16 reads. A sector is accepted when ≥ 8 of 16 reads agree. Allow at most 1/3/5 rounds per sector (error-recovery quality Low/Medium/High), so the worst case is 82 reads, as in EAC.
   - If no majority is reached, keep the most frequent version, mark the sector **suspicious**, and record its track and timestamp (mm:ss.ff).
   - Fast path: if the drive has accurate stream, no cache, and trusted C2, read once and re-read only sectors flagged by C2.
3. **Test & Copy** (option, on top of Secure): a full test pass, then a copy pass. Log both CRC32s per track and flag any mismatch.
- **Read offset correction** (sample-accurate, ±several thousand samples): shift the stream by the drive offset. Overread into the lead-in/lead-out if the drive supports it (detected); otherwise fill missing samples with silence, so the track length is always correct.
- **Speed management**: on repeated errors, drop speed (SET CD SPEED) for that region, then restore it afterwards.
- **Jitter/sync for non-accurate-stream drives**: overlap consecutive batches by N sectors and align by searching for the overlap (as paranoia does), including at track junctions ("track synchronization").
- Gap handling: the default is to append a pre-gap to the previous track (the AccurateRip-compatible layout). If track 1 index 01 > 0, detect **HTOA** and offer to rip it as track `00`.
- The engine yields progress events: sector position, current speed, retries, suspicious positions, and per-track CRCs and peak level.

### Phase 3: Verification (`verify/`)
- **AccurateRip**: compute the disc IDs (track count, id1, id2, FreeDB/CDDB ID; include data-track handling for CD-Extra). Fetch `http://www.accuraterip.com/accuraterip/{id1&0xF:x}/{id1>>4&0xF:x}/{id1>>8&0xF:x}/dBAR-{n:03d}-{id1:08x}-{id2:08x}-{cddb:08x}.bin` and parse all pressings/responses.
  - Compute **ARv1 and ARv2 CRCs** per track. On the first track, skip the first 5 frames (2,939 samples, i.e. 5 × 588 − 1); on the last track, skip the last 5 × 588 samples.
  - Report "Accurately ripped (confidence N) [v1/v2]" or "not present in database".
  - Match against other pressings' offsets as well, and report pressing offset differences.
- **Drive offset detection**: (a) look up the drive model in the AccurateRip drive offset list (download and cache it); (b) detect the offset from any CD in the AR database by trying offsets in ±3,000 samples on the first tracks. Require agreement from two CDs before saving the offset to the drive profile, as EAC does.
- **Drive feature detection wizard**: test (1) accurate stream (repeated reads from different start points must align at the same sample offset); (2) caching (timing plus identical-data test, then estimate cache size; if results are inconsistent, assume the drive caches); (3) C2 support and trustworthiness (compare C2 flags against known-bad re-reads; ask the user to insert a scratched CD for a real test). Store results per drive (vendor+model+firmware) in config.
- Compute per-track CRC32 ("Copy CRC"; "Test CRC" in Test & Copy mode) and peak level (%).

### Phase 4: Metadata (`metadata/`)
- Compute the **MusicBrainz disc ID** from the TOC (SHA-1 of the formatted TOC, MB's Base64 variant). Validate it against the libdiscid test vector. Look up `https://musicbrainz.org/ws/2/discid/{id}?inc=artists+recordings+release-groups+labels+isrcs&fmt=json`, falling back to a TOC fuzzy lookup.
  - Respect a rate limit of 1 request per second.
  - Send the User-Agent `PerfectAudioCopy/<version> ( <project url> )`. Do not put the user's email in it.
- When several releases match, show a chooser with country, date, label, catalog number, barcode, and format, and preselect a release whose barcode matches the disc's MCN.
- **Cover Art Archive**: front image for the release (falling back to the release-group). Prefer the 1200 px thumbnail or the original; convert to JPEG if needed; cap at 1500 px for `cover.jpg`; embed a ≤ 1000 px copy as a FLAC PICTURE block (type 3).
- **Discogs** fallback (the user supplies a personal token in Settings): search by barcode, then by artist + album + track count. Use Discogs images when CAA has none.
- Use CD-Text as a last resort, and always allow manual editing of every field in the UI. Cache lookups in SQLite, keyed by disc ID.

### Phase 5: Output (`output/`)
- **Tags** (Vorbis comments, Plex/MusicBrainz Picard compatible): TITLE, ARTIST, ALBUMARTIST, ALBUM, TRACKNUMBER, TRACKTOTAL, DISCNUMBER, DISCTOTAL, DATE, ORIGINALDATE, GENRE, LABEL, CATALOGNUMBER, BARCODE, ISRC, MEDIA=CD, MUSICBRAINZ_ALBUMID, MUSICBRAINZ_ARTISTID, MUSICBRAINZ_ALBUMARTISTID, MUSICBRAINZ_TRACKID, MUSICBRAINZ_RELEASEGROUPID, MUSICBRAINZ_DISCID, COMMENT (`Ripped with Perfect Audio Copy, AccurateRip confidence N`), REPLAYGAIN_* (via metaflac).
- **Encoding pipeline**: write each track to a temp WAV/PCM, run `flac -8 --verify`, write tags and the picture, move the file into place atomically, and delete the temp file. Encode in parallel with extraction.
- **CUE sheet**: CATALOG, per-track ISRC, TITLE/PERFORMER, FLAGS PRE/DCP, INDEX 00/01 laid out as "gaps appended to previous track" over the separate FLAC files.
- **Log**: EAC-like and plain text. Include:
  - App version, date, drive model, read offset, overread, cache and C2 settings, extraction mode, and gap handling.
  - TOC table (track, start, length, start/end sector).
  - Per track: filename, pre-gap, peak level, extraction speed, Test CRC / Copy CRC, AR v1/v2 result, suspicious positions, and "Copy OK" / "Copy finished with errors".
  - Overall summary.
  - A final `==== Log checksum <SHA-256> ====` line computed over the preceding text.
- Handle existing folders by asking, with options to overwrite, add a suffix, or skip.

### Phase 6: UI (`ui/`, QML)
Aim for a sleek, modern, uncluttered UI that matches Plasma 6.
- **Main window**:
  - Header with the drive selector, an eject button, and the disc status.
  - Left: large cover art (with drag-and-drop or paste to replace), plus editable album artist, album, year, genre, disc n/m, and the release chooser.
  - Right: track table with checkbox, #, title (inline edit), artist, length, pre-gap, status chip (Pending, Reading, Re-reading, OK, Suspicious, AR ✓ N), and CRC.
  - Primary **Rip** button, with the mode (Burst / Secure / Paranoid) in a split-button.
- **Rip progress**:
  - Overall and per-track progress, and live speed (x).
  - A sector-map strip per track (green = first-pass match, amber = recovered after re-reads, red = suspicious).
  - Elapsed time and ETA, plus pause and cancel.
- **Results panel**: per-track AccurateRip results, totals, "Open folder", and "View log". Send a desktop notification on completion (Plasma notification via `QSystemTrayIcon` or D-Bus).
- **Drive setup wizard** (first run, and for every new drive): detect the offset (AR list, then disc-based confirmation), test features, set overread.
- **Settings**: library path, naming template with live preview, extraction mode defaults, error-recovery quality, FLAC compression level, ReplayGain on/off, cover sizes, metadata sources and Discogs token, gap handling (append to previous / HTOA as track 00 / discard), Test & Copy toggle, eject when done.
- Auto-detect disc insertion (pyudev) and auto-lookup metadata. Optionally rip automatically when metadata is unambiguous.
- Keep the UI responsive at all times: all I/O runs on worker threads.

### Phase 7: Gap detection, polish, packaging
- **Pre-gap / index detection**: read the Q subchannel via READ CD and binary-search the INDEX 00→01 transitions near each track start. Accept ±2 sectors of drive inaccuracy, and confirm with a second read. Detect HTOA and silence in gaps.
- Package a `.desktop` file + SVG icon + AppStream metainfo, install with `uv tool install` or pipx, and add an RPM spec for Fedora. Do not use Flatpak in v1, because raw SG_IO device access in the sandbox is awkward.
- Write a README covering install, permissions (`/dev/sr0` access, `cdrom` group fallback), first-run drive setup, and Plex notes.

### Phase 8 (future, do not build in v1)
DVD-Video music discs: extract LPCM/AC3 audio per chapter (libdvdread / ffmpeg) to FLAC (decoding AC3 is lossy, so warn the user), with the same metadata and output pipeline.

### Quality bar and tests
- Unit tests:
  - AR v1/v2 CRC and disc IDs against known vectors (cross-check with whipper's / ARCue's published examples).
  - MusicBrainz disc ID against the libdiscid example.
  - CUE and log rendering against golden files.
  - Path sanitizing, and tag mapping from MB JSON fixtures.
- VirtualDrive integration tests:
  - Secure mode yields a **bit-identical** output with errors, jitter, cache, and offset simulated.
  - Unrecoverable sectors are reported at the right timestamp.
  - Burst mode differs and is flagged.
  - Offset detection finds the injected offset.
- Network clients are tested against recorded fixtures, with no live network in CI.
- Manual hardware checklist (in `docs/TESTING.md`):
  - Rip a common pressed CD and confirm the AR match.
  - Re-rip and confirm identical CRCs.
  - Confirm the drive offset matches the AccurateRip drive list.
  - Scan the output folder in Plex and confirm correct artist, album, tracks, and art.
- Never block the UI thread. Never silently lose an error: every anomaly must appear in the log.

### Working rules
Implement phase by phase. At the end of each phase, run `ruff`, `mypy`, and `pytest`, summarize what works, and commit. Ask before adding dependencies beyond those listed. Where a hardware behavior is uncertain, make it a per-drive setting with a sensible default and a log entry rather than a hard assumption.
