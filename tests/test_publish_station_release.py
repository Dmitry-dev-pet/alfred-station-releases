import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import publish_station_release as p


def fake_elf():
    b=bytearray(100)
    b[:7]=b'\x7fELF\x01\x01\x01'
    struct.pack_into('<H',b,16,2)
    struct.pack_into('<H',b,18,40)
    struct.pack_into('<I',b,20,1)
    struct.pack_into('<I',b,28,52)
    struct.pack_into('<I',b,36,5<<24)
    struct.pack_into('<H',b,40,52)
    struct.pack_into('<H',b,42,32)
    struct.pack_into('<H',b,44,1)
    struct.pack_into('<I',b,52,1)
    return bytes(b)


def fixture(data=None,part_mutation=None,source_pr=55):
    data=data or fake_elf()
    sha=hashlib.sha256(data).hexdigest()
    txt=base64.b64encode(data)
    parts=[txt[i:i+p.BLOCK_CHARS] for i in range(0,len(txt),p.BLOCK_CHARS)]
    manifest={'schema':'alfred-station-staging/v1','state':'unpublished','repository':p.REPO,
      'source':'Dmitry-dev-pet/alfred-station','source_pr':source_pr,'compiler':'go1.27.4',
      'architecture':'linux/armv7-eabi5-static','asset':p.ASSET,'asset_sha256':sha,
      'asset_bytes':len(data),'transfer_encoding':'base64-text',
      'chunk_chars':p.BLOCK_CHARS,'total_base64_chars':len(txt),
      'chunks':[{'path':f'payload/part-{i:02}.b64','git_blob_sha':p.git_blob_sha(part),'chars':len(part)} for i,part in enumerate(parts)]}
    files={part['path']:block for part,block in zip(manifest['chunks'],parts)}
    if part_mutation:part_mutation(files,manifest)
    files['STAGING_MANIFEST.json']=json.dumps(manifest).encode()
    spec={'tag':'v2026.11.16','commit':'9d1526a7c0d4ed5be17782066dfef8f36a66c632',
        'sha256':sha,'size':len(data)}
    return data, files, spec


class Reader:
    def __init__(self,files):self.files=files;self.calls=[]
    def read(self,commit,path):
        assert commit=='9d1526a7c0d4ed5be17782066dfef8f36a66c632'
        self.calls.append(path)
        return self.files[path]


def event(opts=None):
    e={'repository':{'full_name':'Dmitry-dev-pet/alfred-station-releases'},
       'sender':{'login':'Dmitry-dev-pet'},'ref':'refs/heads/main',
       'inputs':{'release':'v2026.11.16',
                 'confirmation':p.CONFIRMATION}}
    if opts:
        for key,value in opts.items():
            if key=='user':e['sender']['login']=value
            elif key=='release':e['inputs']['release']=value
            elif key=='confirmation':e['inputs']['confirmation']=value
            elif key=='extra_input':e['inputs']['path']=value
            elif key=='repo':e['repository']['full_name']=value
            else:e[key]=value
    return json.dumps(e).encode()

class FakePublisher:
    def __init__(self,fail_digest=False,old_tag=False,fail_manifest=False,missing_manifest=False,
                 extra_asset=False,patch_failure=None,fail_final_get=False,
                 existing_draft_tag=False,list_full_pages=False):
        self.calls=[]
        self.fail_digest=fail_digest
        self.old_tag=old_tag
        self.fail_manifest=fail_manifest
        self.missing_manifest=missing_manifest
        self.extra_asset=extra_asset
        self.patch_failure=patch_failure
        self.fail_final_get=fail_final_get
        self.existing_draft_tag=existing_draft_tag
        self.list_full_pages=list_full_pages
        self.draft=True
        self.draft_tag=None
        self.assets=[]
        self.manifest_body=None

    def json_request(self,method,path,payload=None,*,absent_ok=False):
        self.calls.append((method,path))
        if method=='GET' and '/releases?per_page=100&page=' in path:
            if self.existing_draft_tag:
                return [{'tag_name':'v2026.11.16','draft':True}]
            if self.list_full_pages:
                return [{'tag_name':'v1.0.0'}]*100
            return []
        if method=='GET' and ('/releases/tags/' in path or '/git/ref/tags/' in path):
            if self.old_tag and path.endswith('/v2026.11.16'):
                return {'id':1}
            return None
        if method=='POST':
            assert payload['draft'] is True and payload['prerelease'] is False
            self.draft_tag=payload['tag_name']
            return {'id':555,'draft':True,'prerelease':False}
        if method=='PATCH':
            assert payload=={'tag_name':self.draft_tag,'draft':False,'prerelease':False}
            if self.patch_failure=='before':
                raise p.Refused('github_http_403')
            self.draft=False
            if self.patch_failure=='after':
                raise p.Refused('github_api_unavailable')
            return {'id':555,'draft':False,'prerelease':False}
        if method=='GET' and path.endswith('/releases/555') and not self.draft and self.fail_final_get:
            raise p.Refused('github_api_unavailable')
        assets=list(self.assets)
        if self.extra_asset:assets.append({'name':'unexpected','size':1,'digest':'sha256:'+'0'*64})
        return {'id':555,'draft':self.draft,'prerelease':False,'tag_name':self.draft_tag,'assets':assets}

    def upload(self,release_id,path,asset_name):
        assert release_id==555
        assert asset_name in (p.ASSET,p.MANIFEST_ASSET)
        self.calls.append(('UPLOAD',asset_name))
        data=path.read_bytes()
        digest=hashlib.sha256(data).hexdigest()
        if asset_name==p.MANIFEST_ASSET:
            self.manifest_body=data
        if self.fail_digest and asset_name==p.ASSET:
            digest='0'*64
        if self.fail_manifest and asset_name==p.MANIFEST_ASSET:
            digest='0'*64
        asset={'name':asset_name,'size':len(data),'digest':'sha256:'+digest}
        if not (self.missing_manifest and asset_name==p.MANIFEST_ASSET):
            self.assets.append(asset)
        return asset


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.original_releases=p.RELEASES
        p.RELEASES={**self.original_releases,
            'v2026.11.16':{'commit':'9d1526a7c0d4ed5be17782066dfef8f36a66c632',
                'sha256':hashlib.sha256(fake_elf()).hexdigest(),'size':len(fake_elf())}}

    def tearDown(self):
        p.RELEASES=self.original_releases

    def test_event_schema_and_actor(self):
        good=p.parse_event(event())
        self.assertEqual(good['tag'],'v2026.11.16')
        self.assertEqual(good['size'],100)
        for bad in [{'user':'attacker'},{'release':'../oops'},{'confirmation':'INVALID'},
                    {'extra_input':'https://attacker.test'},{'ref':'refs/heads/feature'},
                    {'repo':'attacker/repo'},{'sender':{}},{'inputs':None}]:
            with self.subTest(bad=bad),self.assertRaises(p.Refused):
                p.parse_event(event(bad))

    def test_verifies_one_arm_elf_without_publish(self):
        data,files,spec=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'verified'
            result=p.verify_staging(Reader(files),spec,f)
            self.assertEqual(f.read_bytes(),data)
            self.assertEqual(result['sha256'],spec['sha256'])
            self.assertEqual(f.stat().st_mode & 0o777,0o600)

    def test_bad_manifest_or_chunk_rejected_and_file_removed(self):
        def changes(files,m):files[m['chunks'][0]['path']]=b'AAAA'
        for mutator in [changes,
             lambda files,m:m.update({'asset':'different'}),
             lambda files,m:m.update({'source_pr':'untrusted'}),
             lambda files,m:m['chunks'][0].update({'path':'../../danger'})]:
            _,files,spec=fixture(part_mutation=mutator)
            with tempfile.TemporaryDirectory() as tmp:
                f=Path(tmp)/'candidate'
                with self.assertRaises(p.Refused):p.verify_staging(Reader(files),spec,f)
                self.assertFalse(f.exists())

    def test_verified_old_and_new_pr_provenance_metadata(self):
        # Current PR52 baseline and PR60 updater use identical fixed staging
        # schema. The PR number is provenance; release permission remains
        # bound to the owner-created issue and exact SHA-256/staging commit.
        for pr in (52, 60):
            with self.subTest(pr=pr):
                data,files,spec=fixture(source_pr=pr)
                with tempfile.TemporaryDirectory() as tmp:
                    out=Path(tmp)/'asset'
                    result=p.verify_staging(Reader(files),spec,out)
                    self.assertEqual(result['sha256'],spec['sha256'])
                    self.assertEqual(out.read_bytes(),data)
        for invalid in (True,0,-1,100001,None,'60'):
            with self.subTest(invalid=invalid):
                _,files,spec=fixture(source_pr=invalid)
                with tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(p.Refused):
                        p.verify_staging(Reader(files),spec,Path(tmp)/'asset')

    def test_wrong_arch_rejected(self):
        data=bytearray(fake_elf());data[18:20]=(62).to_bytes(2,'little')
        _,files,spec=fixture(bytes(data))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(p.Refused):p.verify_staging(Reader(files),spec,Path(tmp)/'verified')

    def test_publish_stable_binary_and_manifest_with_verified_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            art=Path(tmp)/'verified';art.write_bytes(fake_elf())
            sha=hashlib.sha256(fake_elf()).hexdigest()
            pub=FakePublisher()
            out=p.publish(pub,'v2026.11.16',100,sha,art)
            self.assertEqual(out['state'],'PUBLISHED')
            self.assertEqual(out['manifest_sha256'],hashlib.sha256(pub.manifest_body).hexdigest())
            self.assertEqual(pub.manifest_body,p.stable_manifest('v2026.11.16',100,sha))
            self.assertEqual(pub.manifest_body.decode(),
                f'ALFRED_STATION_STABLE_V1\ntag=v2026.11.16\nsize=100\nsha256={sha}\n')
            self.assertEqual([x[0] for x in pub.calls],['GET','GET','GET','POST','UPLOAD','UPLOAD','GET','GET','PATCH','GET'])
            self.assertEqual([x[1] for x in pub.calls if x[0]=='UPLOAD'],[p.ASSET,p.MANIFEST_ASSET])
            self.assertEqual(pub.draft,False)
            self.assertEqual(list(Path(tmp).iterdir()),[art])

    def test_multichunk_reconstruction_and_later_chunk_cleanup(self):
        # At least two Git staging chunks, beyond 393216 decoded bytes.
        data=fake_elf()+b'X'*(p.BLOCK_CHARS*3//4+37)
        original,files,spec=fixture(data)
        self.assertGreater(len(json.loads(files['STAGING_MANIFEST.json'])['chunks']),1)
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'verified'
            result=p.verify_staging(Reader(files),spec,output)
            self.assertEqual(result['parts'],2)
            self.assertEqual(output.read_bytes(),original)
        def corrupt_later(files,manifest):
            files[manifest['chunks'][1]['path']]=b'AAAA'
        _,files,spec=fixture(data,part_mutation=corrupt_later)
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'verified'
            with self.assertRaises(p.Refused):
                p.verify_staging(Reader(files),spec,output)
            self.assertFalse(output.exists())

    def test_publish_lost_patch_response_reconciles_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'elf'; artifact.write_bytes(fake_elf())
            digest=hashlib.sha256(fake_elf()).hexdigest()
            publisher=FakePublisher(patch_failure='after')
            result=p.publish(publisher,'v2026.11.16',100,digest,artifact)
            self.assertEqual(result['state'],'PUBLISHED')
            self.assertEqual([x[0] for x in publisher.calls].count('PATCH'),1)
            self.assertEqual([x[0] for x in publisher.calls].count('GET'),6)

    def test_publish_unconfirmed_after_patch_is_explicitly_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'elf'; artifact.write_bytes(fake_elf())
            digest=hashlib.sha256(fake_elf()).hexdigest()
            for options in ({'patch_failure':'before'},{'fail_final_get':True},
                            {'patch_failure':'after','fail_final_get':True}):
                with self.subTest(options=options):
                    publisher=FakePublisher(**options)
                    with self.assertRaises(p.PublicationUncertain) as raised:
                        p.publish(publisher,'v2026.11.16',100,digest,artifact)
                    self.assertEqual((raised.exception.release_id,raised.exception.tag),
                                     (555,'v2026.11.16'))
                    self.assertEqual([x[0] for x in publisher.calls].count('PATCH'),1)
                    self.assertEqual([x[0] for x in publisher.calls].count('POST'),1)

    def test_corrupted_or_missing_assets_refuse_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            art=Path(tmp)/'verified';art.write_bytes(fake_elf())
            sha=hashlib.sha256(fake_elf()).hexdigest()
            for opts in [{'fail_digest':True},{'fail_manifest':True},
                         {'missing_manifest':True},{'extra_asset':True}]:
                with self.subTest(opts=opts):
                    bad=FakePublisher(**opts)
                    with self.assertRaises(p.Refused):p.publish(bad,'v2026.11.16',100,sha,art)
                    self.assertNotIn('PATCH',[x[0] for x in bad.calls])
                    self.assertEqual([x for x in Path(tmp).iterdir()],[art])
            exists=FakePublisher(old_tag=True)
            with self.assertRaises(p.Refused):p.publish(exists,'v2026.11.16',100,sha,art)
            self.assertNotIn('POST',[x[0] for x in exists.calls])

    def test_existing_draft_and_truncated_release_list_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'elf'; artifact.write_bytes(fake_elf())
            sha=hashlib.sha256(fake_elf()).hexdigest()
            for flags,expected in (
                ({'existing_draft_tag':True},'release_already_exists'),
                ({'list_full_pages':True},'release_list_truncated'),
            ):
                with self.subTest(flags=flags):
                    publisher=FakePublisher(**flags)
                    with self.assertRaises(p.Refused) as raised:
                        p.publish(publisher,'v2026.11.16',100,sha,artifact)
                    self.assertEqual(str(raised.exception),expected)
                    self.assertNotIn('POST',[x[0] for x in publisher.calls])
                    self.assertNotIn('PATCH',[x[0] for x in publisher.calls])
            # Listing query cannot be manipulated or used for other hosts/paths.
            client=p.GitHubPublish('test-token-never-used')
            for invalid in (
                f'/repos/{p.REPO}/releases?per_page=101&page=1',
                f'/repos/{p.REPO}/releases?per_page=100&page=11',
                f'/repos/{p.REPO}/releases?per_page=100&page=1&after=xyz',
                f'/repos/{p.REPO}/contents?per_page=100&page=1',
            ):
                with self.subTest(invalid=invalid),self.assertRaises(p.Refused):
                    client.json_request('GET',invalid)

    def test_candidate_fallback_removed_before_patch_remains_draft(self):
        sha=hashlib.sha256(fake_elf()).hexdigest()
        p.RELEASES['v2026.11.17']={
            'commit':'6b134118aeb21286e773eb87c63ecdfcf43ee6d5',
            'sha256':sha,'size':100}
        class VanishingFallback(FakePublisher):
            def __init__(self):
                super().__init__()
                self.fallback_reads=0
            def json_request(self,method,path,payload=None,*,absent_ok=False):
                if method=='GET' and path.endswith('/releases/tags/v2026.11.16'):
                    self.calls.append((method,path))
                    self.fallback_reads+=1
                    if self.fallback_reads>1:
                        return None
                    old=p.RELEASES['v2026.11.16']
                    manifest=p.stable_manifest('v2026.11.16',old['size'],old['sha256'])
                    return {
                        'tag_name':'v2026.11.16','draft':False,'prerelease':False,
                        'assets':[
                            {'name':p.ASSET,'size':old['size'],
                             'digest':'sha256:'+old['sha256']},
                            {'name':p.MANIFEST_ASSET,'size':len(manifest),
                             'digest':'sha256:'+hashlib.sha256(manifest).hexdigest()}]}
                return super().json_request(method,path,payload,absent_ok=absent_ok)
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'elf';artifact.write_bytes(fake_elf())
            publisher=VanishingFallback()
            with self.assertRaises(p.Refused) as raised:
                p.publish(publisher,'v2026.11.17',100,sha,artifact)
            self.assertEqual(str(raised.exception),'fallback_release_missing_or_unstable')
            self.assertEqual(publisher.fallback_reads,2)
            self.assertIn('POST',[x[0] for x in publisher.calls])
            self.assertNotIn('PATCH',[x[0] for x in publisher.calls])
            self.assertTrue(publisher.draft)


    def test_candidate_published_with_its_own_selected_tag(self):
        # Exercise the candidate PATCH, not just the baseline or rejected candidates.
        digest=hashlib.sha256(fake_elf()).hexdigest()
        p.RELEASES['v2026.11.17']={
            'commit':'6b134118aeb21286e773eb87c63ecdfcf43ee6d5',
            'sha256':digest,'size':100}
        class VerifiedFallback(FakePublisher):
            def __init__(self):
                super().__init__()
                self.fallback_reads=0
            def json_request(self,method,path,payload=None,*,absent_ok=False):
                if method=='GET' and path.endswith('/releases/tags/v2026.11.16'):
                    self.calls.append((method,path))
                    self.fallback_reads+=1
                    old=p.RELEASES['v2026.11.16']
                    manifest=p.stable_manifest('v2026.11.16',old['size'],old['sha256'])
                    return {'tag_name':'v2026.11.16','draft':False,'prerelease':False,
                        'assets':[
                            {'name':p.ASSET,'size':old['size'],'digest':'sha256:'+old['sha256']},
                            {'name':p.MANIFEST_ASSET,'size':len(manifest),
                             'digest':'sha256:'+hashlib.sha256(manifest).hexdigest()}]}
                return super().json_request(method,path,payload,absent_ok=absent_ok)
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'elf';artifact.write_bytes(fake_elf())
            pub=VerifiedFallback()
            result=p.publish(pub,'v2026.11.17',100,digest,artifact)
            self.assertEqual(result['tag'],'v2026.11.17')
            self.assertEqual(pub.draft_tag,'v2026.11.17')
            self.assertEqual(pub.fallback_reads,2)
            self.assertEqual([method for method,_ in pub.calls].count('PATCH'),1)

    def test_changed_draft_after_fallback_check_stays_unpublished(self):
        class ChangedBeforePatch(FakePublisher):
            def __init__(self):
                super().__init__()
                self.draft_gets=0
            def json_request(self,method,path,payload=None,*,absent_ok=False):
                response=super().json_request(method,path,payload,absent_ok=absent_ok)
                if method=='GET' and path.endswith('/releases/555'):
                    self.draft_gets+=1
                    if self.draft_gets==2:
                        return {**response,'assets':response['assets'][:-1]}
                return response
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'elf';artifact.write_bytes(fake_elf())
            pub=ChangedBeforePatch()
            with self.assertRaises(p.Refused):
                p.publish(pub,'v2026.11.16',100,hashlib.sha256(fake_elf()).hexdigest(),artifact)
            self.assertEqual(pub.draft_gets,2)
            self.assertEqual([method for method,_ in pub.calls].count('PATCH'),0)

    def test_publish_deadline_before_patch_leaves_explicit_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'elf';artifact.write_bytes(fake_elf())
            pub=FakePublisher()
            with self.assertRaises(p.DraftLeftUnpublished) as ctx:
                p.publish(pub,'v2026.11.16',100,hashlib.sha256(fake_elf()).hexdigest(),
                          artifact,deadline_epoch=100,clock=lambda:30)
            self.assertEqual((ctx.exception.release_id,ctx.exception.tag),
                             (555,'v2026.11.16'))
            self.assertEqual([method for method,_ in pub.calls].count('PATCH'),0)
            self.assertTrue(pub.draft)

    def test_new_candidate_refuses_without_matching_published_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            art=Path(tmp)/'elf';art.write_bytes(fake_elf())
            digest=hashlib.sha256(fake_elf()).hexdigest()
            p.RELEASES['v2026.11.17']={'commit':'6b134118aeb21286e773eb87c63ecdfcf43ee6d5',
                'sha256':digest,'size':100}
            pub=FakePublisher()
            with self.assertRaises(p.Refused):
                p.publish(pub,'v2026.11.17',100,digest,art)
            self.assertNotIn('POST',[x[0] for x in pub.calls])

    def test_stable_manifest_only_accepts_fixed_tag_digest_and_size(self):
        sha=hashlib.sha256(fake_elf()).hexdigest()
        self.assertEqual(len(p.stable_manifest('v2026.11.16',100,sha).splitlines()),4)
        for bad in [('v2026.11.16.1',100,sha),('v2026.11.16',0,sha),
                    ('v2026.11.16',100,'a'*62),('v2026.11.16',True,sha)]:
            with self.subTest(bad=bad),self.assertRaises(p.Refused):
                p.stable_manifest(*bad)

    def test_prior_verify_required_and_existing_destination_refused(self):
        _,files,spec=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'verified';f.write_bytes(b'existing')
            with self.assertRaises(p.Refused):p.verify_staging(Reader(files),spec,f)

    def test_read_allowlist_strict(self):
        reader=p.GitHubRead()
        for path in ['../etc/shadow','payload/part-100.b64','https://host/payload/part-00.b64','foo']:
            with self.assertRaises(p.Refused):reader.read('9d1526a7c0d4ed5be17782066dfef8f36a66c632',path)

if __name__=='__main__':unittest.main()
