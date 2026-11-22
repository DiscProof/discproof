# Perfect Audio Copy

Secure, verified audio CD ripping to FLAC for the Linux desktop (Fedora KDE Plasma first).
It is inspired by Exact Audio Copy: it re-reads and compares sectors, corrects the drive's read offset, verifies rips against AccurateRip, looks up metadata on MusicBrainz and Discogs, and writes Plex-ready album folders.

> **Status:** pre-alpha. Phases 0–6 are complete: drive layer, secure extraction engine, AccurateRip verification, metadata, FLAC output and the desktop UI. See [docs/PLAN.md](docs/PLAN.md) for the roadmap and [docs/BUILD_PROMPT.md](docs/BUILD_PROMPT.md) for the full spec.

## Development setup (Fedora)

The app runs on the **system Qt** (Fedora's PySide6), so the virtualenv must see system site-packages.
The UI uses its own dark theme (shared with the portfolio-tracker project, see [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md)), so it does not depend on the Plasma style.

```bash
sudo dnf install python3-pyside6 python3-pillow flac uv
uv venv --system-site-packages .venv
uv pip install -e . --group dev        # or: .venv/bin/pip install -e . --group dev
```

Run the app:

```bash
.venv/bin/perfect-audio-copy
```

Try it without a CD (a simulated drive and disc, fully offline):

```bash
.venv/bin/perfect-audio-copy --demo
```

Run all checks (ruff, mypy, pytest):

```bash
scripts/ci.sh
```

Probe a real drive and disc (TOC, CD-Text, ISRC/MCN, C2 support, test read):

```bash
.venv/bin/perfect-audio-copy-probe /dev/sr0 --read-test
```

See [docs/TESTING.md](docs/TESTING.md) for the hardware checklist.

### Drive permissions

PAC talks to the drive directly using SCSI commands, through the kernel's SG_IO interface on `/dev/sr*`.
On a Plasma desktop, logind already gives the logged-in user read/write access.
Over SSH or on a headless system, add the user to the `cdrom` group instead.
If only read access is available, ripping still works, but drive speed control is disabled.

On distributions without a system PySide6, install the wheel instead with `pip install -e '.[qt-wheel]'`.
The app then falls back to the Fusion style.
