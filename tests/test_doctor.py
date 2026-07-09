"""Tests for doctor module."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gold_miner.doctor import CheckResult, Doctor, DoctorReport, run_doctor


class TestDoctorReport:
    def test_all_passed_when_all_critical_ok(self) -> None:
        r = DoctorReport()
        r.add(CheckResult("A", True, "ok", critical=True))
        r.add(CheckResult("B", True, "ok", critical=True))
        assert r.all_passed is True
        assert r.critical_passed is True

    def test_all_passed_when_optional_fails(self) -> None:
        r = DoctorReport()
        r.add(CheckResult("A", True, "ok", critical=True))
        r.add(CheckResult("B", False, "missing", critical=False))
        assert r.all_passed is True  # non-critical failure doesn't block
        assert r.critical_passed is True

    def test_fails_when_critical_fails(self) -> None:
        r = DoctorReport()
        r.add(CheckResult("A", False, "bad", critical=True))
        r.add(CheckResult("B", True, "ok", critical=True))
        assert r.all_passed is False
        assert r.critical_passed is False


class TestDoctor:
    def test_python_version_check(self) -> None:
        d = Doctor()
        d._check_python_version()
        result = d.report.results[0]
        assert result.name == "Python 版本"
        assert result.passed is True  # CI should run on 3.11+
        assert "3." in result.message

    def test_env_file_check_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        d = Doctor()
        d._check_env_file()
        result = [r for r in d.report.results if r.name == ".env 配置文件"][0]
        assert result.passed is False
        assert "不存在" in result.message

    def test_env_file_check_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("TEST=1\n")
        d = Doctor()
        d._check_env_file()
        result = [r for r in d.report.results if r.name == ".env 配置文件"][0]
        assert result.passed is True

    def test_private_dir_check(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        private_dir = tmp_path / "data" / "private"

        from gold_miner import config
        orig = config.settings.private_data_dir
        config.settings.private_data_dir = private_dir

        try:
            d = Doctor()
            d._check_private_data_dir()
            result = [r for r in d.report.results if r.name == "私有数据目录"][0]
            assert result.passed is True
            assert "可写" in result.message
        finally:
            config.settings.private_data_dir = orig

    def test_api_keys_check_none_set(self) -> None:
        d = Doctor()
        # Use a fresh DoctorReport to avoid state from previous tests
        d.report = DoctorReport()
        with patch("gold_miner.doctor.settings") as mock_settings:
            mock_settings.news_api_key = ""
            mock_settings.tavily_api_key = ""
            mock_settings.fred_api_key = ""
            mock_settings.llm_api_key = ""
            d._check_api_keys()
        result = [r for r in d.report.results if r.name == "API Keys"][0]
        assert result.passed is False

    def test_api_keys_check_with_key(self) -> None:
        d = Doctor()
        with patch("gold_miner.doctor.settings") as mock_settings:
            mock_settings.news_api_key = "real_key_123"
            mock_settings.tavily_api_key = ""
            mock_settings.fred_api_key = ""
            mock_settings.llm_api_key = ""
            d._check_api_keys()
        result = [r for r in d.report.results if r.name == "API Keys"][0]
        assert result.passed is True

    def test_storage_check(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        private_dir = tmp_path / "data" / "private"
        private_dir.mkdir(parents=True)

        from gold_miner import config
        orig = config.settings.private_data_dir
        config.settings.private_data_dir = private_dir

        try:
            d = Doctor()
            d._check_storage()
            result = [r for r in d.report.results if r.name == "存储接口"][0]
            assert result.passed is True
        finally:
            config.settings.private_data_dir = orig

    def test_run_all_returns_report(self) -> None:
        d = Doctor()
        report = d.run_all()
        assert len(report.results) >= 5  # python, env, private_dir, api_keys, network, storage
        assert any(r.name == "Python 版本" for r in report.results)
        assert any(r.name == "私有数据目录" for r in report.results)


class TestRunDoctorCLI:
    def test_returns_zero_on_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("gold_miner.doctor.Doctor.run_all") as mock_run:
            report = DoctorReport()
            report.add(CheckResult("Python 版本", True, "ok", critical=True))
            report.add(CheckResult("私有数据目录", True, "ok", critical=True))
            report.add(CheckResult("存储接口", True, "ok", critical=True))
            mock_run.return_value = report
            result = run_doctor()
        assert result == 0
        captured = capsys.readouterr()
        assert "所有关键检查通过" in captured.out

    def test_returns_one_on_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("gold_miner.doctor.Doctor.run_all") as mock_run:
            report = DoctorReport()
            report.add(CheckResult("Python 版本", False, "too old", critical=True))
            report.add(CheckResult("私有数据目录", True, "ok", critical=True))
            mock_run.return_value = report
            result = run_doctor()
        assert result == 1
        captured = capsys.readouterr()
        assert "关键检查失败" in captured.out
