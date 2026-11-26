"""Cover preparation (Pillow) and Cover Art Archive listings."""

from __future__ import annotations

import io
import json

import pytest
from mockweb import FIXTURES, RELEASE, png
from PIL import Image

from discproof.metadata.coverart import CoverError, caa_front_url, prepare_cover


def size(jpeg: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(jpeg)) as im:
        assert im.format == "JPEG" and im.mode == "RGB"
        return im.size


def test_large_image_is_capped_for_file_and_embed() -> None:
    c = prepare_cover(png(3000, 2000))
    assert size(c.cover_jpeg) == (1500, 1000) == (c.width, c.height)
    assert size(c.embed_jpeg) == (1000, 667) == (c.embed_width, c.embed_height)


def test_small_image_is_not_upscaled() -> None:
    c = prepare_cover(png(600, 600))
    assert size(c.cover_jpeg) == (600, 600) and size(c.embed_jpeg) == (600, 600)


@pytest.mark.parametrize("mode", ["RGBA", "L", "CMYK", "P"])
def test_other_modes_become_rgb_jpeg(mode: str) -> None:
    buf = io.BytesIO()
    fmt = "JPEG" if mode == "CMYK" else "PNG"
    Image.new(mode, (50, 40)).save(buf, fmt)
    c = prepare_cover(buf.getvalue())
    assert size(c.cover_jpeg) == (50, 40)


def test_garbage_is_rejected() -> None:
    with pytest.raises(CoverError):
        prepare_cover(b"<html>not an image</html>")


def test_caa_front_prefers_1200_thumbnail() -> None:
    listing = json.loads((FIXTURES / "caa_release_d3dc4be9.json").read_text())
    url = caa_front_url(listing)
    assert url is not None and url.startswith(f"https://coverartarchive.org/release/{RELEASE}/")
    assert url.endswith("-1200.jpg")
    assert caa_front_url({"images": [{"front": False, "image": "x"}]}) is None
