"""Tests for setup_cli module."""

from pathlib import Path

import pytest

from gold_miner.setup_cli import _copy_examples_to_private, _create_env_example, run_setup


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Create a temporary project structure."""
    # Create data dir with example files
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "portfolio.example.yaml").write_text("positions:\n  test: {}\n")
    (data_dir / "trade_log.example.md").write_text("# Example\n")
    (data_dir / "personal_rules.example.md").write_text("# Rules\n")
    (data_dir / "investor_profile.example.md").write_text("# Profile\n")
    (data_dir / "doctrine_state.example.json").write_text("{}")
    (data_dir / "jd_ms_gold_history.example.csv").write_text("date,close\n")

    # Create private dir
    (data_dir / "private").mkdir()

    return tmp_path


class TestCopyExamples:
    def test_copies_when_private_empty(self, temp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should copy example files when private dir is empty."""
        monkeypatch.chdir(temp_project)
        # Override settings to use temp private dir
        from gold_miner import config
        orig_private = config.settings.private_data_dir
        config.settings.private_data_dir = temp_project / "data" / "private"

        try:
            copied = _copy_examples_to_private()
            assert len(copied) == 6
            assert (temp_project / "data" / "private" / "portfolio.yaml").exists()
            assert (temp_project / "data" / "private" / "trade_log.md").exists()
        finally:
            config.settings.private_data_dir = orig_private

    def test_skips_when_private_exists(self, temp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should not overwrite existing files."""
        monkeypatch.chdir(temp_project)
        # Pre-populate private dir
        (temp_project / "data" / "private" / "portfolio.yaml").write_text("existing: true\n")

        from gold_miner import config
        orig_private = config.settings.private_data_dir
        config.settings.private_data_dir = temp_project / "data" / "private"

        try:
            copied = _copy_examples_to_private()
            assert len(copied) == 5  # portfolio.yaml skipped
            # Existing file unchanged
            content = (temp_project / "data" / "private" / "portfolio.yaml").read_text()
            assert "existing: true" in content
        finally:
            config.settings.private_data_dir = orig_private


class TestEnvExample:
    def test_creates_env_example(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should create .env.example with all required keys."""
        monkeypatch.chdir(tmp_path)
        path = _create_env_example()
        assert path.exists()
        content = path.read_text()
        assert "FRED_API_KEY=" in content
        assert "NEWS_API_KEY=" in content
        assert "TAVILY_API_KEY=" in content
        assert "LLM_API_KEY=" in content
        assert "RISK_PROFILE=" in content


class TestRunSetup:
    def test_non_interactive_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-interactive mode should copy examples and run doctor."""
        monkeypatch.chdir(tmp_path)
        # Create data dir structure
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "private").mkdir()
        (data_dir / "portfolio.example.yaml").write_text("test: {}\n")
        (data_dir / "trade_log.example.md").write_text("# test\n")
        (data_dir / "personal_rules.example.md").write_text("# test\n")
        (data_dir / "investor_profile.example.md").write_text("# test\n")
        (data_dir / "doctrine_state.example.json").write_text("{}")
        (data_dir / "jd_ms_gold_history.example.csv").write_text("date,close\n")

        from gold_miner import config
        orig_private = config.settings.private_data_dir
        config.settings.private_data_dir = data_dir / "private"

        try:
            # Non-interactive should not prompt and should succeed
            result = run_setup(non_interactive=True)
            # Doctor may fail due to network but setup itself should work
            assert result in (0, 1)  # 0 if doctor passes, 1 if network fails
            # Verify files were copied
            assert (data_dir / "private" / "portfolio.yaml").exists()
        finally:
            config.settings.private_data_dir = orig_private
