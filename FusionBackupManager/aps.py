"""APS archive downloads and public-client PKCE. No Fusion imports or design writes."""
import base64
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

from .core import atomic_json

API = 'https://developer.api.autodesk.com'
REDIRECT_URI = 'http://localhost:8765/callback'
SCOPES = 'data:read data:create'


class APSError(RuntimeError):
    """Messages intentionally exclude URLs, response bodies and credentials."""
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_url(value):
    url = urllib.parse.urljoin(API, value)
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'developer.api.autodesk.com'
            or parsed.fragment or parsed.username or parsed.password):
        raise APSError('Untrusted APS API URL')
    return url


def check_route(method, url):
    path = urllib.parse.urlsplit(api_url(url)).path
    allowed = (method == 'POST' and (path == '/authentication/v2/token'
               or re.fullmatch(r'/data/v1/projects/[^/]+/downloads', path)))
    allowed = allowed or (method == 'GET' and (
        re.fullmatch(r'/data/v1/projects/[^/]+/(jobs|downloads)/[^/]+', path)
        or re.fullmatch(r'/data/v1/projects/[^/]+/versions/[^/]+/downloadFormats', path)
        or re.fullmatch(r'/oss/v2/buckets/[^/]+/objects/[^/]+/signeds3download', path)))
    if not allowed:
        raise APSError('APS operation is not permitted by the backup client')


def download_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443)
            or not any(host.endswith(suffix) for suffix in ('.amazonaws.com', '.amazonaws.com.cn',
                                                            '.autodesk.com', '.cloudfront.net'))):
        raise APSError('Untrusted archive download URL')
    return url


class HTTP:
    def __init__(self, opener=None, timeout=15):
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.timeout = timeout

    def json(self, method, url, token=None, body=None, form=False):
        url = api_url(url)
        check_route(method, url)
        headers = {'Accept': 'application/vnd.api+json, application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/vnd.api+json'
            data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status = response.code
                if status == 303:
                    return status, dict(response.headers), {}
                if not 200 <= status < 300:
                    raise APSError('APS request failed (HTTP ' + str(status) + ')', status)
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise APSError('APS JSON response is too large')
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError()
                return status, dict(response.headers), value
        except APSError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            raise APSError('APS request failed; check network or response format') from None

    def download(self, url, path, checkpoint, max_bytes=20 * 1024**3):
        """No bearer header is sent to storage hosts, including redirects."""
        for _ in range(5):
            checkpoint()
            url = download_url(url)
            request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity'})
            try:
                try:
                    response = self.opener.open(request, timeout=self.timeout)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    if response.code in (301, 302, 303, 307, 308):
                        url = urllib.parse.urljoin(url, response.headers.get('Location', ''))
                        continue
                    if response.code != 200:
                        raise APSError('Archive download failed (HTTP ' + str(response.code) + ')')
                    length = response.headers.get('Content-Length')
                    length = int(length) if length is not None else None
                    if length is not None and (length <= 0 or length > max_bytes):
                        raise APSError('Archive size is outside the supported limit')
                    total = 0
                    with Path(path).open('xb') as output:
                        while True:
                            checkpoint()
                            block = response.read(256 * 1024)
                            if not block:
                                break
                            total += len(block)
                            if total > max_bytes:
                                raise APSError('Archive exceeded the supported size limit')
                            output.write(block)
                    if not total or (length is not None and total != length):
                        raise APSError('Archive download was incomplete')
                    checkpoint()
                    return
            except APSError:
                raise
            except (OSError, ValueError, urllib.error.URLError):
                raise APSError('Archive download failed; check network or local storage') from None
        raise APSError('Too many archive download redirects')


def dpapi(data, decrypt=False):
    """Current Windows user protection. Fail closed rather than save plaintext."""
    if os.name != 'nt':
        raise APSError('Encrypted token storage requires Windows')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    func.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise APSError('Windows could not protect or read APS credentials')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class TokenStore:
    def __init__(self, path, protect=dpapi):
        self.path, self.protect = Path(path), protect

    def load(self, client_id):
        if not self.path.exists():
            return {}
        try:
            wrapper = json.loads(self.path.read_text(encoding='utf-8'))
            value = json.loads(self.protect(base64.b64decode(wrapper['encrypted'], validate=True), True))
            return value if value.get('client_id') == client_id and value.get('scopes') == SCOPES else {}
        except (KeyError, ValueError, OSError):
            raise APSError('APS credentials could not be read; rename aps-tokens.json and sign in again') from None

    def save(self, value):
        sealed = self.protect(json.dumps(value).encode(), False)
        atomic_json(self.path, {'encrypted': base64.b64encode(sealed).decode('ascii')})


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


def callback_code(path, state):
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query)
    if parsed.path != '/callback' or len(query.get('state', [])) != 1:
        raise APSError('Invalid OAuth callback')
    if not hmac.compare_digest(query['state'][0], state):
        raise APSError('OAuth state mismatch')
    if 'error' in query:
        raise APSError('Autodesk sign-in was declined or failed')
    codes = query.get('code', [])
    if len(codes) != 1 or not codes[0]:
        raise APSError('OAuth callback has no authorization code')
    return codes[0]


class LoopbackServer(HTTPServer):
    def get_request(self):
        client, address = super().get_request()
        client.settimeout(1)
        return client, address


def browser_login(client_id, http, checkpoint, browser=webbrowser.open, timeout=300):
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    received = {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log the callback's code or state.
        def do_GET(self):
            try:
                code = callback_code(self.path, state)
            except APSError as exc:
                # Ignore unrelated requests/state mismatches; do not let them terminate sign-in.
                if urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('state') == [state]:
                    received['error'] = exc
                self.send_response(400)
                message = b'Invalid sign-in response. Return to Fusion.'
            else:
                received['code'] = code
                self.send_response(200)
                message = b'Sign-in received. You can return to Fusion.'
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(message)))
            self.end_headers()
            self.wfile.write(message)
    params = dict(response_type='code', client_id=client_id, redirect_uri=REDIRECT_URI,
                  scope=SCOPES, state=state, code_challenge=challenge, code_challenge_method='S256')
    try:
        server = LoopbackServer(('127.0.0.1', 8765), Handler)
    except OSError:
        raise APSError('Sign-in callback port 8765 is busy; close the other sign-in and retry') from None
    with server:
        server.timeout = 0.2
        if not browser(API + '/authentication/v2/authorize?' + urllib.parse.urlencode(params)):
            raise APSError('Could not open the Autodesk sign-in browser')
        end = time.monotonic() + timeout
        while not received:
            checkpoint()
            if time.monotonic() >= end:
                raise APSError('Autodesk sign-in timed out; retry backup to sign in again')
            server.handle_request()
    if 'error' in received:
        raise received['error']
    checkpoint()
    return http.json('POST', API + '/authentication/v2/token', body=dict(
        grant_type='authorization_code', client_id=client_id, code_verifier=verifier,
        code=received['code'], redirect_uri=REDIRECT_URI), form=True)[2]


def valid_client_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{8,256}', value))


class Tokens:
    def __init__(self, client_id, store, http=None, login=browser_login, clock=time.time):
        if not valid_client_id(client_id):
            raise APSError('APS Client ID is missing or invalid')
        self.client_id, self.store = client_id, store
        self.http, self.login, self.clock = http or HTTP(), login, clock
        self.value = None
        self.failure = None

    def access(self, checkpoint, force=False):
        if self.failure is not None:
            raise APSError('APS sign-in is unavailable for this run; check setup and retry backup')
        try:
            return self._access(checkpoint, force)
        except APSError as exc:
            self.failure = exc
            raise

    def _access(self, checkpoint, force=False):
        checkpoint()
        if self.value is None:
            self.value = self.store.load(self.client_id)
        if not force and self.value.get('access_token') and self.value.get('expires_at', 0) > self.clock() + 60:
            return self.value['access_token']
        refresh = self.value.get('refresh_token')
        if refresh:
            try:
                value = self.http.json('POST', API + '/authentication/v2/token', body=dict(
                    grant_type='refresh_token', client_id=self.client_id,
                    refresh_token=refresh, scope=SCOPES), form=True)[2]
            except APSError as exc:
                if exc.status not in (400, 401):
                    raise
                value = self.login(self.client_id, self.http, checkpoint)
        else:
            value = self.login(self.client_id, self.http, checkpoint)
        if (not isinstance(value.get('access_token'), str) or not value['access_token']
                or not isinstance(value.get('refresh_token', refresh), str)):
            raise APSError('Autodesk returned incomplete sign-in credentials')
        if value.get('scope') is not None and not set(SCOPES.split()) <= set(value['scope'].split()):
            raise APSError('Autodesk sign-in did not grant the required APS scopes')
        try:
            expires = min(float(value['expires_in']), 86400)
            if not 60 < expires <= 86400:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise APSError('Autodesk returned an invalid token expiry') from None
        self.value = dict(client_id=self.client_id, scopes=SCOPES, access_token=value['access_token'],
                          refresh_token=value.get('refresh_token', refresh), expires_at=self.clock() + expires)
        self.store.save(self.value)
        return self.value['access_token']


def validate_f3z(path, checkpoint=lambda: None, max_unpacked=50 * 1024**3):
    """Structural validation only. Relationships still need a Fusion restore test."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = [entry for entry in archive.infolist() if not entry.is_dir()]
            if (not members or len(members) > 100000 or sum(e.file_size for e in members) > max_unpacked
                    or not any(e.filename.lower().endswith('.f3d') for e in members)
                    or not any(not e.filename.lower().endswith('.f3d') for e in members)):
                raise APSError('F3Z archive is missing F3D data or package metadata')
            names = set()
            for entry in members:
                checkpoint()
                name = entry.filename.replace('\\', '/')
                if (entry.flag_bits & 1 or name.startswith('/') or ':' in name
                        or '..' in name.split('/') or name.casefold() in names or not entry.file_size):
                    raise APSError('F3Z archive has unsafe or invalid members')
                names.add(name.casefold())
                with archive.open(entry) as stream:
                    while stream.read(256 * 1024):
                        checkpoint()  # Reading to EOF verifies each member's CRC without extraction.
    except APSError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
        raise APSError('F3Z archive structure or CRC validation failed') from None


class Archives:
    def __init__(self, tokens, http=None, clock=time.monotonic, sleep=time.sleep, job_timeout=1800):
        self.tokens, self.http = tokens, http or HTTP()
        self.clock, self.sleep, self.job_timeout = clock, sleep, job_timeout

    def request(self, method, url, checkpoint, body=None):
        token = self.tokens.access(checkpoint)
        refreshed = False
        for attempt in range(3):
            checkpoint()
            try:
                return self.http.json(method, url, token, body)
            except APSError as exc:
                if exc.status == 401 and not refreshed and attempt < 2:
                    # An explicit 401 did not start a job. Never retry a timed-out POST.
                    token = self.tokens.access(checkpoint, force=True)
                    refreshed = True
                    continue
                if method != 'GET' or exc.status not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
                for _ in range(10 * (attempt + 1)):
                    checkpoint()
                    self.sleep(0.1)
        raise APSError('APS request retry limit reached')

    def export(self, item, path, checkpoint, report):
        project = '/data/v1/projects/' + urllib.parse.quote(item.project_id, safe='')
        version = urllib.parse.quote(item.version_id, safe='')
        report('cloud_auth')
        self.tokens.access(checkpoint)
        report('cloud_formats')
        formats = self.request('GET', API + project + '/versions/' + version + '/downloadFormats', checkpoint)[2]
        available = formats.get('data', {}).get('attributes', {}).get('formats', [])
        if not any(str(value.get('fileType', '')).lower() == 'f3z' for value in available):
            raise APSError('APS does not offer F3Z for this saved version; manual archive is required')
        checkpoint()
        report('cloud_queued')
        payload = dict(jsonapi={'version': '1.0'}, data=dict(type='downloads',
            attributes={'format': {'fileType': 'f3z'}},
            relationships={'source': {'data': {'type': 'versions', 'id': item.version_id}}}))
        value = self.request('POST', API + project + '/downloads', checkpoint, payload)[2]
        job = value.get('data', {})
        if job.get('type') != 'jobs' or not isinstance(job.get('id'), str) or not job['id']:
            raise APSError('APS did not return an archive job ID')
        job_id = job['id']
        job_url = API + project + '/jobs/' + urllib.parse.quote(job_id, safe='')
        deadline = self.clock() + self.job_timeout
        while True:
            checkpoint()
            if self.clock() >= deadline:
                raise APSError('APS F3Z generation timed out; the cloud job may still finish later')
            report('cloud_wait')
            status, headers, value = self.request('GET', job_url, checkpoint)
            if status == 303:
                location = next((v for k, v in headers.items() if k.lower() == 'location'), '')
                url = api_url(urllib.parse.urljoin(job_url, location))
                if urllib.parse.urlsplit(url).path != project + '/downloads/' + urllib.parse.quote(job_id, safe=''):
                    raise APSError('APS returned an unexpected archive download location')
                value = self.request('GET', url, checkpoint)[2]
                break
            data = value.get('data', {})
            if data.get('type') == 'downloads':
                break
            if data.get('type') != 'jobs' or data.get('id') != job_id:
                raise APSError('APS returned an unexpected archive job')
            state = data.get('attributes', {}).get('status')
            if state not in ('queued', 'processing'):
                raise APSError('APS archive job failed or returned an unsupported status')
            for _ in range(20):
                checkpoint()
                self.sleep(0.1)
        checkpoint()
        data = value.get('data', {})
        relations = data.get('relationships', {})
        source = relations.get('source', {}).get('data', {})
        if (data.get('type') != 'downloads' or data.get('id') != job_id
                or data.get('attributes', {}).get('format', {}).get('fileType', '').lower() != 'f3z'
                or source.get('type') != 'versions' or source.get('id') != item.version_id):
            raise APSError('APS archive does not match the requested saved version and format')
        storage = relations.get('storage', {}).get('data', {})
        urn = storage.get('id', '')
        prefix = 'urn:adsk.objects:os.object:'
        if storage.get('type') != 'objects' or not isinstance(urn, str) or not urn.startswith(prefix):
            raise APSError('APS archive has no valid storage object')
        bucket, separator, key = urn[len(prefix):].partition('/')
        if not separator or not bucket or not key:
            raise APSError('APS archive has an invalid storage object')
        signed_url = API + '/oss/v2/buckets/' + urllib.parse.quote(bucket, safe='') + '/objects/' + urllib.parse.quote(key, safe='') + '/signeds3download'
        signed = self.request('GET', signed_url, checkpoint)[2]
        if signed.get('status') not in (None, 'complete') or not isinstance(signed.get('url'), str):
            raise APSError('APS archive storage is not ready for a complete download')
        report('cloud_download')
        self.http.download(signed['url'], path, checkpoint)
        checkpoint()
        report('cloud_validate')
        validate_f3z(path, checkpoint)
