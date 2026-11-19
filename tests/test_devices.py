import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from server import devices, main, runtime


class DeviceDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_merges_lan_tailnet_and_manual_aliases(self):
        instance = str(uuid4())
        lan = 'http://192.168.1.11:9786'
        tail = 'http://100.66.1.10:9786'
        alias = 'https://console.example'
        status = {'available': True, 'state': 'Running', 'peers': [
            {'name': 'Test host', 'online': True, 'ips': ['100.66.1.10', '192.0.2.2']},
            {'name': 'offline', 'online': False, 'ips': ['100.66.1.20']},
        ]}
        scanner = devices.Discovery()
        reached = {lan: instance, tail: instance, alias: instance}

        async def probe(_client, url):
            return reached[url]

        with tempfile.TemporaryDirectory() as tmp, patch.object(devices, 'STATE', Path(tmp)), \
                patch.object(devices, 'discovery', scanner), patch.object(devices, 'probe', side_effect=probe), \
                patch.object(devices.host, 'tailnet', return_value=status), \
                patch.object(scanner, 'lan', return_value=[{'source': 'lan', 'url': lan, 'name': 'Test host'}]):
            own_id = devices.instance_id()
            self.assertEqual(devices.instance_id(), own_id)
            self.assertNotEqual(instance, own_id)
            await devices.add(devices.ManualDevice(url=alias, name='My device'))

            result = await scanner.refresh()
            self.assertTrue(result['tailscale'])
            self.assertEqual(len(result['devices']), 1)
            device = result['devices'][0]
            self.assertEqual(device['id'], instance)
            self.assertEqual(device['name'], 'My device')
            self.assertEqual([row['url'] for row in device['addresses']], [lan, tail, alias])

            reached[lan] = None
            scanner.updated = 0
            result = await scanner.refresh()
            device = result['devices'][0]
            self.assertEqual([row['url'] for row in device['addresses']], [tail, alias, lan])
            self.assertFalse(device['addresses'][-1]['reachable'])
            self.assertEqual(len(devices.Discovery().snapshot()['devices'][0]['addresses']), 3)

    async def test_identity_probe_ignores_redirects_and_other_services(self):
        instance = str(uuid4())

        def response(request):
            self.assertEqual(request.url.path, devices.IDENTITY)
            if request.url.host == 'valid.example':
                return httpx.Response(200, json={'service': 'server-console', 'id': instance})
            if request.url.host == 'redirect.example':
                return httpx.Response(302, headers={'Location': 'https://valid.example/api/identity'})
            if request.url.host == 'large.example':
                return httpx.Response(200, json={'service': 'server-console', 'id': instance, 'data': 'x' * 2000})
            return httpx.Response(200, json={'service': 'other', 'id': instance})

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            self.assertEqual(await devices.probe(client, 'https://valid.example'), instance)
            for label in ('redirect', 'large', 'other'):
                self.assertIsNone(await devices.probe(client, f'https://{label}.example'))


class DeviceApiTests(unittest.TestCase):
    def test_public_identity_and_authenticated_manual_crud(self):
        headers = {'X-Server-Console': '1'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(devices, 'STATE', Path(tmp)), \
                patch.object(runtime, 'STATE', Path(tmp)), patch.dict('os.environ', {'SERVER_CONSOLE_MIHOMO_BROKER': 'off'}), \
                patch.object(devices.Discovery, 'start_lan'), patch.object(devices, 'probe', return_value=None), \
                TestClient(main.app) as client:
            runtime.set_password('fixture-password')
            identity = client.get('/api/identity')
            self.assertEqual(identity.status_code, 200)
            self.assertEqual(set(identity.json()), {'service', 'id'})
            self.assertEqual(identity.json(), client.get('/api/identity').json())
            self.assertEqual(client.get('/api/devices').status_code, 401)
            self.assertEqual(client.post('/api/devices/refresh', headers=headers).status_code, 401)
            self.assertEqual(client.post('/api/login', headers=headers, json={'password': 'fixture-password'}).status_code, 200)
            url = 'https://console.example'
            response = client.post('/api/devices', headers=headers, json={'url': url})
            row = response.json()['devices'][0]
            self.assertEqual(row['addresses'], [{'url': url, 'sources': ['manual'], 'reachable': False}])
            self.assertEqual(client.post('/api/devices', headers=headers, json={'url': url + '/'}).status_code, 409)
            for invalid in ('javascript:alert(1)', 'https://user:secret@console.example', url + '/unexpected'):
                self.assertEqual(client.post('/api/devices', headers=headers, json={'url': invalid}).status_code, 422)
            path = '/api/devices/' + row['manual'][0]['key']
            self.assertEqual(client.put(path, headers=headers, json={'url': url, 'name': 'New name'})
                             .json()['devices'][0]['name'], 'New name')
            self.assertEqual(client.delete(path, headers=headers).json()['devices'], [])
