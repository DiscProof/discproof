# Perfect Audio Copy

Secure, verified audio CD ripping to FLAC for the Linux desktop (Fedora KDE Plasma first).
It is inspired by Exact Audio Copy: it re-reads and compares sectors, corrects the drive's read offset, verifies rips against AccurateRip, looks up metadata on MusicBrainz and Discogs, and writes Plex-ready album folders.

> **Status:** pre-alpha (0.1.0.dev0). All planned v1 features are in place. See [docs/PLAN.md](docs/PLAN.md) for the roadmap and [docs/BUILD_PROMPT.md](docs/BUILD_PROMPT.md) for the full spec.

## Install

### Fedora RPM

```bash
sudo dnf install rpm-build pyproject-rpm-macros python3-devel
scripts/build-rpm.sh
sudo dnf install dist/rpm/RPMS/noarch/perfect-audio-copy-*.noarch.rpm
```

The first builds usually stop with "Missing build dependencies": the dependencies are worked out from `pyproject.toml` during the build, first the build tools, then (because the build runs the tests) the app's own dependencies. Each time, run the `sudo dnf builddep …` command it prints, then `scripts/build-rpm.sh` again; expect this twice on a fresh system.

This installs the app system-wide, with its menu entry, icon and the Plasma "audio CD inserted" action. Runtime dependencies are pulled in automatically: Fedora's PySide6, NumPy, Pillow, mutagen, httpx, pyudev, `flac`.

### Per user, with pipx or uv

```bash
sudo dnf install python3-pyside6 flac
pipx install --system-site-packages /path/to/perfect-audio-copy
perfect-audio-copy --install-desktop      # add it to the application menu
```

`--system-site-packages` lets it use Fedora's PySide6. With `uv` (which can't see system packages), install the Qt wheel instead: `uv tool install "/path/to/perfect-audio-copy[qt-wheel]"`. The UI draws its own theme, so it looks the same either way. `perfect-audio-copy --uninstall-desktop` removes the menu entry again.

## First run

1. Insert an audio CD. The app reads it, looks it up on MusicBrainz (then Discogs, then CD-Text) and fetches the cover. If several releases match, it asks you to pick one: compare the barcode or catalog number with your case.
2. A new drive shows an amber **"hasn't been set up yet"** banner. Until you set it up, the app uses the drive's read offset from the AccurateRip drive list (if the drive is listed) and assumes the drive caches audio. That is safe, but slower.
3. Click **Set up drive**. In a minute or two it tests:
   - whether the drive caches audio, and how to empty its cache;
   - whether it has accurate stream;
   - whether its C2 error pointers can be trusted;
   - its read offset.

   A CD that is in AccurateRip lets it confirm the offset. The offset is adopted for good once two different discs agree. C2 is only trusted after a test with a scratched disc.
4. Press **Rip**. The split button chooses the mode:
   - **Secure** (default): every sector is read until two reads agree;
   - **Paranoid**: Secure plus a full test pass, and both CRCs must match;
   - **Burst**: one fast pass, relying on AccurateRip to confirm the result.

   Before reading audio, the app detects each track's pre-gap for the CUE sheet and the log. This can be turned off in Settings.

Try everything without a CD: `perfect-audio-copy --demo` runs the real UI against a simulated disc, offline.

## Output and Plex

Each album goes to `<Library>/<Album artist>/<Album> (<Year>)/` (configurable in Settings):

```
Uncle Tupelo/Anodyne (1993)/
    01 - Slate.flac …           tagged FLAC (MusicBrainz IDs, ISRC, ReplayGain, embedded cover)
    cover.jpg                   front cover, up to 1500 px
    Anodyne.cue                 CUE sheet; pre-gaps are appended to the previous track
    Anodyne.log                 EAC-style rip log with a SHA-256 checksum line
```

Multi-disc sets get a folder per disc (`Disc 1/`, `Disc 2/`), with `cover.jpg` also in the album folder. Plex reads the Vorbis comments (album artist, disc/track numbers, MusicBrainz IDs) and `cover.jpg`. Point a Plex *Music* library at the library folder and keep "Prefer local metadata" on.

## Drive permissions

The app talks to the drive directly with SCSI commands, through the kernel's SG_IO interface on `/dev/sr*`.
On a Plasma desktop, logind already gives the logged-in user read/write access.
Over SSH or on a headless system, add the user to the `cdrom` group: `sudo usermod -aG cdrom $USER`, then log in again.
If only read access is available, ripping still works, but drive speed control is disabled.

## Tools

```bash
perfect-audio-copy-probe /dev/sr0 --read-test   # drive capabilities, TOC, CD-Text, ISRC/MCN, test read
perfect-audio-copy-drivecheck --save            # drive setup from the command line
```

## Development (Fedora)

The app runs on the **system Qt** (Fedora's PySide6), so the virtualenv must see system site-packages.
The UI uses its own dark theme (shared with the portfolio-tracker project, see [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md)).

```bash
sudo dnf install python3-pyside6 python3-pillow flac uv
uv venv --system-site-packages .venv
uv pip install -e . --group dev        # or: .venv/bin/pip install -e . --group dev
.venv/bin/perfect-audio-copy           # or: --demo
scripts/ci.sh                          # ruff, mypy (strict on core), pytest
```

See [docs/TESTING.md](docs/TESTING.md) for the hardware checklist.

## Licence

GPL-3.0-or-later (see `LICENSE`). Bundled fonts: Inter and JetBrains Mono (SIL OFL 1.1). Bundled icons: Lucide (ISC).
