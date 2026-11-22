"""Path templates and sanitizing."""

from __future__ import annotations

import pytest

from perfect_audio_copy.output.naming import (
    DEFAULT_TEMPLATE,
    TemplateError,
    render,
    sanitize,
    track_fields,
)


def fields(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "albumartist": "Uncle Tupelo",
        "album": "Anodyne",
        "date": "1993-10-05",
        "title": "Slate",
        "artist": "Uncle Tupelo",
        "tracknumber": 1,
        "tracktotal": 12,
    }
    base.update(kw)
    return track_fields(**base)  # type: ignore[arg-type]


def test_default_template() -> None:
    n = render(DEFAULT_TEMPLATE, fields())
    assert n.folder == ("Uncle Tupelo", "Anodyne (1993)")
    assert n.stem == "01 - Slate"


def test_multi_disc_prefix() -> None:
    n = render(DEFAULT_TEMPLATE, fields(discnumber=2, disctotal=2, tracknumber=7))
    assert n.stem == "2-07 - Slate"


def test_values_cannot_create_folders_or_illegal_names() -> None:
    n = render(DEFAULT_TEMPLATE, fields(albumartist="AC/DC", title='What? "Now": <yes>|no*'))
    assert n.folder[0] == "AC_DC"
    assert n.stem == "01 - What_ _Now__ _yes__no_"


def test_missing_year_leaves_no_empty_parentheses() -> None:
    assert render(DEFAULT_TEMPLATE, fields(date=None)).folder[1] == "Anodyne"


def test_missing_names_get_placeholders() -> None:
    n = render(DEFAULT_TEMPLATE, fields(albumartist="", artist="", album="", title=""))
    assert n.folder == ("Unknown Artist", "Unknown Album (1993)")
    assert n.stem == "01 - Track 01"


def test_sanitize() -> None:
    assert sanitize("  ..hidden. ") == "hidden"
    assert sanitize("tab\there\x00") == "tabhere"
    assert sanitize("Café") == "Café"  # NFC
    long = "é" * 300
    out = sanitize(long)
    assert len(out.encode()) <= 200 and out == "é" * 100


@pytest.mark.parametrize("bad", ["{nope}", "{title", "", "{tracknumber:zz}"])
def test_bad_templates(bad: str) -> None:
    with pytest.raises(TemplateError):
        render(bad, fields())


def test_custom_template() -> None:
    n = render("{albumartist} - {album}/{tracknumber:02}. {artist} - {title}", fields())
    assert n.folder == ("Uncle Tupelo - Anodyne",) and n.stem == "01. Uncle Tupelo - Slate"


def test_dangling_separator_from_empty_field_is_removed() -> None:
    n = render("{albumartist}/{album}/{tracknumber:02} - {title} - {label}", fields())
    assert n.stem == "01 - Slate"
