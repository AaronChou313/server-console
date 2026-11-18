import os
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from server import terminal


class SessionDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_excludes_exited_sessions_and_preserves_unicode_names(self):
        output = "中文 组 [Created 4h ago] \nold [Created 1d ago] (EXITED - attach to resurrect)\n"
        with (
            patch.object(terminal, "zellij", return_value=["zellij"]),
            patch.object(terminal, "run", new=AsyncMock(return_value=(0, output, ""))),
        ):
            self.assertEqual(await terminal.active_sessions(), ["中文 组"])

    def test_process_discovery_is_scoped_to_exact_socket_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / namespace / "same name" for namespace in ("selected", "other")]
            for path in paths:
                path.parent.mkdir()
            with socket.socket(socket.AF_UNIX) as current, socket.socket(socket.AF_UNIX) as other:
                current.bind(str(paths[0]))
                other.bind(str(paths[1]))
                processes = [Mock(info={"uids": SimpleNamespace(real=os.getuid()),
                                        "cmdline": ["zellij", "--server", str(path)]})
                             for path in paths]
                for process in processes:
                    process.exe.return_value = "/usr/bin/zellij"
                with (
                    patch.object(terminal, "socket_directory", return_value=paths[0].parent.resolve()),
                    patch.object(terminal, "require", return_value="/usr/bin/zellij"),
                    patch.object(terminal.psutil, "process_iter", return_value=processes),
                ):
                    self.assertEqual(terminal.session_servers(), {"same name": (processes[0], paths[0])})


class SessionTerminationTests(unittest.IsolatedAsyncioTestCase):
    async def test_graceful_timeout_leaves_force_to_explicit_request(self):
        target = (Mock(), Path("/tmp/example.sock"))
        with (
            patch.object(terminal, "session_servers", return_value={"测试": target}),
            patch.object(terminal, "zellij", return_value=["zellij"]),
            patch.object(terminal, "run", new=AsyncMock(return_value=(124, "", "timeout"))),
            patch.object(terminal, "wait_for_exit", return_value=False),
            patch.object(terminal, "force_stop", return_value=True) as force,
        ):
            with self.assertRaises(HTTPException) as raised:
                await terminal.stop_session("测试")
            self.assertEqual(raised.exception.status_code, 409)
            force.assert_not_called()
            self.assertEqual(await terminal.stop_session("测试", force=True), {"ok": True})
            force.assert_called_once_with(*target)
