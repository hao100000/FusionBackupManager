"""Deterministic APS protocol/security tests. No Autodesk account or network used."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import zipfile

from FusionBackupManager.aps import (API, APSError, Archives, HTTP, Tokens, TokenStore,
    SCOPES, REDIRECT_URI, pkce_pair, callback_code, check_route, api_url, download_url,
    validate_f3z, dpapi, browser_login)
from FusionBackupManager.core import Cancelled, Backup, Store
from test_core import Adapter, item


def archive_bytes(name='part.f3d'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr(name, b'CAD payload')
        archive.writestr('manifest.json', b'{"relations":[]}')
    return out.getvalue()


class FakeTokens:
    def __init__(self):
        self.forced = []
    def access(self, checkpoint, force=False):
        checkpoint()
        self.forced.append(force)
        return 'SECRET-BEARER'


class Protocol:
    def __init__(self, version='file:version:1'):
        self.calls = []
        self.formats = ['f3z']
        self.job_states = ['queued', 'processing']
        self.redirect = None
        self.details = dict(data=dict(type='downloads', id='job1', attributes={'format': {'fileType':'f3z'}},
            relationships={'source': {'data': {'type':'versions','id':version}},
                           'storage': {'data': {'type':'objects','id':'urn:adsk.objects:os.object:bucket/key/path.f3z'}}}))
        self.signed = {'status':'complete','url':'https://bucket.s3.amazonaws.com/archive?signature=SECRET'}
        self.bytes = archive_bytes()
        self.download_hook = lambda: None
    def json(self, method, url, token=None, body=None, form=False):
        check_route(method, url)
        self.calls.append((method, url, token, body, form))
        if url.endswith('/downloadFormats'):
            return 200, {}, {'data': {'attributes': {'formats': [{'fileType':f} for f in self.formats]}}}
        if method == 'POST':
            return 202, {}, {'data': {'type':'jobs','id':'job1','attributes':{'status':'queued'}}}
        if '/jobs/' in url:
            if self.job_states:
                return 200, {}, {'data': {'type':'jobs','id':'job1','attributes':{'status':self.job_states.pop(0)}}}
            return 303, {'Location': self.redirect or '/data/v1/projects/project/downloads/job1'}, {}
        if '/downloads/' in url:
            return 200, {}, self.details
        if url.endswith('/signeds3download'):
            return 200, {}, self.signed
        raise AssertionError('Unexpected request ' + url)
    def download(self, url, path, checkpoint):
        download_url(url)
        self.download_hook()
        Path(path).write_bytes(self.bytes)
        checkpoint()


class Response(io.BytesIO):
    def __init__(self, content=b'{}', code=200, headers=None):
        super().__init__(content)
        self.code, self.headers = code, headers or {}


class Opener:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
    def open(self, request, timeout):
        self.requests.append(request)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class APSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('FBM_TEST_TMP'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'out.f3z'
        self.http = Protocol()
        self.ticks = [0]
        self.tokens = FakeTokens()
        self.client = Archives(self.tokens, self.http, clock=lambda:self.ticks[0],
                               sleep=lambda n:self.ticks.__setitem__(0,self.ticks[0]+n),job_timeout=10)
        self.parent = item(reference_state='external')
        self.events = []
    def export(self, checkpoint=lambda:None):
        self.client.export(self.parent,self.path,checkpoint,self.events.append)

    def test_archive_job_exact_version_signed_download_and_crc(self):
        self.export()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.events[-2:],['cloud_download','cloud_validate'])
        posts = [c for c in self.http.calls if c[0]=='POST']
        self.assertEqual(len(posts),1)
        self.assertEqual(posts[0][3]['data']['relationships']['source']['data'],
                         {'type':'versions','id':self.parent.version_id})
        self.assertIn('/objects/key%2Fpath.f3z/signeds3download',self.http.calls[-1][1])
        self.assertTrue(all(c[0] in ('GET','POST') for c in self.http.calls))

    def test_ids_are_encoded_not_interpolated_as_query(self):
        self.parent.project_id='a.project space'
        self.parent.version_id='urn:adsk:file?version=7'
        self.http.details['data']['relationships']['source']['data']['id']=self.parent.version_id
        self.http.redirect='/data/v1/projects/a.project%20space/downloads/job1'
        self.export()
        self.assertIn('urn%3Aadsk%3Afile%3Fversion%3D7/downloadFormats',self.http.calls[0][1])

    def test_unsupported_format_never_starts_job(self):
        self.http.formats=['step']
        with self.assertRaisesRegex(APSError,'does not offer'):
            self.export()
        self.assertFalse(any(c[0]=='POST' for c in self.http.calls))

    def test_failed_job_and_timeout_do_not_download(self):
        self.http.job_states=['failed']
        with self.assertRaisesRegex(APSError,'failed'):
            self.export()
        self.assertFalse(self.path.exists())
        self.http.job_states=['processing']*100
        with self.assertRaisesRegex(APSError,'timed out'):
            self.export()
        self.assertFalse(self.path.exists())

    def test_303_cannot_redirect_bearer_to_another_host_or_project(self):
        for location in ['https://attacker.example/collect', '/data/v1/projects/other/downloads/job1',
                         '/data/v1/projects/project/downloads/other']:
            self.http.redirect=location
            self.http.job_states=[]
            with self.assertRaises(APSError):
                self.export()
            self.assertFalse(any('attacker' in c[1] or '/other/' in c[1] for c in self.http.calls))

    def test_download_requires_exact_source_and_format(self):
        data=self.http.details['data']
        mutations=[lambda:data['relationships']['source']['data'].update(id='other-version'),
                   lambda:data['attributes']['format'].update(fileType='f3d'),
                   lambda:data.update(id='different-job')]
        for mutate in mutations:
            self.http=Protocol(); self.client.http=self.http; data=self.http.details['data']
            mutate()
            with self.assertRaisesRegex(APSError,'does not match'):
                self.export()
            self.assertFalse(self.path.exists())

    def test_missing_storage_or_incomplete_storage_is_rejected(self):
        self.http.details['data']['relationships']['storage']['data']['id']='wrong-storage'
        with self.assertRaises(APSError): self.export()
        self.http=Protocol(); self.client.http=self.http
        self.http.signed['status']='chunked'
        with self.assertRaisesRegex(APSError,'not ready'): self.export()

    def test_cancel_wait_and_download_does_not_publish(self):
        def cancel_wait():
            if 'cloud_wait' in self.events: raise Cancelled()
        with self.assertRaises(Cancelled): self.export(cancel_wait)
        self.assertFalse(self.path.exists())
        self.events.clear()
        self.http.job_states=[]
        stopped=[False]
        self.http.download_hook=lambda:stopped.__setitem__(0,True)
        def cancel_download():
            if stopped[0]: raise Cancelled()
        with self.assertRaises(Cancelled): self.export(cancel_download)

    def test_bad_archive_rejected_and_not_treated_as_f3z(self):
        for content in [b'<html>error</html>',b'not-a-zip']:
            self.path.write_bytes(content)
            with self.assertRaises(APSError): validate_f3z(self.path)
        for name in ['../part.f3d','C:/part.f3d','/part.f3d']:
            self.path.write_bytes(archive_bytes(name))
            with self.assertRaises(APSError): validate_f3z(self.path)
        with zipfile.ZipFile(self.path,'w') as z: z.writestr('one.f3d',b'cad')
        with self.assertRaisesRegex(APSError,'metadata'): validate_f3z(self.path)

    def test_crc_corruption_and_zip_size_limit(self):
        raw=archive_bytes().replace(b'CAD payload',b'BAD payload')
        self.path.write_bytes(raw)
        with self.assertRaisesRegex(APSError,'CRC'): validate_f3z(self.path)
        self.path.write_bytes(archive_bytes())
        with self.assertRaises(APSError): validate_f3z(self.path,max_unpacked=1)

    def test_http_blocks_source_writes_and_unsafe_urls(self):
        for method,url in [('DELETE',API+'/data/v1/projects/project/downloads/job1'),
                           ('POST',API+'/data/v1/projects/project/items'),
                           ('PATCH',API+'/data/v1/projects/project/versions/v1'),
                           ('GET','http://developer.api.autodesk.com/data/v1/projects/p/jobs/j'),
                           ('GET','https://developer.api.autodesk.com.attacker.example/x')]:
            with self.assertRaises(APSError): check_route(method,url)
        for url in ['file:///tmp/secret','http://s3.amazonaws.com/file','https://attacker.example/file',
                    'https://developer.api.autodesk.com@attacker.example/file']:
            with self.assertRaises(APSError): download_url(url)

    def test_http_failure_redacts_response_body_and_token(self):
        err=urllib.error.HTTPError(API+'/authentication/v2/token',400,'bad',{},io.BytesIO(b'SECRET'))
        http=HTTP(Opener([err]))
        with self.assertRaises(APSError) as ctx:
            http.json('POST',API+'/authentication/v2/token',body={'code':'SECRET'},form=True)
        self.assertNotIn('SECRET',str(ctx.exception))
        self.assertEqual(ctx.exception.status,400)

    def test_http_storage_redirect_never_gets_bearer(self):
        opener=Opener([Response(b'',302,{'Location':'https://next.s3.amazonaws.com/data'}),
                       Response(b'abc',200,{'Content-Length':'3'})])
        HTTP(opener).download('https://bucket.s3.amazonaws.com/file',self.path,lambda:None)
        self.assertEqual(self.path.read_bytes(),b'abc')
        self.assertTrue(all(not req.has_header('Authorization') for req in opener.requests))
        opener=Opener([Response(b'',302,{'Location':'https://attacker.example/data'})])
        with self.assertRaises(APSError):
            HTTP(opener).download('https://bucket.s3.amazonaws.com/file',self.root/'other',lambda:None)
        self.assertEqual(len(opener.requests),1)

    def test_truncated_download_and_error_page_rejected(self):
        opener=Opener([Response(b'abc',200,{'Content-Length':'9'})])
        with self.assertRaisesRegex(APSError,'incomplete'):
            HTTP(opener).download('https://bucket.s3.amazonaws.com/file',self.path,lambda:None)

    def test_only_explicit_401_retries_no_network_post_retry(self):
        original=self.http.json
        attempts=[0]
        def unauthorized_once(method,url,*args,**kwargs):
            if method=='POST':
                attempts[0]+=1
                if attempts[0]==1: raise APSError('expired',401)
            return original(method,url,*args,**kwargs)
        self.http.json=unauthorized_once
        self.export()
        self.assertIn(True,self.tokens.forced)
        self.assertEqual(attempts[0],2)
        self.path.unlink()
        def offline(method,url,*args,**kwargs):
            if method=='POST': raise APSError('connection lost')
            return original(method,url,*args,**kwargs)
        self.http.json=offline
        with self.assertRaisesRegex(APSError,'connection lost'): self.export()

    def test_engine_cloud_enabled_preserves_step_when_archive_fails(self):
        adapter=Adapter([self.parent])
        def output(source,fmt,path):
            adapter.calls.append((source.key(),fmt))
            if fmt=='f3z': raise APSError('cloud is offline')
            path.write_bytes(b'STEP')
        adapter.export=output
        result=Backup(self.root,adapter,{'cloud_f3z':True}).run([])
        self.assertEqual(result['failed'],1)
        entries=Store(self.root).data['files']
        self.assertEqual([e['format'] for e in entries],['step'])
        self.assertEqual([c[1] for c in adapter.calls],['step','f3z'])
        self.assertFalse((self.root/'.backup.lock').exists())

    def test_engine_records_archive_without_claiming_restored_and_skips_verified(self):
        adapter=Adapter([self.parent])
        def output(source,fmt,path):
            adapter.calls.append((source.key(),fmt))
            path.write_bytes(archive_bytes() if fmt=='f3z' else b'STEP')
        adapter.export=output
        settings={'cloud_f3z':True}
        result=Backup(self.root,adapter,settings).run([])
        self.assertEqual(result['status'],'PARTIAL')  # Restoration remains manual.
        self.assertIn('F3Z downloaded',result['manual'][0]['reason'])
        entry=next(e for e in Store(self.root).data['files'] if e['format']=='f3z')
        self.assertFalse(entry['restore_tested'])
        self.assertEqual(entry['archive_origin'],'aps_downloads')
        adapter.calls=[]
        result=Backup(self.root,adapter,settings).run([])
        self.assertEqual(adapter.calls,[])
        self.assertEqual(result['skipped'],1)
        self.assertIn('F3Z downloaded',result['manual'][0]['reason'])

    def test_cancel_at_archive_publication_keeps_prior_step(self):
        adapter=Adapter([self.parent]); stop=[False]
        def activity(event):
            if event['phase']=='publishing' and event['format']=='f3z': stop[0]=True
        result=Backup(self.root,adapter,{'cloud_f3z':True},cancelled=lambda:stop[0],activity=activity).run([])
        self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual([e['format'] for e in Store(self.root).data['files']],['step'])


    def test_get_transient_failures_retry_but_post_does_not(self):
        original=self.http.json
        calls=[]
        def busy_get(method,url,*args,**kwargs):
            calls.append(method)
            if len(calls)==1: raise APSError('busy',429)
            return original(method,url,*args,**kwargs)
        self.http.json=busy_get
        self.export()
        self.assertEqual(calls[:2],['GET','GET'])
        attempts=[]
        def busy_post(method,url,*args,**kwargs):
            attempts.append(method)
            if method=='POST': raise APSError('busy',503)
            return original(method,url,*args,**kwargs)
        self.http.json=busy_post
        with self.assertRaises(APSError): self.export()
        self.assertEqual(attempts.count('POST'),1)


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=os.environ.get('FBM_TEST_TMP'))
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'tokens.json'
        self.cipher=lambda data,decrypt=False:bytes(b^0xAA for b in data)
        self.store=TokenStore(self.path,self.cipher)
        self.credentials={'access_token':'TOP-SECRET','refresh_token':'REFRESH-SECRET',
                          'expires_in':3600,'scope':SCOPES}
        self.logins=[]
        def login(*args): self.logins.append(True); return dict(self.credentials)
        self.tokens=Tokens('public-client-id',self.store,login=login,clock=lambda:100)

    def test_pkce_s256_and_state_validation(self):
        v,c=pkce_pair()
        self.assertEqual(c,base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip('='))
        self.assertGreaterEqual(len(v),43)
        self.assertNotEqual(pkce_pair()[0],v)
        self.assertEqual(callback_code('/callback?state=nonce&code=abc%2Bdef','nonce'),'abc+def')
        for path in ['/callback?state=wrong&code=x','/callback?code=x',
                     '/callback?state=nonce&error=access_denied','/wrong?state=nonce&code=x',
                     '/callback?state=nonce&code=x&code=y']:
            with self.assertRaises(APSError): callback_code(path,'nonce')

    def test_token_store_encrypted_bound_to_client_and_scopes(self):
        self.assertEqual(self.tokens.access(lambda:None),'TOP-SECRET')
        disk=self.path.read_text(encoding='utf-8')
        self.assertNotIn('TOP-SECRET',disk); self.assertNotIn('REFRESH-SECRET',disk)
        self.assertEqual(self.store.load('other'),{})
        second=Tokens('public-client-id',self.store,login=lambda *args:self.fail('unneeded login'),clock=lambda:100)
        self.assertEqual(second.access(lambda:None),'TOP-SECRET')
        self.assertEqual(len(self.logins),1)

    def test_refresh_rotates_tokens_and_no_secret_is_sent(self):
        self.tokens.access(lambda:None)
        class Refresh:
            def json(inner,method,url,token=None,body=None,form=False):
                self.assertNotIn('client_secret',body)
                self.assertEqual(body['scope'],SCOPES)
                self.assertTrue(form)
                return 200,{},dict(self.credentials,access_token='NEW',refresh_token='ROTATED')
        self.tokens.http=Refresh()
        self.assertEqual(self.tokens.access(lambda:None,force=True),'NEW')
        self.assertEqual(self.store.load('public-client-id')['refresh_token'],'ROTATED')

    def test_declined_scopes_and_corrupt_store_fail_closed(self):
        self.credentials['scope']='data:read'
        with self.assertRaisesRegex(APSError,'scopes'): self.tokens.access(lambda:None)
        self.assertFalse(self.path.exists())
        self.path.write_text('{broken')
        with self.assertRaises(APSError): self.store.load('public-client-id')

    def test_login_cancellation_before_any_browser(self):
        def stop(): raise Cancelled()
        with self.assertRaises(Cancelled): self.tokens.access(stop)
        self.assertEqual(self.logins,[])

    @unittest.skipUnless(os.name=='nt','Windows-only credential protection')
    def test_windows_dpapi_roundtrip_and_protected_storage(self):
        plain=b'UNIT-TEST-NOT-A-REAL-TOKEN'
        cipher=dpapi(plain)
        self.assertNotIn(plain,cipher)
        self.assertEqual(dpapi(cipher,True),plain)
        actual=TokenStore(self.path)
        actual.save(dict(client_id='client',scopes=SCOPES,access_token=plain.decode()))
        self.assertEqual(actual.load('client')['access_token'],plain.decode())

    def test_browser_pkce_flow_ignores_wrong_state_and_closes_callback(self):
        captured={}
        events=[]
        class Server:
            def __init__(inner,address,handler):
                self.assertEqual(address,('127.0.0.1',8765))
                inner.handler=handler; inner.requests=0
                captured['server']=inner
            def __enter__(inner): return inner
            def __exit__(inner,*args): captured['closed']=True
            def handle_request(inner):
                state=captured['query']['state'][0] if inner.requests else 'wrong-state'
                inner.requests+=1
                request=object.__new__(inner.handler)
                request.path='/callback?'+urllib.parse.urlencode({'state':state,'code':'ONE-TIME-CODE'})
                request.send_response=lambda status:events.append(status)
                request.send_header=lambda *args:None
                request.end_headers=lambda:None
                request.wfile=io.BytesIO()
                inner.handler.do_GET(request)
        def browser(url):
            captured['query']=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            return True
        class AuthHTTP:
            def json(inner,method,url,**kwargs):
                body=kwargs['body']
                challenge=base64.urlsafe_b64encode(hashlib.sha256(body['code_verifier'].encode()).digest()).decode().rstrip('=')
                self.assertEqual(challenge,captured['query']['code_challenge'][0])
                self.assertEqual(body['code'],'ONE-TIME-CODE')
                self.assertEqual(body['redirect_uri'],REDIRECT_URI)
                self.assertNotIn('client_secret',body)
                self.assertEqual(captured['query']['scope'],[SCOPES])
                return 200,{},self.credentials
        with patch('FusionBackupManager.aps.LoopbackServer',Server):
            result=browser_login('public-client-id',AuthHTTP(),lambda:None,browser=browser)
        self.assertEqual(result,self.credentials)
        self.assertTrue(captured['closed'])
        self.assertEqual(events,[400,200])

    def test_browser_signin_cancel_releases_callback_port(self):
        closed=[]
        class Server:
            def __init__(inner,*args): pass
            def __enter__(inner): return inner
            def __exit__(inner,*args): closed.append(True)
        def cancel(): raise Cancelled()
        with patch('FusionBackupManager.aps.LoopbackServer',Server):
            with self.assertRaises(Cancelled):
                browser_login('public-client-id',None,cancel,browser=lambda url:True)
        self.assertEqual(closed,[True])

    def test_failed_signin_is_not_repeated_for_every_assembly(self):
        self.credentials['scope']='data:read'
        with self.assertRaises(APSError): self.tokens.access(lambda:None)
        with self.assertRaisesRegex(APSError,'this run'): self.tokens.access(lambda:None)
        self.assertEqual(len(self.logins),1)
