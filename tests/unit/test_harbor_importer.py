"""Harbor task import: what converts, what is refused, and why."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from meridian.cli import app
from meridian.importers import harbor
from meridian.snapshots.registry import UNBUILT_DIGEST
from meridian.suites.loader import load_suite

pytestmark = pytest.mark.unit

TASK_TOML = """
version = "1.0"

[task]
name = "acme/word-count"

[metadata]
difficulty = "easy"
category = "file-operations"
tags = ["Text", "wc"]

[agent]
timeout_sec = 240.0

[verifier]
timeout_sec = 60.0

[environment]
cpus = 2
memory_mb = 1024
"""

DOCKERFILE = "FROM ubuntu:24.04\nWORKDIR /app\nCOPY words.txt /app/words.txt\n"
TEST_SH = (
    "#!/bin/bash\nmkdir -p /logs/verifier\n"
    'if [ "$(cat /app/count.txt)" = "3" ]; then echo 1 > /logs/verifier/reward.txt; '
    "else echo 0 > /logs/verifier/reward.txt; fi\n"
)


def make_task(
    root: Path,
    name: str = "word-count",
    *,
    toml: str = TASK_TOML,
    dockerfile: str | None = DOCKERFILE,
    test_sh: str | None = TEST_SH,
    extra: dict[str, str] | None = None,
) -> Path:
    task = root / name
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "task.toml").write_text(toml)
    (task / "instruction.md").write_text("Count the words in /app/words.txt into /app/count.txt.")
    if dockerfile is not None:
        (task / "environment" / "Dockerfile").write_text(dockerfile)
    (task / "environment" / "words.txt").write_text("one two three")
    if test_sh is not None:
        (task / "tests" / "test.sh").write_text(test_sh)
    for relative, content in (extra or {}).items():
        path = task / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return task


def codes(task: harbor.HarborTask, severity: str = harbor.UNSUPPORTED) -> set[str]:
    return {i.code for i in task.issues if i.severity == severity}


# -- parsing -----------------------------------------------------------------------


def test_a_plain_task_is_supported(tmp_path: Path) -> None:
    task = harbor.load_harbor_task(make_task(tmp_path))
    assert task.supported and not task.issues
    assert task.name == "acme/word-count"
    assert task.workdir == "/app"
    assert task.agent_timeout_seconds == 240
    assert task.cpus == "2" and task.memory_mb == 1024
    assert set(task.tags) == {"text", "wc", "easy", "file-operations"}


def test_workdir_comes_from_the_last_workdir_in_the_dockerfile(tmp_path: Path) -> None:
    root = make_task(tmp_path, dockerfile="FROM ubuntu\nWORKDIR /a\nWORKDIR /srv/work\n")
    assert harbor.load_harbor_task(root).workdir == "/srv/work"


def test_timeout_is_clamped_into_the_range_meridian_accepts(tmp_path: Path) -> None:
    root = make_task(tmp_path, toml=TASK_TOML.replace("240.0", "999999"))
    assert harbor.load_harbor_task(root).agent_timeout_seconds == harbor.MAX_TIMEOUT_SECONDS


def test_a_directory_that_is_not_a_task_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(harbor.HarborImportError, match=r"task\.toml"):
        harbor.load_harbor_task(tmp_path)
    (tmp_path / "task.toml").write_text(TASK_TOML)
    with pytest.raises(harbor.HarborImportError, match=r"instruction\.md"):
        harbor.load_harbor_task(tmp_path)


def test_invalid_toml_is_an_error(tmp_path: Path) -> None:
    root = make_task(tmp_path, toml="this is = = not toml")
    with pytest.raises(harbor.HarborImportError, match="TOML"):
        harbor.load_harbor_task(root)


# -- refusals: each unsupported feature gets a specific reason ----------------------


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        ("memory_mb = 1024\ngpus = 1", "accelerator"),
        ('memory_mb = 1024\nmcp_servers = [{name = "x", url = "http://x"}]', "mcp_servers"),
        ('memory_mb = 1024\nos = "windows"', "not_linux"),
        ('memory_mb = 1024\nhealthcheck = {command = "true"}', "healthcheck"),
    ],
)
def test_unsupported_environment_features_are_named(
    tmp_path: Path, replacement: str, code: str
) -> None:
    root = make_task(tmp_path, toml=TASK_TOML.replace("memory_mb = 1024", replacement))
    task = harbor.load_harbor_task(root)
    assert not task.supported
    assert code in codes(task)


def test_multi_step_tasks_are_refused(tmp_path: Path) -> None:
    root = make_task(tmp_path, extra={"steps/one/instruction.md": "step"})
    assert "multi_step" in codes(harbor.load_harbor_task(root))


def test_compose_sidecars_are_refused(tmp_path: Path) -> None:
    root = make_task(tmp_path, extra={"environment/docker-compose.yaml": "services: {}"})
    assert "docker_compose" in codes(harbor.load_harbor_task(root))


def test_a_task_with_no_environment_is_refused(tmp_path: Path) -> None:
    root = make_task(tmp_path, dockerfile=None)
    assert "no_environment" in codes(harbor.load_harbor_task(root))


def test_a_prebuilt_docker_image_counts_as_an_environment(tmp_path: Path) -> None:
    toml = TASK_TOML.replace("memory_mb = 1024", 'memory_mb = 1024\ndocker_image = "ubuntu:24.04"')
    task = harbor.load_harbor_task(make_task(tmp_path, toml=toml, dockerfile=None))
    assert "no_environment" not in codes(task)
    assert harbor.overlay_dockerfile(task).startswith("FROM ubuntu:24.04\n")


def test_a_task_without_a_verifier_script_is_refused(tmp_path: Path) -> None:
    assert "no_test_script" in codes(harbor.load_harbor_task(make_task(tmp_path, test_sh=None)))


@pytest.mark.parametrize(
    "line",
    [
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "pip install pytest",
        "uvx -p 3.13 pytest /tests/test_outputs.py",
        "apt-get install -y jq",
        "git clone https://example.com/x.git",
    ],
)
def test_a_verifier_that_needs_the_network_is_refused(tmp_path: Path, line: str) -> None:
    """The grading container has no network, so installing at verify time cannot work."""
    root = make_task(tmp_path, test_sh=f"#!/bin/bash\n{line}\n{TEST_SH}")
    task = harbor.load_harbor_task(root)
    assert "verifier_needs_network" in codes(task)


def test_a_verifier_that_only_mentions_the_network_in_prose_is_fine(tmp_path: Path) -> None:
    root = make_task(tmp_path, test_sh=TEST_SH + "# no curl needed here\n")
    assert "verifier_needs_network" not in codes(harbor.load_harbor_task(root))


def test_non_apt_bases_are_refused(tmp_path: Path) -> None:
    root = make_task(tmp_path, dockerfile="FROM alpine\nRUN apk add curl\nWORKDIR /app\n")
    assert "non_debian_base" in codes(harbor.load_harbor_task(root))


def test_allow_internet_is_a_warning_not_a_refusal(tmp_path: Path) -> None:
    toml = TASK_TOML.replace("memory_mb = 1024", "memory_mb = 1024\nallow_internet = true")
    task = harbor.load_harbor_task(make_task(tmp_path, toml=toml))
    assert task.supported
    assert "internet" in codes(task, harbor.WARNING)


# -- slugs -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("acme/Word Count!", "word-count"),
        ("A", "task-a"),
        ("x" * 80, "x" * 48),
        ("---", "task"),
    ],
)
def test_slugify_produces_valid_slugs(name: str, expected: str) -> None:
    slug = harbor.slugify(name)
    assert slug == expected
    import re

    assert re.fullmatch(r"[a-z0-9-]{3,48}", slug)


def test_slugify_keeps_slugs_unique() -> None:
    taken: set[str] = set()
    slugs = [harbor.slugify("same", taken) for _ in range(3)]
    assert slugs == ["same", "same-2", "same-3"]
    long = harbor.slugify("y" * 60, set(slugs))
    assert len(long) <= 48


# -- writing a suite that Meridian accepts -----------------------------------------


def test_converted_suite_loads_and_has_the_expected_shape(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    task = harbor.load_harbor_task(make_task(source))
    suite_dir = tmp_path / "suite"
    converted, skipped = harbor.write_suite([task], suite_dir, suite_slug="demo-suite")
    assert [c.slug for c in converted] == ["word-count"] and not skipped

    suite = load_suite(suite_dir)  # validates containment, existence and schema
    imported = suite.task("word-count")
    assert imported.environment.workdir == "/app"
    assert imported.environment.snapshot == UNBUILT_DIGEST
    assert imported.limits.timeout_seconds == 240
    assert imported.outcome_assertions[0].kind == "verifier_script"
    assert [(f.src, f.dest) for f in imported.grader_files] == [
        ("grader/word-count/test.sh", "/tests/test.sh")
    ]
    assert (suite_dir / "grader/word-count/test.sh").read_text() == TEST_SH
    assert "Count the words" in (suite_dir / "prompts/word-count.txt").read_text()


def test_the_agent_never_sees_the_verifier(tmp_path: Path) -> None:
    """Tests ship as grader files only; they are not copied into the image context."""
    source = tmp_path / "src"
    source.mkdir()
    suite_dir = tmp_path / "suite"
    harbor.write_suite([harbor.load_harbor_task(make_task(source))], suite_dir, suite_slug="demo")
    image = suite_dir / "images" / "word-count"
    assert not list(image.rglob("test.sh"))
    document = yaml.safe_load((suite_dir / "tasks/word-count.yaml").read_text())
    assert all(f["dest"].startswith("/tests/") for f in document["grader_files"])
    assert document["input"] == {"prompt_file": "prompts/word-count.txt"}


def test_the_image_context_has_the_overlay_and_the_tasks_own_files(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    suite_dir = tmp_path / "suite"
    harbor.write_suite([harbor.load_harbor_task(make_task(source))], suite_dir, suite_slug="demo")
    image = suite_dir / "images" / "word-count"
    dockerfile = (image / "Dockerfile").read_text()
    assert dockerfile.startswith(DOCKERFILE)
    assert "USER 10001:10001" in dockerfile and "ENTRYPOINT" in dockerfile
    assert "WORKDIR /app" in dockerfile
    assert (image / "words.txt").read_text() == "one two three"
    assert (image / "meridian-bootstrap.sh").read_text() == harbor.BOOTSTRAP_SH


def test_unsupported_tasks_are_reported_and_not_written(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    good = harbor.load_harbor_task(make_task(source, "good"))
    bad = harbor.load_harbor_task(make_task(source, "bad", test_sh=None))
    suite_dir = tmp_path / "suite"
    converted, skipped = harbor.write_suite([good, bad], suite_dir, suite_slug="demo")
    assert len(converted) == 1 and len(skipped) == 1
    assert skipped[0].issues[0].code == "no_test_script"
    assert not (suite_dir / "tasks" / "bad.yaml").exists()


def test_nothing_is_written_when_nothing_converts(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    bad = harbor.load_harbor_task(make_task(source, test_sh=None))
    suite_dir = tmp_path / "suite"
    harbor.write_suite([bad], suite_dir, suite_slug="demo")
    assert not (suite_dir / "suite.yaml").exists()


def test_importing_twice_does_not_overwrite_an_existing_slug(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    task = harbor.load_harbor_task(make_task(source))
    suite_dir = tmp_path / "suite"
    harbor.write_suite([task], suite_dir, suite_slug="demo")
    converted, _ = harbor.write_suite([task], suite_dir, suite_slug="demo")
    assert [c.slug for c in converted] == ["word-count-2"]
    assert len(load_suite(suite_dir).tasks) == 2


def test_find_task_dirs_handles_a_single_task_and_a_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    one = make_task(dataset, "one")
    make_task(dataset, "two")
    assert harbor.find_task_dirs(one) == [one]
    assert [p.name for p in harbor.find_task_dirs(dataset)] == ["one", "two"]
    with pytest.raises(harbor.HarborImportError):
        harbor.find_task_dirs(tmp_path / "empty")


# -- the CLI -----------------------------------------------------------------------


def test_cli_reports_converted_and_skipped(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    make_task(dataset, "good")
    make_task(dataset, "bad", test_sh=None)
    result = CliRunner().invoke(app, ["import-harbor", str(dataset), "--out", str(tmp_path / "s")])
    assert result.exit_code == 0, result.output
    assert "1 converted, 1 skipped" in result.output
    assert "no_test_script" in result.output


def test_cli_exits_nonzero_when_nothing_converts(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    make_task(dataset, "bad", test_sh=None)
    result = CliRunner().invoke(app, ["import-harbor", str(dataset), "--out", str(tmp_path / "s")])
    assert result.exit_code == 2


def test_a_commented_out_install_does_not_count(tmp_path: Path) -> None:
    root = make_task(tmp_path, test_sh="#!/bin/bash\n  # pip install pytest\n" + TEST_SH)
    assert "verifier_needs_network" not in codes(harbor.load_harbor_task(root))


def test_multi_step_tasks_without_a_root_instruction_are_refused_not_fatal(tmp_path: Path) -> None:
    root = tmp_path / "multi"
    (root / "steps" / "one").mkdir(parents=True)
    (root / "task.toml").write_text(TASK_TOML)
    task = harbor.load_harbor_task(root)
    assert "multi_step" in codes(task) and task.instruction == ""


def test_one_unreadable_task_does_not_abort_the_batch(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    make_task(dataset, "good")
    broken = dataset / "broken"
    broken.mkdir()
    (broken / "task.toml").write_text("= not toml =")
    tasks, unreadable = harbor.load_all(harbor.find_task_dirs(dataset))
    assert [t.root.name for t in tasks] == ["good"]
    assert unreadable[0].issues[0].code == "unreadable"
    result = CliRunner().invoke(app, ["import-harbor", str(dataset), "--out", str(tmp_path / "s")])
    assert result.exit_code == 0 and "1 converted, 1 skipped" in result.output


HARBOR_UV_VERIFIER = """#!/bin/bash

apt-get update
apt-get install -y curl

curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh

source $HOME/.local/bin/env

uvx \\
  --with pytest==8.4.1 \\
  --with pytest-json-ctrf==0.3.5 \\
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_state.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
"""


def test_the_standard_uv_preamble_is_rewritten_when_asked(tmp_path: Path) -> None:
    root = make_task(tmp_path, test_sh=HARBOR_UV_VERIFIER)
    assert "verifier_needs_network" in codes(harbor.load_harbor_task(root))  # off by default

    task = harbor.load_harbor_task(root, rewrite_boilerplate=True)
    assert task.supported
    assert "rewrote_verifier_boilerplate" in codes(task, harbor.WARNING)
    script = task.test_files["test.sh"].decode()
    assert "python3 -m pytest /tests/test_state.py -rA" in script
    for gone in ("apt-get", "curl", "uvx", "ctrf", ".local/bin/env"):
        assert gone not in script
    # the reward logic is untouched
    assert "echo 1 > /logs/verifier/reward.txt" in script and "echo 0 >" in script


def test_an_unrecognised_network_use_is_still_refused_after_rewriting(tmp_path: Path) -> None:
    script = HARBOR_UV_VERIFIER.replace("source $HOME", "pip install extra-dep\nsource $HOME")
    task = harbor.load_harbor_task(make_task(tmp_path, test_sh=script), rewrite_boilerplate=True)
    assert "verifier_needs_network" in codes(task)
    assert "rewrote_verifier_boilerplate" not in codes(task, harbor.WARNING)


def test_a_script_that_needs_no_rewrite_is_left_alone(tmp_path: Path) -> None:
    task = harbor.load_harbor_task(make_task(tmp_path), rewrite_boilerplate=True)
    assert task.test_files["test.sh"].decode() == TEST_SH and not task.issues


def test_rewrite_handles_other_pytest_pins_and_python_flags() -> None:
    script = "uvx -p 3.13 -w pytest==8.4.3 pytest /tests/test_outputs.py -rA\n"
    assert harbor.rewrite_uv_boilerplate(script) == "python3 -m pytest /tests/test_outputs.py -rA\n"


# -- pinning per-task images -----------------------------------------------------------


def test_rewrite_task_snapshot_pins_only_that_file(tmp_path: Path) -> None:
    from meridian.snapshots.build import rewrite_task_snapshot

    source = tmp_path / "src"
    source.mkdir()
    tasks = [harbor.load_harbor_task(make_task(source, n)) for n in ("one", "two")]
    suite_dir = tmp_path / "suite"
    converted, _ = harbor.write_suite(tasks, suite_dir, suite_slug="demo")
    first, second = (c.slug for c in converted)
    digest = "sha256:" + "ab" * 32

    assert rewrite_task_snapshot(suite_dir / "tasks" / f"{first}.yaml", digest) is True
    assert rewrite_task_snapshot(suite_dir / "tasks" / f"{first}.yaml", digest) is False
    suite = load_suite(suite_dir)
    assert suite.task(first).environment.snapshot == digest
    assert suite.task(second).environment.snapshot == UNBUILT_DIGEST


def test_running_a_suite_with_unbuilt_images_is_refused_with_the_fix(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    suite_dir = tmp_path / "suite"
    harbor.write_suite([harbor.load_harbor_task(make_task(source))], suite_dir, suite_slug="demo")
    result = CliRunner().invoke(app, ["run", "--suite", str(suite_dir)])
    assert result.exit_code == 2
    assert "no built image" in result.output and "build-images" in result.output


def test_build_images_without_dockerfiles_says_so(tmp_path: Path) -> None:
    from tests.conftest import CHECKOUT_SUITE

    result = CliRunner().invoke(app, ["suite", "build-images", str(CHECKOUT_SUITE)])
    assert result.exit_code == 2 and "images/<slug>/Dockerfile" in result.output


def test_only_selects_tasks_by_directory_name(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for name in ("alpha", "beta", "gamma"):
        make_task(dataset, name)
    result = CliRunner().invoke(
        app, ["import-harbor", str(dataset), "--out", str(tmp_path / "s"), "--only", "beta"]
    )
    assert result.exit_code == 0 and "read 1 task(s)" in result.output
