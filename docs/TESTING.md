# Testing

## Automated (no hardware)

```bash
scripts/ci.sh
```

This runs ruff, mypy (strict) and pytest. The drive layer is tested against `tests/drive/fakes.py`, a fake transport that returns byte-exact MMC responses. Those responses cover the TOC, full TOC, CD-Text packs with CRCs, sense data, READ CD with C2 pointers (in either field order), and the Q sub-channel.

The extraction engine is tested against `VirtualDrive` (`engine/virtual.py`), which serves a synthetic disc and simulates read offsets, overread support, jitter, a drive cache (including one that ignores FUA), weak/bad/unreadable sectors, accurate or lying C2 pointers, and speed-dependent error rates. The integration tests in `tests/engine/test_extract.py` require secure mode to produce bit-identical tracks under all of these, report unrecoverable sectors at the right track timestamps, and flag every sector burst mode gets wrong.

Verification is tested against published whipper vectors (AccurateRip and CDDB disc IDs for Ladyhawke and Bloc Party), a literal port of the reference AccurateRip checksum loop, and simulated drives for offset detection and the feature wizard. The AccurateRip and drive-list clients run against recorded responses (`httpx.MockTransport`), never the live network.

## Hardware checklist

Plug in the drive, insert a commercially pressed audio CD, then work through the list.

### Phase 1 — drive layer

1. Run `.venv/bin/perfect-audio-copy-probe`. The drive should be listed with `access=rw`.
   If it says `r` only, speed control will be unavailable. If it says `NO ACCESS`, add yourself to the `cdrom` group or log in locally.
2. Run `.venv/bin/perfect-audio-copy-probe /dev/sr0 --read-test` and check that:
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

Run `.venv/bin/python -m perfect_audio_copy.verify.drivecheck` (or `perfect-audio-copy-drivecheck` once the package is reinstalled). Add `--save` to store the result in `~/.config/perfect-audio-copy/drives.toml`. It detects the drive's features, looks it up in the AccurateRip drive offset list, and, if the disc is in AccurateRip, reads three tracks to find the offsets the disc supports. Check that:

- the detected cache behaviour matches the drive. The HL-DT-ST GP75N should show `caches audio: yes`, `FUA flush works: no` and `seek-away works: yes`;
- the drive-list offset is found (GP75N: +6) and appears among the offsets the disc supports;
- a disc with several pressings in the database is reported as ambiguous instead of setting a wrong offset. The offset is only confirmed when two different discs share exactly one candidate;
- C2 is only trusted after a run with a scratched disc that shows errors and no missed flags.

### Later phases

- Rip a common pressed CD and confirm an AccurateRip match.
- Re-rip it and confirm the CRCs are identical.
- Confirm the drive offset matches the AccurateRip drive list.
- Scan the output folder in Plex and confirm the artist, album, tracks and cover art are correct.
