from __future__ import annotations

import os

import pytest

from services.artifact_io import write_artifacts


def test_directory_replacement_during_write_does_not_redirect(tmp_path, monkeypatch):
    output = tmp_path / "runtime" / "seq"
    output.parent.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    real_open = os.open

    def race_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if path == "seq" and flags & os.O_DIRECTORY:
            output.rename(output.with_name("moved"))
            output.symlink_to(private, target_is_directory=True)
        return fd

    monkeypatch.setattr(os, "open", race_open)
    write_artifacts(output, {"manifest.json": b"{}"})
    assert not list(private.iterdir())
    assert (output.with_name("moved") / "manifest.json").read_bytes() == b"{}"


@pytest.mark.parametrize("link_type", ["symlink", "hardlink"])
def test_raced_existing_file_is_never_overwritten(tmp_path, monkeypatch, link_type):
    victim = tmp_path / "private.json"
    victim.write_bytes(b"unchanged")
    output = tmp_path / "seq"
    real_open = os.open

    def race_open(path, flags, *args, **kwargs):
        if path == "manifest.json":
            if link_type == "symlink":
                (output / path).symlink_to(victim)
            else:
                os.link(victim, output / path)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", race_open)
    with pytest.raises(FileExistsError):
        write_artifacts(output, {"manifest.json": b"overwritten"})
    assert victim.read_bytes() == b"unchanged"
