import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from server import mihomo_geo


class GeoAssetTests(unittest.IsolatedAsyncioTestCase):
    def test_selects_assets_from_rules_and_dns(self):
        self.assertEqual(mihomo_geo.assets_for({"proxies": [{"name": "GEOIP 名称"}]}), [])
        self.assertEqual(mihomo_geo.assets_for({"rules": ["AND,((GEOIP,CN),(DST-PORT,443)),DIRECT",
                                                          "GEOSITE,cn,DIRECT"]}), ["mmdb", "geosite"])
        source = {"geodata-mode": True, "dns": {"fallback": ["https://dns.example/dns-query"]}}
        self.assertEqual(mihomo_geo.assets_for(source), ["geoip"])
        source["dns"]["fallback-filter"] = {"geoip": False}
        source["dns"]["nameserver-policy"] = {"geosite:cn": "system"}
        self.assertEqual(mihomo_geo.assets_for(source), ["geosite"])

    def test_honors_custom_urls_and_mirror_prefix(self):
        source = {"geox-url": {"mmdb": "https://geo.example/custom.mmdb"}}
        with patch.dict("os.environ", {"SERVER_CONSOLE_GEO_MIRROR": "https://mirror.example/geo"}):
            self.assertEqual(mihomo_geo.asset_urls(source, "mmdb"), ["https://geo.example/custom.mmdb"])
            self.assertEqual(mihomo_geo.asset_urls(source, "geosite"), ["https://mirror.example/geo/geosite.dat"])

    async def test_download_skips_html_and_preserves_existing_file_if_every_source_fails(self):
        def fetch(request):
            if request.url.path == "/good":
                return httpx.Response(200, content=b"data" * 1000)
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>" * 1000)

        async with httpx.AsyncClient(transport=httpx.MockTransport(fetch)) as client:
            with tempfile.TemporaryDirectory() as root:
                path = Path(root) / "geosite.dat"
                path.write_bytes(b"original")
                self.assertFalse(await mihomo_geo.download_asset(client, ["https://geo.example/bad"], path))
                self.assertEqual(path.read_bytes(), b"original")
                self.assertTrue(await mihomo_geo.download_asset(
                    client, ["https://geo.example/bad", "https://geo.example/good"], path,
                ))
                self.assertEqual(path.stat().st_size, 4000)
                self.assertEqual(list(path.parent.iterdir()), [path])

    async def test_reuses_existing_mmdb_alias(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "country.mmdb").write_bytes(b"data" * 1000)
            with patch.object(mihomo_geo.httpx, "AsyncClient") as client:
                await mihomo_geo.ensure_geo({"rules": ["GEOIP,CN,DIRECT"]}, path)
            client.assert_not_called()
