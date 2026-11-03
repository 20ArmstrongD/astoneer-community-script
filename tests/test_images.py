"""Registry protocol fixtures are synthetic, never release image digests."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import images
import host
import manage
from test_project import config, result


def encode(obj):
    data = json.dumps(obj).encode()
    return data, 'sha256:' + hashlib.sha256(data).hexdigest()


class RegistryTests(unittest.TestCase):
    def fixture(self, index=False, arch='amd64'):
        cfg, cfg_digest = encode({'os': 'linux', 'architecture': arch, 'config': {'Labels': {}}})
        manifest, digest = encode({'schemaVersion': 2, 'mediaType': images.MANIFEST[0], 'config': {'digest': cfg_digest, 'size': len(cfg)}})
        self.digest = digest; self.cfg_digest = cfg_digest
        self.manifest = manifest; self.cfg = cfg
        self.index = None
        if index:
            self.index, self.top_digest = encode({'schemaVersion': 2, 'mediaType': images.INDEX[0], 'manifests': [{'digest': digest, 'size': len(manifest), 'platform': {'os': 'linux', 'architecture': arch}}]})
        self.calls = []

    def fetch(self, url, headers=None):
        self.calls.append((url, headers))
        if '/token?' in url:
            return b'{"token":"fixture-token"}', {}
        if '/blobs/' in url:
            return self.cfg, {}
        if self.index is not None and url.endswith('/latest'):
            return self.index, {'Docker-Content-Digest': self.top_digest}
        return self.manifest, {'Docker-Content-Digest': self.digest}

    def test_single_manifest_verified(self):
        self.fixture()
        with patch.object(images, 'fetch', self.fetch):
            record = images.resolve('game')
        self.assertEqual(record['image'], 'barumel/docker-astroneer-server@' + self.digest)
        self.assertEqual(record['platform'], 'linux/amd64')
        self.assertEqual(record['config_digest'], self.cfg_digest)
        self.assertNotIn('fixture-token', json.dumps(record))
        self.assertIn('registry-1.docker.io/v2/barumel/', self.calls[1][0])

    def test_multiarch_selects_child_not_index(self):
        self.fixture(index=True)
        with patch.object(images, 'fetch', self.fetch):
            record = images.resolve('game')
        self.assertEqual(record['top_digest'], self.top_digest)
        self.assertTrue(record['image'].endswith(self.digest))
        self.assertNotEqual(self.digest, self.top_digest)

    def test_pinned_digest_checked_again(self):
        self.fixture()
        with patch.object(images, 'fetch', self.fetch):
            images.resolve('game', self.digest)
            with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                images.resolve('game', 'sha256:' + '0'*64)

    def test_wrong_architecture(self):
        self.fixture(arch='arm64')
        with patch.object(images, 'fetch', self.fetch), self.assertRaisesRegex(ValueError, 'not linux/amd64'):
            images.resolve('game')

    def test_no_amd64_index(self):
        self.fixture(index=True, arch='arm64')
        with patch.object(images, 'fetch', self.fetch), self.assertRaisesRegex(ValueError, 'exactly one'):
            images.resolve('game')

    def test_tampered_manifest_header(self):
        self.fixture()
        original = self.fetch
        def fetch(url, headers=None):
            data, hdr = original(url, headers)
            if '/manifests/' in url:
                hdr['Docker-Content-Digest'] = 'sha256:' + '0'*64
            return data, hdr
        with patch.object(images, 'fetch', fetch), self.assertRaisesRegex(ValueError, 'digest mismatch'):
            images.resolve('game')

    def test_tampered_config(self):
        self.fixture(); self.cfg += b' '
        with patch.object(images, 'fetch', self.fetch), self.assertRaisesRegex(ValueError, 'digest mismatch'):
            images.resolve('game')

    def test_wrong_descriptor_size(self):
        self.fixture(index=True)
        doc = json.loads(self.index); doc['manifests'][0]['size'] += 1
        self.index, self.top_digest = encode(doc)
        with patch.object(images, 'fetch', self.fetch), self.assertRaisesRegex(ValueError, 'size mismatch'):
            images.resolve('game')

    def test_invalid_references_no_requests(self):
        with patch.object(images, 'fetch') as fetch:
            for ref in ('https://evil/image', '../x', 'tag\nheader', 'latest;id'):
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    images.resolve('game', ref)
            fetch.assert_not_called()

    def test_missing_token(self):
        with patch.object(images, 'fetch', return_value=(b'{}', {})), self.assertRaisesRegex(ValueError, 'pull token'):
            images.resolve('game')

    def test_playit_approved_registry(self):
        self.fixture()
        with patch.object(images, 'fetch', self.fetch):
            record = images.resolve('playit')
        self.assertTrue(record['image'].startswith('ghcr.io/playit-cloud/playit-agent@'))
        self.assertTrue(self.calls[0][0].startswith('https://ghcr.io/token?'))

    def test_no_insecure_fetch(self):
        with self.assertRaises(ValueError):
            images.fetch('http://registry.example/x')

    def test_redirect_strips_token_cross_host(self):
        req = urllib.request.Request('https://ghcr.io/blob', headers={'Authorization': 'Bearer private'})
        new = images.SafeRedirect().redirect_request(req, None, 302, '', {}, 'https://cdn.example/blob')
        self.assertIsNone(new.get_header('Authorization'))
        with self.assertRaises(ValueError):
            images.SafeRedirect().redirect_request(req, None, 302, '', {}, 'http://cdn.example/blob')

    def test_malformed_metadata_is_readable_error(self):
        with patch.object(images, 'fetch', return_value=(b'[]', {})), self.assertRaisesRegex(ValueError, 'malformed'):
            images.resolve('game')

    def test_network_failure_is_readable(self):
        with patch.object(images.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = images.urllib.error.URLError('fixture DNS failure')
            with self.assertRaisesRegex(ValueError, 'network/TLS/timeout'):
                images.fetch('https://registry-1.docker.io/v2/')

    def test_response_size_bounded(self):
        with patch.object(images.urllib.request, 'build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'x' * (images.LIMIT + 1)
            with self.assertRaisesRegex(ValueError, 'size limit'):
                images.fetch('https://registry-1.docker.io/v2/')



class WizardTests(unittest.TestCase):
    def network(self, answers, secrets='a'*20):
        c = config(); out = io.StringIO()
        with patch.object(host, 'ask', side_effect=answers), patch.object(host.getpass, 'getpass', return_value=secrets), patch.object(host, 'select_image', return_value=('ghcr.io/playit-cloud/playit-agent@sha256:'+'b'*64, {'approved': True})), contextlib.redirect_stdout(out):
            proof = host.network(c)
        return c, proof, out.getvalue()

    def test_direct(self):
        c, proof, out = self.network(['direct', '8.8.8.8', '8777', '8777'])
        self.assertEqual(c['port'], 8777); self.assertIsNone(proof)
        self.assertIn('Forward UDP', out)
        self.assertIn('advertised=8.8.8.8:8777', out)

    def test_direct_rejects_private_and_mismatch(self):
        for answers in (['direct', '192.168.1.5', '8777', '8777'], ['direct', '8.8.8.8', '8777', '12345']):
            with self.subTest(answers=answers), self.assertRaises(ValueError):
                self.network(answers)

    def test_playit_uses_actual_assigned_port(self):
        c, proof, out = self.network(['playit', '8.8.8.8', '12345', '12345'])
        self.assertEqual(c['port'], 12345); self.assertEqual(c['external_port'], 12345)
        self.assertTrue(proof['approved'])
        self.assertIn('assigned public IPv4', out)
        self.assertIn('advertised=8.8.8.8:12345', out)
        self.assertNotIn('a'*20, out)

    def test_playit_mismatch_refused(self):
        with self.assertRaises(ValueError):
            self.network(['playit', '8.8.8.8', '12345', '8777'])

    def test_lan_no_public_endpoint_prompt(self):
        c, proof, out = self.network(['lan', '192.168.1.105', '8777'])
        self.assertEqual(c['external_port'], 8777); self.assertIsNone(proof)
        self.assertIn('LAN joins may fail', out)
        self.assertEqual(c['playit_secret'], '')

    def test_invalid_mode_refused_early(self):
        with self.assertRaisesRegex(ValueError, 'Network mode'):
            self.network(['invalid'])

    def test_image_approval_required(self):
        proof = {'image': config()['image'], 'platform': 'linux/amd64'}
        with patch.object(host, 'ask', return_value='latest'), patch.object(host, 'resolve', return_value=proof), patch('builtins.input', return_value='no'), patch.object(host, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'approval declined'):
                host.select_image('game')
        run.assert_not_called()

    def test_image_approval_returns_digest(self):
        proof = {'image': config()['image'], 'platform': 'linux/amd64'}
        with patch.object(host, 'ask', return_value='latest'), patch.object(host, 'resolve', return_value=proof), patch('builtins.input', return_value='APPROVE'), contextlib.redirect_stdout(io.StringIO()):
            image, record = host.select_image('game')
        self.assertEqual(image, config()['image']); self.assertTrue(record['approved'])

    def test_default_wizard_without_manual_digest(self):
        answers = ['Astroneer', 'Owner', 'yes', 'direct', '8.8.8.8', '8777', '8777']
        proof = {'image': config()['image'], 'approved': True}
        with patch.object(host, 'run', return_value=result('105')), patch.object(host, 'select_image', return_value=(config()['image'], proof)), patch.object(host, 'ask', side_effect=answers), patch.object(host.getpass, 'getpass', return_value='secret'), contextlib.redirect_stdout(io.StringIO()):
            h, c, records = host.wizard(False)
        self.assertEqual(h['id'], 105)
        self.assertEqual(c['image'], config()['image'])
        self.assertEqual(records['game'], proof)
        self.assertIsNone(records['playit'])

    def test_lan_rejects_loopback(self):
        with self.assertRaisesRegex(ValueError, 'unicast'):
            self.network(['lan', '127.0.0.1', '8777'])



class GuestVerification(unittest.TestCase):
    def test_pull_and_inspect_matches_approval(self):
        image = config()['image']; config_id = 'sha256:'+'c'*64
        info = [{'Os': 'linux', 'Architecture': 'amd64', 'Id': config_id, 'RepoDigests': [image]}]
        proof = {'image': image, 'platform': 'linux/amd64', 'approved': True, 'config_digest': config_id}
        with patch.object(manage, 'run', side_effect=[result(), result(json.dumps(info))]) as run:
            manage.pull_verified(image, proof)
        self.assertEqual(run.call_args_list[0].args[0], ['docker', 'pull', '--platform', 'linux/amd64', image])

    def test_guest_rejects_wrong_platform_digest_or_config(self):
        image = config()['image']
        good = dict(Os='linux', Architecture='amd64', Id='sha256:'+'c'*64, RepoDigests=[image])
        for override in ({'Architecture': 'arm64'}, {'RepoDigests': []}, {'Id': 'sha256:'+'d'*64}):
            proof = {'image': image, 'platform': 'linux/amd64', 'approved': True, 'config_digest': good['Id']}
            with self.subTest(override=override), patch.object(manage, 'run', side_effect=[result(), result(json.dumps([dict(good, **override)]))]), self.assertRaises(ValueError):
                manage.pull_verified(image, proof)
