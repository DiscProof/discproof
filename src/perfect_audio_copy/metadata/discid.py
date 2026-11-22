"""MusicBrainz disc IDs (the libdiscid algorithm) and TOC strings for fuzzy lookup.

The ID is the SHA-1 of the uppercase-hex TOC: first track (2 digits), last track (2),
then 100 offsets of 8 digits (lead-out first, then each track, zero-padded), in
MusicBrainz's Base64 variant (``+/=`` become ``._-``). Offsets are LBA + 150.
On an Enhanced CD (audio session + data session) the data track is left out and the
lead-out is the end of the audio session, as libdiscid does.
"""

from __future__ import annotations

import base64
import hashlib

from perfect_audio_copy.drive.constants import MSF_OFFSET
from perfect_audio_copy.drive.toc import Toc


def _layout(toc: Toc) -> tuple[int, int, int, list[int]]:
    tracks = list(toc.tracks)
    leadout = toc.leadout
    if toc.is_enhanced:
        while tracks and tracks[-1].is_data:
            tracks.pop()
        leadout = toc.audio_leadout
    return (
        tracks[0].number,
        tracks[-1].number,
        leadout + MSF_OFFSET,
        [t.start + MSF_OFFSET for t in tracks],
    )


def mb_disc_id(toc: Toc) -> str:
    first, last, leadout, offsets = _layout(toc)
    text = f"{first:02X}{last:02X}{leadout:08X}"
    text += "".join(f"{o:08X}" for o in offsets)
    text += "00000000" * (99 - len(offsets))
    digest = hashlib.sha1(text.encode("ascii")).digest()  # SHA-1 is what the algorithm defines
    return base64.b64encode(digest).decode("ascii").translate(str.maketrans("+/=", "._-"))


def mb_toc_string(toc: Toc) -> str:
    """``first last leadout offset...`` as used by ``/ws/2/discid/-?toc=``."""
    first, last, leadout, offsets = _layout(toc)
    return " ".join(str(v) for v in (first, last, leadout, *offsets))


def mb_submit_url(toc: Toc) -> str:
    """Page where the user can attach this disc ID to a release on MusicBrainz."""
    first, last, _, _ = _layout(toc)
    toc_param = mb_toc_string(toc).replace(" ", "+")
    return (
        f"https://musicbrainz.org/cdtoc/attach?id={mb_disc_id(toc)}"
        f"&tracks={last - first + 1}&toc={toc_param}"
    )
