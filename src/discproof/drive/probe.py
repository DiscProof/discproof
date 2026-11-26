"""Hardware probe: exercises the drive layer against a real drive and disc.

discproof-probe                 # list drives, probe the first one
discproof-probe /dev/sr1 --read-test
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence

from discproof.drive.device import Drive
from discproof.drive.enumerate import list_drives
from discproof.drive.msf import frames_to_timestamp
from discproof.drive.sense import DriveError
from discproof.drive.subchannel import QPosition, parse_q


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _probe(path: str, read_test: bool, sectors: int) -> int:  # noqa: PLR0915
    with Drive.open(path) as drive:
        inq = drive.inquiry()
        print(f"\n== {path}: {inq.name} (firmware {inq.revision})")
        print(f"   write-class commands permitted: {_yn(drive.transport.writable)}")
        print(f"   max batch: {drive.effective_batch()} sectors")

        caps = drive.capabilities()
        if caps:
            print(
                f"   mode page 2A: CD-DA {_yn(caps.cdda_supported)}, accurate stream "
                f"{_yn(caps.accurate_stream)}, C2 pointers {_yn(caps.c2_pointers)}, "
                f"ISRC {_yn(caps.isrc)}, UPC {_yn(caps.upc)}, max {caps.max_read_speed_x:.1f}x"
            )
        else:
            print("   mode page 2A: not available")
        feat = drive.cd_read_feature()
        if feat:
            print(
                f"   CD Read feature: C2 flags {_yn(feat.c2_flags)}, CD-Text "
                f"{_yn(feat.cd_text)}, medium {feat.medium}"
            )

        if not drive.wait_ready(timeout_s=30):
            print("   no disc in drive (or it did not become ready)")
            return 0

        info = drive.read_disc_info()
        toc = info.toc
        print(
            f"\n   TOC ({toc.source}): tracks {toc.first}-{toc.last}, sessions {toc.sessions}, "
            f"lead-out {toc.leadout}, audio lead-out {toc.audio_leadout}"
            + (", Enhanced CD" if toc.is_enhanced else "")
        )
        print(f"   MCN/UPC: {info.mcn or '-'}")
        print("   trk  type   start     end   length     pre isrc")
        for t in toc.tracks:
            print(
                f"   {t.number:3d}  {'audio' if t.is_audio else 'data '} {t.start:7d} "
                f"{toc.end(t.number) - 1:7d} {frames_to_timestamp(toc.length(t.number)):>8} "
                f"{'  yes' if t.preemphasis else '    -'} {info.isrcs.get(t.number, '-')}"
            )
        ct = info.cdtext
        if ct:
            print(f"\n   CD-Text ({ct.charset}, {ct.bad_packs} bad packs):")
            print(f"     album: {ct.album.performer or '?'} - {ct.album.title or '?'}")
            for n in sorted(ct.tracks):
                e = ct.tracks[n]
                print(f"     {n:2d}: {e.performer or ''} - {e.title or ''}")
        else:
            print("\n   CD-Text: none")

        if read_test and toc.audio_tracks:
            use_c2 = bool((caps and caps.c2_pointers) or (feat and feat.c2_flags))
            first = toc.audio_tracks[0]
            n = min(sectors, toc.length(first.number))
            print(f"\n   read test: {n} sectors of track {first.number}, C2={_yn(use_c2)}, Q=yes")
            passes = []
            for attempt in (1, 2):
                t0 = time.monotonic()
                chunks = list(drive.iter_read(first.start, n, c2=use_c2, subq=True))
                dt = time.monotonic() - t0
                audio = b"".join(c.audio for c in chunks)
                c2_bits = sum(c.c2_error_bits for c in chunks)
                speed = n / 75 / dt if dt else 0.0
                print(f"     pass {attempt}: {dt:.2f}s ({speed:.1f}x), C2 error bits: {c2_bits}")
                passes.append(audio)
                if attempt == 1:
                    qs = [q for c in chunks for q in (c.subq or ())]
                    pos = [p for p in map(parse_q, qs[:5]) if isinstance(p, QPosition)]
                    if pos:
                        p = pos[0]
                        print(
                            f"     Q at LBA {first.start}: track {p.track} index {p.index} "
                            f"abs LBA {p.absolute_lba} crc {p.crc_ok}"
                        )
            same = passes[0] == passes[1]
            print(f"     two passes identical: {_yn(same)} (cache may make this trivially true)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discproof-probe", description=__doc__)
    parser.add_argument("device", nargs="?", help="device node, e.g. /dev/sr0")
    parser.add_argument("--read-test", action="store_true", help="also read audio sectors")
    parser.add_argument("--sectors", type=int, default=750, help="sectors for --read-test")
    args = parser.parse_args(argv)

    drives = list_drives()
    print("Optical drives:")
    if not drives:
        print("  none found")
    for d in drives:
        access = "rw" if d.writable else ("r" if d.readable else "NO ACCESS")
        print(f"  {d.path}: {d.name} [{d.revision}] access={access}")
    target = args.device or (drives[0].path if drives else None)
    if target is None:
        return 1
    try:
        return _probe(target, args.read_test, args.sectors)
    except DriveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
