from __future__ import annotations

import logging
import os
from datetime import datetime

import pytest

from sgc.discovery import discover, natural_key
from sgc.errors import FatalError
from sgc.runlog import RunFolder, get_logger, submission_id


def touch(path, data=b"x"):
    path.write_bytes(data)
    return path


def test_discovery_filters_and_sorts(tmp_path):
    for name in ["10.pdf", "2.docx", "1.PDF", "notes.txt", ".DS_Store", "~$lock.docx",
                 "sgc_report_2026-10-03T141500.pdf", "Sgc_Report_x.PDF", "legacy.doc"]:
        touch(tmp_path / name)
    (tmp_path / "subdir").mkdir()
    touch(tmp_path / "subdir" / "nested.pdf")
    (tmp_path / "sgc_run_2026").mkdir()

    found = discover(tmp_path)
    assert [c.name for c in found] == ["1.PDF", "2.docx", "10.pdf", "legacy.doc", "notes.txt"]
    kinds = {c.name: c.kind for c in found}
    assert kinds["1.PDF"] == "pdf" and kinds["2.docx"] == "docx"
    unsupported = {c.name: c.unsupported_reason for c in found if c.kind is None}
    assert unsupported == {
        "legacy.doc": "unsupported file type (.doc)",
        "notes.txt": "unsupported file type (.txt)",
    }


def test_empty_directory_is_fatal(tmp_path):
    touch(tmp_path / ".DS_Store")
    with pytest.raises(FatalError, match="No submissions"):
        discover(tmp_path)


def test_missing_and_non_directory(tmp_path):
    with pytest.raises(FatalError, match="does not exist"):
        discover(tmp_path / "nope")
    f = touch(tmp_path / "a.pdf")
    with pytest.raises(FatalError, match="Not a directory"):
        discover(f)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_unwritable_directory_is_fatal(tmp_path):
    touch(tmp_path / "a.pdf")
    tmp_path.chmod(0o555)
    try:
        with pytest.raises(FatalError, match="not writable"):
            discover(tmp_path)
    finally:
        tmp_path.chmod(0o755)


def test_natural_key():
    names = ["a10", "a2", "a1"]
    assert sorted(names, key=natural_key) == ["a1", "a2", "a10"]


def test_submission_id():
    assert submission_id(1) == "S001"
    assert submission_id(42) == "S042"


def test_run_folder_creation_and_unique_names(tmp_path):
    when = datetime(2026, 10, 3, 14, 15, 0)
    first = RunFolder(tmp_path, when)
    first.create()
    assert first.run_dir.name == "sgc_run_2026-10-03T141500"
    assert first.report_path.name == "sgc_report_2026-10-03T141500.pdf"
    get_logger().info("hello")
    first.close()
    assert "hello" in (first.run_dir / "run.log").read_text()

    second = RunFolder(tmp_path, when)
    assert second.run_dir.name == "sgc_run_2026-10-03T141500_2"
    assert second.report_path.name == "sgc_report_2026-10-03T141500_2.pdf"


def test_run_folder_does_not_leak_handlers(tmp_path):
    folder = RunFolder(tmp_path)
    folder.create()
    n = len(logging.getLogger("sgc").handlers)
    folder.close()
    assert len(logging.getLogger("sgc").handlers) == n - 1


def test_manifest_and_submission_dir(tmp_path):
    folder = RunFolder(tmp_path)
    folder.create()
    sub = folder.submission_dir("S001")
    assert sub.is_dir()
    folder.write_manifest({"a": 1})
    assert (folder.run_dir / "manifest.json").exists()
    folder.close()
