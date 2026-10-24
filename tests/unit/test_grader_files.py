"""Grader-only files: hidden from the agent, overlaid at grading time."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from meridian.runtime.pytest_runner import _archive
from meridian.suites.loader import load_suite

REAL_SUITE = Path(__file__).resolve().parents[2] / "suites" / "checkout-agent"

pytestmark = pytest.mark.unit


def _names(payload: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        out: dict[str, bytes] = {}
        for member in tar.getmembers():
            handle = tar.extractfile(member)
            assert handle is not None
            out[member.name] = handle.read()
        return out


def test_grader_files_are_added_to_the_state(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("agent output")
    files = _names(
        _archive(tmp_path, {"/work/tests/test_outputs.py": b"assert True"}, workdir="/work")
    )
    assert files == {
        "work/out.txt": b"agent output",
        "work/tests/test_outputs.py": b"assert True",
    }


def test_grader_files_win_over_agent_written_files(tmp_path: Path) -> None:
    """An agent that rewrites the test file must not change what is graded."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_outputs.py").write_text("assert True  # tampered")
    files = _names(
        _archive(tmp_path, {"/work/tests/test_outputs.py": b"assert 1 == 2"}, workdir="/work")
    )
    assert files["work/tests/test_outputs.py"] == b"assert 1 == 2"


def test_archive_without_grader_files_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    assert _archive(tmp_path, workdir="/work") == _archive(tmp_path, {}, workdir="/work")


def test_existing_tasks_keep_their_definition_hash() -> None:
    """A task without grader_files hashes exactly as it did before the field existed."""
    from meridian.hashing import content_hash

    suite = load_suite(REAL_SUITE)
    for task in suite.tasks:
        legacy = task.model_dump(mode="json")
        legacy.pop("grader_files")
        assert task.grader_files == ()
        assert task.definition_hash() == content_hash(legacy)


def test_grader_files_change_the_definition_hash() -> None:
    from meridian.models.task import InputFile

    task = load_suite(REAL_SUITE).tasks[0]
    with_files = task.model_copy(
        update={"grader_files": (InputFile(src="seed/x", dest="/work/tests/test_x.py"),)}
    )
    assert with_files.definition_hash() != task.definition_hash()


def test_grader_files_are_validated_like_inputs(tmp_path: Path) -> None:
    import shutil

    from meridian.suites.validate import SuiteValidationError

    target = tmp_path / "suite"
    shutil.copytree(REAL_SUITE, target)
    task_file = target / "tasks" / "happy-path.yaml"
    text = task_file.read_text()
    task_file.write_text(
        text.replace(
            "limits:",
            "grader_files:\n  - src: ../../outside.py\n    dest: /work/tests/test_x.py\nlimits:",
            1,
        )
    )
    with pytest.raises(SuiteValidationError) as excinfo:
        load_suite(target)
    assert any(i.code == "input_file_outside_suite" for i in excinfo.value.issues)


def test_grader_files_can_land_in_the_tests_directory(tmp_path: Path) -> None:
    """Benchmark ecosystems expect hidden tests at /tests, outside the workdir."""
    (tmp_path / "answer.txt").write_text("42")
    files = _names(_archive(tmp_path, {"/tests/test.sh": b"echo 1"}, workdir="/app"))
    assert files == {"app/answer.txt": b"42", "tests/test.sh": b"echo 1"}


def test_a_grader_file_outside_the_allowed_directories_is_rejected(tmp_path: Path) -> None:
    import shutil

    from meridian.suites.validate import SuiteValidationError

    target = tmp_path / "suite"
    shutil.copytree(REAL_SUITE, target)
    (target / "seed" / "evil.sh").write_text("echo")
    task_file = target / "tasks" / "happy-path.yaml"
    task_file.write_text(
        task_file.read_text().replace(
            "limits:",
            "grader_files:\n  - src: seed/evil.sh\n    dest: /etc/evil.sh\nlimits:",
            1,
        )
    )
    with pytest.raises(SuiteValidationError):
        load_suite(target)
