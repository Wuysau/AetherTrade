"""Relocating a configured checkout must retain its existing database volume."""
import shutil

from dotenv import dotenv_values
import pytest

from scripts import configure_console


def test_configured_instance_and_password_survive_relocation(tmp_path, monkeypatch):
    original, relocated = tmp_path / 'worktree', tmp_path / 'main'
    original.mkdir()
    relocated.mkdir()
    monkeypatch.setattr(configure_console, 'ROOT', original)
    initial = dotenv_values(configure_console.configure(55432))
    shutil.copytree(original / '.runtime', relocated / '.runtime')
    monkeypatch.setattr(configure_console, 'ROOT', relocated)
    assert dotenv_values(configure_console.configure(55432)) == initial


def test_invalid_saved_instance_is_not_overwritten(tmp_path, monkeypatch):
    runtime = tmp_path / '.runtime'
    runtime.mkdir()
    saved = runtime / 'postgres.env'
    content = 'AETHERTRADE_INSTANCE=invalid\nAETHERTRADE_POSTGRES_PASSWORD=fixture-only\n'
    saved.write_text(content)
    monkeypatch.setattr(configure_console, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='instance is invalid'):
        configure_console.configure(55432)
    assert saved.read_text() == content
