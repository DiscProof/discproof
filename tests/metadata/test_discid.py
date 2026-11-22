"""MusicBrainz disc ID against the libdiscid / MusicBrainz documentation example."""

from __future__ import annotations

from mockweb import DISC_ID, EXAMPLE_TOC

from perfect_audio_copy.drive.constants import CTRL_DATA
from perfect_audio_copy.drive.toc import Toc, Track
from perfect_audio_copy.metadata.discid import mb_disc_id, mb_submit_url, mb_toc_string


def test_documented_example() -> None:
    assert mb_disc_id(EXAMPLE_TOC) == DISC_ID
    assert mb_toc_string(EXAMPLE_TOC) == "1 6 95462 150 15363 32314 46592 63414 80489"


def test_enhanced_cd_ignores_data_session() -> None:
    """Same audio session plus a data track: same disc ID as the audio-only disc."""
    audio = list(EXAMPLE_TOC.tracks)
    enhanced = Toc(
        (*audio, Track(7, 95312 + 11400, CTRL_DATA, session=2)),
        ((1, 95312), (2, 95312 + 11400 + 20000)),
    )
    assert mb_disc_id(enhanced) == DISC_ID
    assert mb_toc_string(enhanced).startswith("1 6 95462 ")


def test_submit_url() -> None:
    url = mb_submit_url(EXAMPLE_TOC)
    assert url.startswith(
        f"https://musicbrainz.org/cdtoc/attach?id={DISC_ID}&tracks=6&toc=1+6+95462+150"
    )
