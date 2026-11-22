"""File and folder names from a template.

The template uses ``str.format`` fields separated by ``/`` into folders; the last
part is the track file's name (without extension). Field values are sanitized
*before* substitution, so a "/" in a title never creates a folder.

Fields: albumartist, album, year, date, title, artist, tracknumber, tracktotal,
discnumber, disctotal, disc_prefix ("1-" on multi-disc releases, else ""), genre,
label, catalognumber.
"""

from __future__ import annotations

import re
import string
import unicodedata
from dataclasses import dataclass
from typing import Any

DEFAULT_TEMPLATE = "{albumartist}/{album} ({year})/{disc_prefix}{tracknumber:02} - {title}"
#: Bytes per path component; leaves room below the usual 255 for suffixes.
MAX_COMPONENT_BYTES = 200
FIELDS = {
    "albumartist",
    "album",
    "year",
    "date",
    "title",
    "artist",
    "tracknumber",
    "tracktotal",
    "discnumber",
    "disctotal",
    "disc_prefix",
    "genre",
    "label",
    "catalognumber",
}

# Characters Windows/Samba (and so Plex libraries on NAS shares) cannot store.
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_EMPTY_GROUPS = re.compile(r"\s*(\(\s*\)|\[\s*\])")
_SPACES = re.compile(r"\s{2,}")
_DANGLING = re.compile(r"(^-\s+|\s+-$)")


class TemplateError(ValueError):
    pass


def truncate_utf8(text: str, max_bytes: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def sanitize(value: str, *, max_bytes: int = MAX_COMPONENT_BYTES) -> str:
    """One path component: no separators, control or reserved characters, no
    leading dots, no trailing dots/spaces, NFC-normalized, length-limited."""
    text = unicodedata.normalize("NFC", value)
    text = _CONTROL.sub("", text)
    text = _ILLEGAL.sub("_", text)
    text = _SPACES.sub(" ", text).strip()
    text = text.lstrip(".")
    text = truncate_utf8(text, max_bytes).rstrip(". ")
    return text


@dataclass(frozen=True, slots=True)
class TrackName:
    folder: tuple[str, ...]  # album folder components, relative to the library
    stem: str  # track file name without extension


def _tidy(component: str) -> str:
    """Remove empty "()" / "[]" left by missing fields, collapse spaces."""
    text = _EMPTY_GROUPS.sub("", component)
    text = _SPACES.sub(" ", text).strip()
    # A separator left dangling by an empty field: "Artist - " / " - Title".
    return _DANGLING.sub("", text).strip() or text


def render(template: str, fields: dict[str, Any]) -> TrackName:
    """Apply the template. Raises TemplateError for unknown fields or bad syntax."""
    if not template.strip():
        raise TemplateError("the naming template is empty")
    safe: dict[str, Any] = {}
    for k, v in fields.items():
        safe[k] = sanitize(v) if isinstance(v, str) else v
    try:
        for _, name, _, _ in string.Formatter().parse(template):
            if name is not None and name.split(".")[0].split("[")[0] not in FIELDS:
                raise TemplateError(f"unknown field {{{name}}}")
        rendered = template.format(**safe)
    except (KeyError, ValueError, IndexError) as exc:
        if isinstance(exc, TemplateError):
            raise
        raise TemplateError(f"invalid template: {exc}") from exc
    parts = [_tidy(p) for p in rendered.split("/")]
    parts = [sanitize(p) or "_" for p in parts if p.strip()]
    if not parts:
        raise TemplateError("the template produces an empty file name")
    return TrackName(tuple(parts[:-1]), parts[-1])


def track_fields(
    *,
    albumartist: str,
    album: str,
    date: str | None,
    title: str,
    artist: str,
    tracknumber: int,
    tracktotal: int,
    discnumber: int = 1,
    disctotal: int = 1,
    genre: str | None = None,
    label: str | None = None,
    catalognumber: str | None = None,
) -> dict[str, Any]:
    return {
        "albumartist": albumartist or artist or "Unknown Artist",
        "album": album or "Unknown Album",
        "year": (date or "")[:4],
        "date": date or "",
        "title": title or f"Track {tracknumber:02d}",
        "artist": artist or albumartist or "Unknown Artist",
        "tracknumber": tracknumber,
        "tracktotal": tracktotal,
        "discnumber": discnumber,
        "disctotal": disctotal,
        "disc_prefix": f"{discnumber}-" if disctotal > 1 else "",
        "genre": genre or "",
        "label": label or "",
        "catalognumber": catalognumber or "",
    }
