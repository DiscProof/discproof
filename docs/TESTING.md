# Testing

## Automated (no hardware)

```bash
scripts/ci.sh
```

This runs ruff, mypy (strict) and pytest. The drive layer is tested against `tests/drive/fakes.py`, a fake transport that returns byte-exact MMC responses. Those responses cover the TOC, full TOC, CD-Text packs with CRCs, sense data, READ CD with C2 pointers, and the Q sub-channel.

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

### Later phases

- Rip a common pressed CD and confirm an AccurateRip match.
- Re-rip it and confirm the CRCs are identical.
- Confirm the drive offset matches the AccurateRip drive list.
- Scan the output folder in Plex and confirm the artist, album, tracks and cover art are correct.
