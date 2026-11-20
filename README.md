# Perfect Audio Copy

Secure, verified audio CD ripping to FLAC for the Linux desktop (Fedora KDE Plasma first).
It is inspired by Exact Audio Copy: it re-reads and compares sectors, corrects the drive's read offset, verifies rips against AccurateRip, looks up metadata on MusicBrainz and Discogs, and writes Plex-ready album folders.

> **Status:** pre-alpha. Phase 0 (project scaffolding) is complete. See [docs/PLAN.md](docs/PLAN.md) for the roadmap and [docs/BUILD_PROMPT.md](docs/BUILD_PROMPT.md) for the full spec.

## Development setup (Fedora)

The app runs on the **system Qt** so that it gets the Breeze look and Kirigami.
That is why the virtualenv must see system site-packages.

```bash
sudo dnf install python3-pyside6 kf6-kirigami kf6-qqc2-desktop-style flac uv
uv venv --system-site-packages .venv
uv pip install -e . --group dev        # or: .venv/bin/pip install -e . --group dev
```

Run the app:

```bash
.venv/bin/perfect-audio-copy
```

Run all checks (ruff, mypy, pytest):

```bash
scripts/ci.sh
```

On distributions without a system PySide6, install the wheel instead with `pip install -e '.[qt-wheel]'`.
The app then falls back to the Fusion style.
