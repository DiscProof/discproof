"""CUE sheets and logs against golden files (regenerate with UPDATE_GOLDEN=1)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from reports import clean_report, offline_report, troubled_report

from discproof.output.cue import render_cue
from discproof.output.log import render_log, verify_log
from discproof.output.report import AlbumReport

GOLDEN = Path(__file__).parent / "golden"
CASES = {"clean": clean_report, "troubled": troubled_report, "offline": offline_report}


def check(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", CASES)
def test_cue_golden(case: str) -> None:
    check(f"{case}.cue", render_cue(CASES[case]()))


@pytest.mark.parametrize("case", CASES)
def test_log_golden(case: str) -> None:
    check(f"{case}.log", render_log(CASES[case]()))


def test_log_checksum_detects_edits() -> None:
    text = render_log(clean_report())
    assert verify_log(text)
    assert not verify_log(text.replace("Copy OK", "Copy 0K", 1))
    assert not verify_log(text.replace("==== Log checksum ", "==== Log checksum 0", 1))
    assert not verify_log("no checksum here\n")


def test_report_ok_flags() -> None:
    assert clean_report().ok
    assert not troubled_report().ok
    report: AlbumReport = offline_report()
    assert not report.ok  # cancelled


def test_gap_level_wording() -> None:
    from dataclasses import replace

    from discproof.output.log import gap_level

    assert gap_level(0) == "silent"
    assert gap_level(23) == "near-silent, peak 0.1 %"
    assert gap_level(11200) == "contains audio, peak 34.2 %"
    report = clean_report()
    t2 = replace(report.tracks[1], pregap_peak=23)
    text = render_log(replace(report, tracks=(report.tracks[0], t2, report.tracks[2])))
    assert "Pre-gap length  0:02.00 (near-silent, peak 0.1 %)" in text
    assert verify_log(text)
