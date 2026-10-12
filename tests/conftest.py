"""Fixtures shared by every test file.

Put a fixture here only when more than one test file needs it, or when it
must apply to every test (autouse), like keeping run_pipeline's scratch files
out of the repo. A fixture used by one file belongs in that file.
"""

import pytest

import run_pipeline


@pytest.fixture(autouse=True)
def isolated_work_dir(tmp_path, monkeypatch):
    """Keep run_pipeline's intermediate JSON out of the repo's work/ directory.

    run() always writes under the module-level WORK_DIR; output_dir is a
    parameter, so tests pass a tmp_path for that themselves.
    """
    work_dir = tmp_path / "work"
    monkeypatch.setattr(run_pipeline, "WORK_DIR", work_dir)
    return work_dir
