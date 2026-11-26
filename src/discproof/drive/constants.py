"""Red Book / MMC constants shared across the drive layer."""

from __future__ import annotations

#: Bytes of CD-DA audio per sector (588 stereo 16-bit samples).
SECTOR_SIZE = 2352
#: Stereo samples (frames of L+R) per sector.
SAMPLES_PER_SECTOR = 588
#: Bytes per stereo sample.
BYTES_PER_SAMPLE = 4
#: C2 error pointer bytes per sector (one bit per audio byte).
C2_SIZE = 294
#: C2 pointers + block error byte (+1 pad) when error field = 10b.
C2_BLOCK_SIZE = 296
#: Formatted Q sub-channel bytes per sector returned by READ CD.
SUBQ_SIZE = 16
#: Raw interleaved P-W sub-channel bytes per sector.
SUB_RAW_SIZE = 96

FRAMES_PER_SECOND = 75  # "frames" == sectors in Red Book time
#: LBA 0 is MSF 00:02:00 — the first 150 sectors are the track-1 pre-gap.
MSF_OFFSET = 150
#: Sectors between the end of an audio session and the first track of the next
#: session on a CD-Extra/Enhanced CD (lead-out 6750 + lead-in 4500 + pre-gap 150).
SESSION_GAP = 11400

#: 1x CD speed in kB/s as used by SET CD SPEED (1x = 75 * 2352 bytes/s).
KBPS_1X = 176.4
#: "Maximum speed" value for SET CD SPEED.
SPEED_MAX = 0xFFFF

# TOC control-field bits (the CONTROL nibble of the Q sub-channel).
CTRL_PREEMPHASIS = 0x1
CTRL_COPY_PERMITTED = 0x2
CTRL_DATA = 0x4
CTRL_FOUR_CHANNEL = 0x8
