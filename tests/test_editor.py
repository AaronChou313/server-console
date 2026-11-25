import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import editor, main


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(main.mihomo, "restore"))
        self.enterContext(patch.object(main.devices.Discovery, "start_lan"))

    def test_requires_login_for_launcher_assets_and_websocket(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.post("/api/editor/open", headers={"X-Server-Console": "1"},
                                         json={"folder": "~"}).status_code, 401)
            self.assertEqual(client.get("/editor/").status_code, 401)
            navigation = client.get("/editor/", headers={"Accept": "text/html"}, follow_redirects=False)
            self.assertEqual(navigation.status_code, 303)
            self.assertEqual(navigation.headers["location"], "/#editor")
            self.assertEqual(client.get("/editor/static/workbench.js").status_code, 401)
            with self.assertRaises(WebSocketDisconnect) as caught:
                with client.websocket_connect("/editor/", headers={"Origin": "http://testserver"}):
                    self.fail("Editor WebSocket accepted an anonymous connection")
            self.assertEqual(caught.exception.code, 4401)

    def test_launch_validates_folder_and_encodes_host_path(self):
        with tempfile.TemporaryDirectory(prefix="sc project 空格 ") as folder, \
                patch.dict(main.sessions, {"test-editor-session": float("inf")}), \
                patch.object(editor, "start") as start, TestClient(main.app) as client:
            client.cookies.set(main.COOKIE, "test-editor-session")
            headers = {"X-Server-Console": "1"}
            self.assertEqual(client.post("/api/editor/open", headers=headers,
                                         json={"folder": folder + "/missing"}).status_code, 400)
            start.assert_not_awaited()
            response = client.post("/api/editor/open", headers=headers, json={"folder": folder})
            self.assertEqual(response.status_code, 200)
            target = urlsplit(response.json()["url"])
            self.assertEqual(target.path, "/editor/")
            self.assertEqual(parse_qs(target.query)["folder"], [str(Path(folder).resolve())])
            start.assert_awaited_once()

    def test_proxy_does_not_forward_console_credentials(self):
        def handler(request):
            self.assertEqual(str(request.url), "http://localhost/workbench?folder=%2Fsample")
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(302, stream=httpx.ByteStream(b""), headers={"Location": "/workbench/next", "Set-Cookie": "upstream=value"})

        with patch.dict(main.sessions, {"test-editor-session": float("inf")}), \
                patch.object(editor, "executable_socket", return_value="/tmp/unused.sock"), \
                patch.object(editor, "client_for", return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler))), \
                TestClient(main.app) as client:
            client.cookies.set(main.COOKIE, "test-editor-session")
            response = client.get("/editor/workbench?folder=%2Fsample", follow_redirects=False,
                                  headers={"Authorization": "test-secret"})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], "/editor/workbench/next")
            self.assertNotIn("set-cookie", response.headers)
