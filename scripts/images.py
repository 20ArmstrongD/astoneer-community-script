"""Resolve only approved publisher repositories using HTTPS, without host Docker."""
import datetime
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request

REPOSITORIES = {
    'game': ('barumel/docker-astroneer-server', 'registry-1.docker.io',
             'https://auth.docker.io/token', 'registry.docker.io',
             'https://github.com/barumel/docker-astroneer-server'),
    'playit': ('playit-cloud/playit-agent', 'ghcr.io', 'https://ghcr.io/token',
               'ghcr.io', 'https://github.com/playit-cloud/playit-agent'),
}
MANIFEST = ('application/vnd.docker.distribution.manifest.v2+json',
            'application/vnd.oci.image.manifest.v1+json')
INDEX = ('application/vnd.docker.distribution.manifest.list.v2+json',
         'application/vnd.oci.image.index.v1+json')
DIGEST = r'sha256:[a-f0-9]{64}'
LIMIT = 8 * 1024 * 1024


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Registry redirect must use HTTPS')
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            new.remove_header('Authorization')
        return new


def fetch(url, headers=None):
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('Registry requests must use HTTPS')
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=20) as response:
            data = response.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError('Registry response exceeds size limit')
            return data, response.headers
    except (urllib.error.URLError, TimeoutError) as e:
        status = f'HTTP {e.code}' if isinstance(e, urllib.error.HTTPError) else 'network/TLS/timeout error'
        raise ValueError(f'Registry lookup failed ({status}); check DNS, HTTPS access, rate limits and tag availability. No image selected.') from None


def checked(data, expected):
    if not isinstance(expected, str) or not re.fullmatch(DIGEST, expected):
        raise ValueError('Registry returned an invalid sha256 digest')
    if 'sha256:' + hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Registry content digest mismatch')


def descriptor(value):
    if not isinstance(value, dict) or not re.fullmatch(DIGEST, str(value.get('digest', ''))) or type(value.get('size')) is not int or not 0 < value['size'] <= LIMIT:
        raise ValueError('Invalid registry content descriptor')
    return value


def resolve(kind, reference='latest'):
    try:
        return _resolve(kind, reference)
    except (AttributeError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('Registry returned malformed image metadata; no image selected') from None


def _resolve(kind, reference='latest'):
    if kind not in REPOSITORIES:
        raise ValueError('Unsupported image publisher')
    if not isinstance(reference, str) or not (re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', reference) or re.fullmatch(DIGEST, reference)):
        raise ValueError('Enter a tag or sha256 digest, not a URL or repository name')
    repo, registry, auth, service, source = REPOSITORIES[kind]
    query = urllib.parse.urlencode({'service': service, 'scope': f'repository:{repo}:pull'})
    token_data, _ = fetch(auth + '?' + query)
    token_doc = json.loads(token_data)
    token = token_doc.get('token') or token_doc.get('access_token')
    if not isinstance(token, str) or not 1 <= len(token) <= 16384 or any(ord(c) < 32 for c in token):
        raise ValueError('Registry did not return a valid anonymous pull token')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': ', '.join(MANIFEST + INDEX)}

    def get_manifest(ref, desc=None):
        data, response_headers = fetch(f'https://{registry}/v2/{repo}/manifests/{ref}', headers)
        actual = 'sha256:' + hashlib.sha256(data).hexdigest()
        if ref.startswith('sha256:'):
            checked(data, ref)
        advertised = response_headers.get('Docker-Content-Digest')
        if advertised:
            checked(data, advertised)
        if desc:
            checked(data, desc['digest'])
            if len(data) != desc['size']:
                raise ValueError('Registry manifest size mismatch')
        doc = json.loads(data)
        if doc.get('schemaVersion') != 2 or doc.get('mediaType') not in MANIFEST + INDEX:
            raise ValueError('Unsupported image manifest schema/media type')
        return doc, actual

    doc, top_digest = get_manifest(reference)
    selected = top_digest
    if doc['mediaType'] in INDEX:
        matches = [d for d in doc.get('manifests', []) if d.get('platform', {}).get('os') == 'linux' and d.get('platform', {}).get('architecture') == 'amd64' and not d.get('platform', {}).get('variant')]
        if len(matches) != 1:
            raise ValueError('Image index must contain exactly one linux/amd64 image')
        desc = descriptor(matches[0])
        doc, selected = get_manifest(desc['digest'], desc)
    if doc['mediaType'] not in MANIFEST:
        raise ValueError('Expected a platform image manifest')
    config = descriptor(doc.get('config'))
    data, _ = fetch(f'https://{registry}/v2/{repo}/blobs/{config["digest"]}', headers)
    checked(data, config['digest'])
    if len(data) != config['size']:
        raise ValueError('Image configuration size mismatch')
    image_config = json.loads(data)
    if image_config.get('os') != 'linux' or image_config.get('architecture') != 'amd64':
        raise ValueError('Image configuration is not linux/amd64; emulation is not supported')
    prefix = repo if kind == 'game' else 'ghcr.io/' + repo
    labels = image_config.get('config', {}).get('Labels') or {}
    runtime = {item.split('=', 1)[0]: item.split('=', 1)[1] for item in image_config.get('config', {}).get('Env', []) if isinstance(item, str) and item.startswith('GE_PROTON_VERSION=')}
    return {'image': prefix + '@' + selected, 'requested': prefix + ':' + reference if not reference.startswith('sha256:') else prefix + '@' + reference,
            'registry': registry, 'source': source, 'platform': 'linux/amd64',
            'top_digest': top_digest, 'config_digest': config['digest'],
            'resolved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'proton_version': runtime.get('GE_PROTON_VERSION', 'not supplied by image'),
            'publisher_revision_label': labels.get('org.opencontainers.image.revision'),
            'trust': 'Registry content hashes verified; publisher signature/source-to-image correspondence not verified'}
