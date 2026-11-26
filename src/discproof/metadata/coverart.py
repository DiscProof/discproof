"""Cover art: finding the front image (Cover Art Archive, then Discogs) and preparing
the two JPEGs the output needs: ``cover.jpg`` (at most 1500 px) and the embedded
FLAC picture (at most 1000 px)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError

from discproof.metadata.web import Web

CAA = "https://coverartarchive.org"
COVER_MAX_PX = 1500
EMBED_MAX_PX = 1000
JPEG_QUALITY = 90
#: Refuse absurd images (decompression bombs) before decoding.
MAX_PIXELS = 100_000_000


class CoverError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Cover:
    cover_jpeg: bytes  # for cover.jpg
    embed_jpeg: bytes  # for the FLAC PICTURE block (type 3, front cover)
    width: int  # of cover_jpeg
    height: int
    embed_width: int
    embed_height: int
    source_url: str | None = None


def _jpeg(img: Image.Image, max_px: int) -> tuple[bytes, int, int]:
    copy = img.copy()
    copy.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)  # never upscales
    buf = io.BytesIO()
    copy.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=False)
    return buf.getvalue(), copy.width, copy.height


def prepare_cover(data: bytes, source_url: str | None = None) -> Cover:
    """Decode any common image format and produce both JPEGs."""
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as raw:
            raw.load()
            img: Image.Image = raw
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                img = Image.new("RGB", rgba.size, (255, 255, 255))
                img.paste(rgba, mask=rgba.getchannel("A"))
            elif img.mode != "RGB":
                img = img.convert("RGB")
            # Encode while the source image is still open.
            cover, w, h = _jpeg(img, COVER_MAX_PX)
            embed, ew, eh = _jpeg(img, EMBED_MAX_PX)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise CoverError(f"not a usable image: {exc}") from exc
    return Cover(cover, embed, w, h, ew, eh, source_url)


def caa_front_url(listing: dict[str, Any]) -> str | None:
    """Best URL for the front image in a CAA listing: the 1200 px thumbnail, else the
    original."""
    for img in listing.get("images") or []:
        if img.get("front") or "Front" in (img.get("types") or []):
            thumbs = img.get("thumbnails") or {}
            url = thumbs.get("1200") or img.get("image")
            return str(url) if url else None
    return None


class CoverArtArchive:
    def __init__(self, web: Web) -> None:
        self.web = web

    def front_url(self, release_id: str | None, release_group_id: str | None) -> str | None:
        """The release's front cover, falling back to the release group's."""
        for kind, mbid in (("release", release_id), ("release-group", release_group_id)):
            if not mbid:
                continue
            listing = self.web.get_json(f"{CAA}/{kind}/{mbid}")
            if listing:
                url = caa_front_url(listing)
                if url:
                    return url
        return None
