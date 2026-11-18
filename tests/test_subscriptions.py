import base64
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import yaml
from fastapi import HTTPException

from server import mihomo
from server.subscriptions import profile_text


PROFILE = "proxy-providers:\n  example: {type: inline, payload: []}\nrules: [\"MATCH,DIRECT\"]\n"


class SubscriptionFormatTests(unittest.TestCase):
    def test_decodes_wrapped_provider_only_yaml(self):
        encoded = base64.urlsafe_b64encode(PROFILE.encode()).decode().rstrip("=")
        self.assertEqual(profile_text(encoded), PROFILE)

    def test_links_produce_unique_names_and_keep_vless_flow(self):
        data = yaml.safe_load(profile_text("\n".join((
            "ss://aes-128-gcm:password@example.com:1234#Node",
            "ss://aes-128-gcm:password@example.com:1234#Node",
            "ss://aes-128-gcm:password@example.com:1234#Node%202",
            "vless://00000000-0000-4000-8000-000000000000@example.com:443"
            "?security=tls&flow=xtls-rprx-vision#Vision",
        ))))
        names = [proxy["name"] for proxy in data["proxies"]]
        self.assertEqual(len(set(names)), 4)
        self.assertEqual(data["proxy-groups"][0]["proxies"], [*names, "DIRECT"])
        self.assertEqual(data["proxies"][-1]["flow"], "xtls-rprx-vision")


class SubscriptionDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_actual_independent_proxy_port_after_transport_failure(self):
        state = {"ready": True, "enabled": True, "mixed_port": 7891}
        with (
            patch.object(mihomo.broker, "socket_path", return_value="/test.sock"),
            patch.object(mihomo.broker, "state", new=AsyncMock(return_value=state)),
            patch.object(mihomo, "fetch", new=AsyncMock(side_effect=[
                httpx.ConnectError("test transport failure"),
                (PROFILE, {"total": 1000}),
            ])) as fetch,
        ):
            url = "https://example.com/sub"
            self.assertEqual(await mihomo.download(url), (PROFILE, {"total": 1000}))
            self.assertEqual([args.args for args in fetch.await_args_list],
                             [(url, None), (url, "http://127.0.0.1:7891")])

    async def test_does_not_retry_rate_limited_subscription(self):
        with (
            patch.object(mihomo, "fetch", new=AsyncMock(side_effect=HTTPException(429, "rate limited"))) as fetch,
            patch.object(mihomo, "local_proxy", new=AsyncMock()) as proxy,
        ):
            with self.assertRaises(HTTPException) as raised:
                await mihomo.download("https://example.com/sub")
            self.assertEqual(raised.exception.status_code, 429)
            fetch.assert_awaited_once()
            proxy.assert_not_called()
