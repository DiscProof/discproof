"""Perfect Audio Copy: secure, verified audio CD ripping to FLAC."""

from importlib.metadata import PackageNotFoundError, version

APP_NAME = "Perfect Audio Copy"
APP_ID = "io.github.perfectaudiocopy.PerfectAudioCopy"
APP_SLUG = "perfect-audio-copy"
#: Contact URL sent to web services (MusicBrainz requires one in the User-Agent).
#: PLACEHOLDER on the reserved .invalid domain until the project has a real home
#: (e.g. its GitHub repository). Replace before release: MusicBrainz may throttle
#: clients without a reachable contact.
PROJECT_URL = "https://perfect-audio-copy.invalid"

try:
    __version__ = version("perfect-audio-copy")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"

__all__ = ["APP_ID", "APP_NAME", "APP_SLUG", "PROJECT_URL", "__version__"]
