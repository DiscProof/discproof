"""Drive setup from the command line (the UI wizard comes in Phase 6).

discproof-drivecheck                    # features + AccurateRip, first drive
discproof-drivecheck /dev/sr0 --no-features
discproof-drivecheck --save             # store results in drives.toml

Needs an audio CD in the drive; for offset detection it must be in AccurateRip.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence

from discproof.config.drives import DriveStore, OffsetSource
from discproof.config.paths import migrate_legacy_dirs
from discproof.drive.device import Drive
from discproof.drive.enumerate import list_drives
from discproof.drive.sense import DriveError
from discproof.verify.accuraterip import ar_disc_id
from discproof.verify.features import FeatureDetector
from discproof.verify.offsets import find_drive_offset, offset_candidates
from discproof.verify.online import AccurateRipClient, OnlineError, drive_offset_list
from discproof.verify.verify import detect_offset_from_disc, offset_test_tracks


def _yn(v: bool | None) -> str:
    return "unknown" if v is None else ("yes" if v else "no")


def run(path: str, *, features: bool, accuraterip: bool, save: bool) -> int:  # noqa: PLR0915
    store = DriveStore()
    with Drive.open(path) as drive:
        inq = drive.inquiry()
        print(f"== {path}: {inq.vendor} {inq.product} (firmware {inq.revision})")
        if not drive.wait_ready(30):
            print("no disc in drive")
            return 1
        toc = drive.read_toc()
        if not toc.audio_tracks:
            print("disc has no audio tracks")
            return 1
        rec = store.get_or_create(inq.vendor, inq.product, inq.revision)
        print(f"   stored profile: offset {rec.profile.read_offset:+d} ({rec.offset_source.value})")

        if features:
            caps = drive.capabilities()
            max_x = caps.max_read_speed_x if caps and caps.max_read_speed_x else 48.0
            print(f"\n-- feature detection (max speed {max_x:.0f}x)")
            report = FeatureDetector(
                drive, toc, max_speed_x=max_x, on_step=lambda s: print(f"   {s}...")
            ).run()
            c = report.cache
            print(
                f"   caches audio:      {_yn(c.caches_audio)}"
                + ("" if c.consistent else " (inconsistent)")
            )
            if c.caches_audio:
                print(f"   cache window:      {c.cache_sectors} sectors (estimated, with margin)")
                print(f"   FUA flush works:   {_yn(c.fua_works)}")
                print(f"   seek-away works:   {_yn(c.seek_away_works)}")
            print(f"   accurate stream:   {_yn(report.accurate_stream)}")
            c2 = report.c2
            print(
                f"   C2 pointers:       supported {_yn(c2.supported)}, "
                f"errors seen {c2.errors_seen}, "
                f"missed {c2.missed}, false flags {c2.false_flags} -> trusted {_yn(c2.trusted)}"
            )
            print(f"   overread lead-in:  {_yn(report.overread_leadin)}")
            print(f"   overread lead-out: {_yn(report.overread_leadout)}")
            for n in report.notes:
                print(f"   note: {n}")
            rec.profile = report.apply(rec.profile)
            rec.features_tested = dt.date.today().isoformat()
            rec.notes = list(report.notes)

        if accuraterip:
            print("\n-- AccurateRip")
            disc = ar_disc_id(toc)
            print(f"   disc: {disc.filename}")
            listed = None
            try:
                entries = drive_offset_list()
                listed = find_drive_offset(entries, inq.vendor, inq.product)
                if listed:
                    print(
                        f"   drive list: {listed.vendor} - {listed.model}: {listed.offset:+d} "
                        f"({listed.submissions} submissions, {listed.agreement}% agree)"
                    )
                    if rec.offset_source in (OffsetSource.DEFAULT, OffsetSource.DRIVE_LIST):
                        assert listed.offset is not None
                        rec.set_offset(listed.offset, OffsetSource.DRIVE_LIST)
                else:
                    print("   drive list: this drive is not listed")
                responses = AccurateRipClient().lookup(disc)
            except OnlineError as exc:
                print(f"   offline: {exc}")
                responses = None
            if responses is None:
                print("   disc not in the database: cannot detect the offset with it")
            else:
                print(
                    f"   {len(responses)} database responses; "
                    f"reading tracks {offset_test_tracks(toc)}..."
                )
                matches = detect_offset_from_disc(drive, toc, responses, rec.profile)
                for m in matches[:3]:
                    print(
                        f"   offset {m.offset:+d}: tracks {list(m.tracks)}, "
                        f"confidence {m.confidence}"
                    )
                found = offset_candidates(matches)
                if not found:
                    print("   no offset supported by enough tracks on this disc")
                else:
                    adopted = rec.observe_offsets(disc.filename, found)
                    listed_s = ""
                    if listed is not None and listed.offset in found:
                        listed_s = " (includes the drive-list value)"
                    print(f"   this disc supports offset(s) {found}{listed_s}")
                    if len(found) > 1:
                        print("   ambiguous: the database has pressings that differ by an offset")
                    confirmed = rec.confirmed_offset()
                    print(
                        f"   discs observed: {len(rec.offset_observations)}; confirmed offset: "
                        + (f"{confirmed:+d}" if confirmed is not None else "none yet")
                        + ("; adopted" if adopted else "")
                    )

        print(f"\n   resulting profile: {rec.profile}")
        if save:
            store.save()
            print(f"   saved to {store.path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discproof-drivecheck", description=__doc__)
    parser.add_argument("device", nargs="?", help="device node, e.g. /dev/sr0")
    parser.add_argument("--no-features", action="store_true", help="skip feature detection")
    parser.add_argument("--no-accuraterip", action="store_true", help="skip the online part")
    parser.add_argument("--save", action="store_true", help="store results in drives.toml")
    args = parser.parse_args(argv)
    migrate_legacy_dirs()
    target = args.device or next((d.path for d in list_drives()), None)
    if target is None:
        print("no optical drive found", file=sys.stderr)
        return 1
    try:
        return run(
            target,
            features=not args.no_features,
            accuraterip=not args.no_accuraterip,
            save=args.save,
        )
    except DriveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
