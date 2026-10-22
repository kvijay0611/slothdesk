from dataclasses import replace
from pathlib import Path

import pytest
from conftest import FakeClient, make_file

from slothdesk.classify import sanitize_part
from slothdesk.engine import run_once
from slothdesk.organizer import name_rule

CATS = ("Finance", "Study", "Medical", "Work", "Identity", "Personal", "Misc")


@pytest.mark.parametrize("name,category", [
    ("Assignment 8 .docx", "Study"),
    ("worksheet 3 code.docx", "Study"),
    ("2026Feb@003827_Degree Mark Sheet_Sem 5.pdf", "Study"),
    ("StudentGradeHistory_22BHI10189.pdf", "Study"),
    ("Student Grade History.pdf", "Study"),
    ("22BHI10189 TRANSCRIPT_0001.pdf", "Study"),
    ("Keerti 12 TC.jpg", "Identity"),
    ("KEERTI COMMUNITY798.jpg", "Identity"),
    ("My Passport scan.png", "Identity"),
    ("recipt for the documentation.pdf", "Finance"),
    ("Invoice-2026-114.pdf", "Finance"),
])
def test_obvious_names_are_routed(name, category):
    d = name_rule(Path(name), CATS)
    assert (d.category if d else None) == category
    if d:
        assert d.source == "rule" and d.confidence == 0.95


@pytest.mark.parametrize("name", [
    "attachment.docx", "TCS_offer.pdf", "community-guidelines.pdf", "notes.pdf",
    "setup.exe", "assignment.zip", "assignment.mp4", "Bill Gates biography.pdf", "bill.txt",
])
def test_ordinary_or_unsuitable_names_are_left_alone(name):
    assert name_rule(Path(name), CATS) is None


def test_rule_needs_the_category_to_exist():
    assert name_rule(Path("Assignment 8.docx"), ("Work", "Misc")) is None


def test_engine_files_by_name_without_calling_the_model(cfg, journal):
    make_file(cfg.root, "Assignment 8 .docx", b"anything")
    make_file(cfg.root, "Degree Mark Sheet.pdf", b"%PDF-1.4")  # scanned: no readable text
    client = FakeClient()
    s = run_once(cfg, client, journal, apply=True)
    assert s.counts() == {"moved": 2} and client.calls == 0
    assert (cfg.sorted_dir / "Study" / "Assignment 8 .docx").exists()
    assert (cfg.sorted_dir / "Study" / "Degree Mark Sheet.pdf").exists()


def test_named_photo_is_filed_even_with_ocr_off(cfg, journal):
    make_file(cfg.root, "community certificate.jpg", b"jpg")
    s = run_once(replace(cfg, ocr=False), FakeClient(), journal, apply=True)
    assert s.counts() == {"moved": 1}
    assert (cfg.sorted_dir / "Identity" / "community certificate.jpg").exists()


def test_other_files_still_go_to_the_model(cfg, journal):
    make_file(cfg.root, "bill.txt")
    client = FakeClient()
    assert run_once(cfg, client, journal, apply=True).counts() == {"moved": 1}
    assert client.calls == 1


@pytest.mark.parametrize("title,expected", [
    ("StudentGradeHistory_22BHI10189.pdf", "StudentGradeHistory_22BHI10189"),
    ("Report.DOCX", "Report"),
    ("Notes v2.1", "Notes-v2.1"),  # a version number is not an extension
    ("archive.zip", "archive"),
])
def test_title_never_ends_in_a_file_extension(title, expected):
    assert sanitize_part(title) == expected
