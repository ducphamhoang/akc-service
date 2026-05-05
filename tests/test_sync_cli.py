import sys
import pytest
from unittest.mock import patch


def _run_cli(args: list) -> int:
    from akc_service.sync.cli import main
    with patch("sys.argv", ["akc-sync"] + args):
        try:
            main()
            return 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 0


class TestSyncCLI:
    def test_status_exits_0(self, tmp_path, monkeypatch):
        import akc_service.sync.cli as cli_mod
        monkeypatch.setattr(cli_mod, "KB_DIR", tmp_path)
        code = _run_cli(["status"])
        assert code == 0

    def test_push_disabled_exits_1(self, tmp_path, monkeypatch):
        import akc_service.sync.config as sc
        import akc_service.sync.cli as cli_mod
        monkeypatch.setattr(sc, "REMOTE_URL", "")
        monkeypatch.setattr(cli_mod, "KB_DIR", tmp_path)
        code = _run_cli(["push"])
        assert code == 1

    @patch("akc_service.sync.cli.push_to_remote")
    def test_push_calls_push_logic(self, mock_push, tmp_path, monkeypatch):
        import akc_service.sync.config as sc
        import akc_service.sync.cli as cli_mod
        monkeypatch.setattr(sc, "REMOTE_URL", "http://remote:8000")
        monkeypatch.setattr(sc, "REMOTE_API_KEY", "key")
        monkeypatch.setattr(cli_mod, "KB_DIR", tmp_path)
        mock_push.return_value = {"pushed": 2, "skipped": 0, "errors": 0, "cursor": None}
        code = _run_cli(["push"])
        assert code == 0
        mock_push.assert_called_once()

    @patch("akc_service.sync.cli.pull_from_remote")
    def test_pull_calls_pull_logic(self, mock_pull, tmp_path, monkeypatch):
        import akc_service.sync.config as sc
        import akc_service.sync.cli as cli_mod
        monkeypatch.setattr(sc, "REMOTE_URL", "http://remote:8000")
        monkeypatch.setattr(sc, "REMOTE_API_KEY", "key")
        monkeypatch.setattr(cli_mod, "KB_DIR", tmp_path)
        mock_pull.return_value = {"pulled": 3, "conflicts": 0, "errors": 0}
        code = _run_cli(["pull"])
        assert code == 0
        mock_pull.assert_called_once()
