# Testing

## Automated (no hardware)

```bash
scripts/ci.sh
```

This runs ruff, mypy (strict) and pytest. The drive layer is tested against `tests/drive/fakes.py`, a fake transport that returns byte-exact MMC responses. Those responses cover the TOC, full TOC, CD-Text packs with CRCs, sense data, READ CD with C2 pointers (in either field order), and the Q sub-channel.

The extraction engine is tested against `VirtualDrive` (`engine/virtual.py`), which serves a synthetic disc and simulates read offsets, overread support, jitter, a drive cache (including one that ignores FUA), weak/bad/unreadable sectors, accurate or lying C2 pointers, and speed-dependent error rates. The integration tests in `tests/engine/test_extract.py` require secure mode to produce bit-identical tracks under all of these, report unrecoverable sectors at the right track timestamps, and flag every sector burst mode gets wrong.

Verification is tested against published whipper vectors (AccurateRip and CDDB disc IDs for Ladyhawke and Bloc Party), a literal port of the reference AccurateRip checksum loop, and simulated drives for offset detection and the feature wizard. The AccurateRip and drive-list clients run against recorded responses (`httpx.MockTransport`), never the live network.

Metadata is tested against responses recorded once from the live services for the MusicBrainz documentation example disc (`49HHV7Eb8UKF3aQiNmu1GR8vKTY-`): the MusicBrainz disc ID and TOC lookups, its Cover Art Archive listing, and the same release on Discogs. The disc ID algorithm is checked against that documented example.

Output is tested with golden CUE and log files (`tests/output/golden`; regenerate with `UPDATE_GOLDEN=1` and review the diff), a bit-exact FLAC encode/decode round trip, and a full album written from a VirtualDrive rip.

## Hardware checklist

Plug in the drive, insert a commercially pressed audio CD, then work through the list.

### Phase 1 — drive layer

1. Run `.venv/bin/discproof-probe`. The drive should be listed with `access=rw`.
   If it says `r` only, speed control will be unavailable. If it says `NO ACCESS`, add yourself to the `cdrom` group or log in locally.
2. Run `.venv/bin/discproof-probe /dev/sr0 --read-test` and check that:
   - the TOC track count and lengths match the CD's case or a player's display;
   - an Enhanced CD (one with a data track) shows `sessions 2, Enhanced CD`, and its audio lead-out is smaller than its lead-out;
   - the MCN and ISRCs appear if the disc carries them (many discs don't);
   - CD-Text appears if the disc has it (not all drives can read CD-Text);
   - the read test reports a plausible speed and `C2 error bits: 0` on a clean disc;
   - the Q sub-channel reports track 1 and index 1 at the track start.
3. Run `PAC_TEST_DRIVE=/dev/sr0 .venv/bin/pytest -m hardware -v`.

Please save the probe output for each drive you own. It is the baseline for the Phase 3 feature-detection work.

### Phase 2 — extraction engine

The hardware run above includes `test_secure_rip_is_repeatable`, which secure-rips the shortest audio track twice with the default (conservative) drive profile and checks that both CRCs match and nothing was suspicious. Expect it to take about a minute: with cache defeat, secure mode runs at roughly a third of the drive's speed.

### Phase 3 — verification and drive setup

Run `.venv/bin/python -m discproof.verify.drivecheck` (or `discproof-drivecheck` once the package is reinstalled). Add `--save` to store the result in `~/.config/discproof/drives.toml`. It detects the drive's features, looks it up in the AccurateRip drive offset list, and, if the disc is in AccurateRip, reads three tracks to find the offsets the disc supports. Check that:

- the detected cache behaviour matches the drive. The HL-DT-ST GP75N should show `caches audio: yes`, `FUA flush works: no` and `seek-away works: yes`;
- the drive-list offset is found (GP75N: +6) and appears among the offsets the disc supports;
- a disc with several pressings in the database is reported as ambiguous instead of setting a wrong offset. The offset is only confirmed when two different discs share exactly one candidate;
- C2 is only trusted after a run with a scratched disc that shows errors and no missed flags.

### Phase 4 — metadata

There is no separate tool yet (the UI arrives in Phase 6). With a disc inserted, run:

```bash
.venv/bin/python -c "
from discproof.drive.device import Drive
from discproof.metadata.service import MetadataService
with Drive.open('/dev/sr0') as d: info = d.read_disc_info()
svc = MetadataService(); res = svc.lookup(info)
print(res.disc_id, res.errors, res.preselected)
for r in res.releases: print(r.source.value, r.artist, '-', r.title, '|', r.summary())
print(svc.cover(res.releases[res.preselected or 0]))"
```

Check that a common pressed CD is found by disc ID (`exact=True`) and preselected, that the track titles match the case, and that a cover of at least 1000 px is found. Compare the disc ID with what MusicBrainz shows for that release. A disc that MusicBrainz doesn't know should fall back to a TOC match, then Discogs (by its MCN), then CD-Text.

The User-Agent names the app and its home page (`PROJECT_URL` in `src/discproof/__init__.py`), as MusicBrainz asks.

### Phase 5 — output

Rip one short track end to end (drive, metadata, AccurateRip, FLAC, tags, cover, CUE, log) into a test folder, then check:

- the FLAC file decodes and plays; `metaflac --list --block-type=VORBIS_COMMENT,PICTURE` shows the tags (Picard mapping: `MUSICBRAINZ_TRACKID` is the recording MBID), ReplayGain values and a 1000 px front cover;
- `cover.jpg` is at most 1500 px;
- the log says "Accurately ripped" for a disc in AccurateRip, and its checksum verifies (`discproof.output.log.verify_log`);
- the CUE sheet names the FLAC files that are next to it.

On 2026-11-22 this was done with the HL-DT-ST GP75N (offset +6, cache defeated by reading elsewhere) on Uncle Tupelo's *Anodyne*, track 2: accurately ripped, confidence 112 (v1 and v2), at 1.2x.

### Phase 6 — user interface

`discproof --demo` runs the real UI and pipeline against a simulated drive and disc, offline. The demo disc has weak sectors on track 3 and a bad sector on track 5. With a real drive, check that:

- inserting a disc reads it and looks it up automatically (udev); ejecting clears it, except after a rip, when the results stay until the next disc;
- a new, untested drive shows the amber "not set up" banner, and the drive wizard reports the GP75N as caching audio (FUA ignored, cleared by reading elsewhere), with accurate stream and the list offset +6;
- album and track fields can be edited, and the cover can be replaced by drag-and-drop or Ctrl+V;
- during a rip, the progress, speed, ETA and sector maps update, and pause, resume and cancel work;
- the results card shows the AccurateRip outcome; "Open folder" and "View log" work; a Plasma notification appears.

UI logic is tested headlessly through the demo (`tests/ui`), and the smoke tests load the full window and fail on any QML warning.

### Phase 7 — gaps and packaging

- Rip a disc with known pre-gaps (most pressed CDs have 2 s gaps; compare with EAC's or a player's negative countdown). Check the Gap column, the log's "Pre-gap length … (silent)" lines and the CUE sheet's `INDEX 00` entries.
- A disc with a hidden track before track 1: the log says whether it contains audio, and "Rip as track 00" rips it.
- Build the RPM (`scripts/build-rpm.sh`), install it, and check that the menu entry and icon appear and that inserting an audio CD offers "Rip with DiscProof" in Plasma's device notifier.
- pipx install plus `--install-desktop`: the menu entry starts the pipx copy.

### Later phases

- Rip a common pressed CD and confirm an AccurateRip match.
- Re-rip it and confirm the CRCs are identical.
- Confirm the drive offset matches the AccurateRip drive list.
- Scan the output folder in Plex and confirm the artist, album, tracks and cover art are correct.
