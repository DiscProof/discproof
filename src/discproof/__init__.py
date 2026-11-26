"""DiscProof: secure, verified audio CD ripping to FLAC."""

from importlib.metadata import PackageNotFoundError, version

APP_NAME = "DiscProof"
APP_ID = "io.github.discproof.DiscProof"
APP_SLUG = "discproof"
#: Project home; also the contact URL sent to web services (MusicBrainz requires one
#: in the User-Agent).
PROJECT_URL = "https://github.com/perfectaudiocopy/perfect-audio-copy"

try:
    __version__ = version("discproof")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"

__all__ = ["APP_ID", "APP_NAME", "APP_SLUG", "PROJECT_URL", "__version__"]
