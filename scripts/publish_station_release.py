#!/usr/bin/env python3
"""Owner-only fixed-target GitHub Release publisher using repository GITHUB_TOKEN.
No external App, PAT, private-source checkout, Station access, or compilation.
Both immutable SHA-pinned versions must be verified before a Release is written.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import stat
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = 'Dmitry-dev-pet/alfred-station-releases'
OWNER = 'Dmitry-dev-pet'
ASSET = 'station-armv7'
MANIFEST_ASSET = 'station-armv7.manifest'
CONFIRMATION = 'HARDWARE-VERIFIED-PUBLISH'
RELEASES = {
 'v2026.11.16': {'commit':'9d1526a7c0d4ed5be17782066dfef8f36a66c632', 'size':5963900, 'sha256':'71c544e083fee96487103ed60e51b92bf4844d8f2b42a3d9ff9f86464dc1e528'},
 'v2026.11.17': {'commit':'6b134118aeb21286e773eb87c63ecdfcf43ee6d5', 'size':6357116, 'sha256':'8b7b5b6ddc9057662f5d29125c01f8db24d16bb55eded94ac57ba666610165f0'},
}
LIMIT = 7 * 1024 * 1024
BLOCK_CHARS = 524288
CHUNKS_MAX = 20
BLOB_PREFIX = 'payload/part-'
STAGED_NAME = 'station-armv7.verified'
TAG_RE = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9][a-zA-Z0-9.-]{0,39})?\Z')
SHA_RE = re.compile(r'[0-9a-f]{64}\Z')
COMMIT_RE = re.compile(r'[0-9a-f]{40}\Z')

class Refused(Exception):
    """Safe nonsecret error codes only; never surface raw exceptions."""


class PublicationUncertain(Exception):
    """Public release may exist; operator must inspect tag/id, never retry."""

    def __init__(self, release_id: int, tag: str):
        self.release_id = release_id
        self.tag = tag
        super().__init__('publication_requires_manual_readback')


def refuse(reason: str):
    raise Refused(reason)


def parse_event(raw: bytes):
    """Only the owner may select one of the two immutable built-in release pins."""
    if len(raw)>100000: refuse('event_size_refused')
    try:
        event=json.loads(raw)
        inputs=event['inputs']
        sender=event['sender']['login']
        repo=event['repository']['full_name']
        ref=event['ref']
    except (TypeError,ValueError,KeyError,IndexError):
        refuse('event_invalid')
    if (not isinstance(inputs,dict) or set(inputs)!={'release','confirmation'}
            or sender!=OWNER or repo!=REPO or ref!='refs/heads/main'
            or inputs.get('confirmation')!=CONFIRMATION
            or inputs.get('release') not in RELEASES):
        refuse('owner_or_request_refused')
    tag=inputs['release']
    return {'tag':tag,**RELEASES[tag]}

def limit_read(response, count=1000000):
    value = response.read(count + 1)
    if len(value) > count:
        refuse('response_too_large')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubRead:
    """Read immutable public blobs using at most the repository's Actions token.

    The optional token affects GitHub API rate limits, not the immutable
    source ref, allowed path, SHA1 Git blob pin or final SHA256 check.
    """
    def __init__(self, opener=None, token=''):
        if token and (not isinstance(token,str) or len(token)>4096 or
                      '\n' in token or '\r' in token):
            refuse('stage_read_token_invalid')
        self.opener = opener or urllib.request.build_opener(NoRedirect)
        self.token = token

    def read(self, ref: str, path: str) -> bytes:
        if not COMMIT_RE.fullmatch(ref) or not (path == 'STAGING_MANIFEST.json' or
                 re.fullmatch(r'payload/part-[0-9]{2}\.b64', path)):
            refuse('stage_path_refused')
        url = 'https://api.github.com/repos/' + REPO + '/contents/' + path + '?ref=' + ref
        headers={'Accept':'application/vnd.github+json',
            'X-GitHub-Api-Version':'2026-03-10', 'User-Agent':'Alfred-Station-Publisher-Read/1.0'}
        if self.token:
            headers['Authorization']='Bearer '+self.token
        request=urllib.request.Request(url,headers=headers)
        try:
            with self.opener.open(request, timeout=30) as res:
                if res.status != 200:
                    refuse('stage_http_refused')
                obj = json.loads(limit_read(res, 1_100_000))
            if (obj.get('type') != 'file' or obj.get('encoding') != 'base64' or
                    not isinstance(obj.get('content'), str) or obj.get('size', 0) > 600_000):
                refuse('stage_metadata_refused')
            return base64.b64decode(obj['content'], validate=False)
        except Refused:
            raise
        except (urllib.error.URLError, OSError, ValueError, binascii.Error):
            refuse('stage_fetch_refused')


def git_blob_sha(b: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(b)).encode() + b'\0' + b).hexdigest()


def inspect_elf(path: Path):
    with path.open('rb') as f:
        head=f.read(52)
        if len(head)<52 or head[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H',head,18)[0]!=40 or (struct.unpack_from('<I',head,36)[0] >> 24)!=5:
            refuse('not_armv7_eabi5')
        off=struct.unpack_from('<I',head,28)[0]
        step,n=struct.unpack_from('<HH',head,42)
        if step < 32 or n < 1 or off + n*step > path.stat().st_size:
            refuse('elf_program_headers_refused')
        f.seek(off)
        for _ in range(n):
            row=f.read(step)
            if len(row)<4 or struct.unpack_from('<I',row,0)[0]==3:
                refuse('elf_dynamic_loader_refused')


def verify_staging(reader, spec, output: Path):
    raw=reader.read(spec['commit'],'STAGING_MANIFEST.json')
    if len(raw)>12000:refuse('manifest_too_large')
    try:
        m=json.loads(raw)
    except (TypeError, ValueError):
        refuse('manifest_invalid')
    required={ 'schema':'alfred-station-staging/v1','repository':REPO,'state':'unpublished',
        'asset':ASSET,'asset_sha256':spec['sha256'],'asset_bytes':spec['size'],
        'transfer_encoding':'base64-text','chunk_chars':BLOCK_CHARS,'source':'Dmitry-dev-pet/alfred-station',
        'compiler':'go1.27.4','architecture':'linux/armv7-eabi5-static'}
    if not isinstance(m,dict) or any(m.get(k)!=v for k,v in required.items()):
        refuse('manifest_mismatch')
    # source_pr is provenance, NOT an authorization input. The true artifact
    # authority is the owner-created issue pinning immutable staging commit,
    # exact size and SHA-256. Accept future verified source PR numbers too.
    source_pr = m.get('source_pr')
    if type(source_pr) is not int or not (1 <= source_pr <= 100000):
        refuse('source_pr_metadata_invalid')
    parts=m.get('chunks')
    if not isinstance(parts,list) or len(parts)<1 or len(parts)>CHUNKS_MAX or not isinstance(m.get('total_base64_chars'),int) or m['total_base64_chars']!=4*((spec['size']+2)//3):
        refuse('manifest_chunk_count_refused')
    if output.exists() or output.is_symlink():
        refuse('artifact_exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    hash256=hashlib.sha256()
    written=0
    try:
        with output.open('xb') as w:
            output.chmod(0o600)
            for i,part in enumerate(parts):
                expected_name=f'payload/part-{i:02}.b64'
                if (not isinstance(part,dict) or set(part)!={'path','git_blob_sha','chars'} or
                        part['path']!=expected_name or not isinstance(part['git_blob_sha'],str) or
                        not COMMIT_RE.fullmatch(part['git_blob_sha']) or type(part['chars']) is not int or
                        part['chars']<=0 or part['chars']>BLOCK_CHARS or part['chars']%4!=0 or
                        (i<len(parts)-1 and part['chars']!=BLOCK_CHARS)):
                    refuse('manifest_chunk_refused')
                block=reader.read(spec['commit'],expected_name)
                if len(block)!=part['chars'] or git_blob_sha(block)!=part['git_blob_sha']:
                    refuse('chunk_corrupt')
                try:
                    decoded=base64.b64decode(block,validate=True)
                except binascii.Error:
                    refuse('chunk_base64_invalid')
                written+=len(decoded)
                if written>spec['size']:
                    refuse('too_many_bytes')
                w.write(decoded)
                hash256.update(decoded)
            w.flush()
            os.fsync(w.fileno())
        if written!=spec['size'] or hash256.hexdigest()!=spec['sha256']:
            refuse('sha256_or_size_mismatch')
        inspect_elf(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {'bytes':written,'sha256':hash256.hexdigest(),'parts':len(parts),'status':'VERIFIED'}


class GitHubPublish:
    """One fixed GitHub API repo and upload host; GITHUB_TOKEN is never logged."""
    def __init__(self, token: str):
        if not token or len(token)>4096 or '\n' in token or '\r' in token:
            refuse('publisher_token_unavailable')
        self.token=token

    def json_request(self, method:str, path:str, payload:dict|None=None, *, absent_ok=False):
        # Only one exact query is permitted: bounded, authenticated listing of
        # drafts and published Releases to prevent duplicate draft tag creation.
        list_query=re.fullmatch(r'/repos/'+re.escape(REPO)+
            r'/releases\?per_page=100&page=(?:[1-9]|10)',path)
        if (not path.startswith('/repos/'+REPO+'/') or '#' in path or
                ('?' in path and not (method=='GET' and list_query))):
            refuse('api_path_refused')
        data=json.dumps(payload,separators=(',',':')).encode() if payload is not None else None
        req=urllib.request.Request('https://api.github.com'+path,data=data,method=method,headers={
            'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
            'Content-Type':'application/json', 'X-GitHub-Api-Version':'2026-03-10',
            'User-Agent':'Alfred-Station-Publisher/1.0'})
        opener=urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=30) as res:
                if res.status not in (200,201):refuse('api_status_refused')
                return json.loads(limit_read(res,1000000))
        except urllib.error.HTTPError as ex:
            if absent_ok and ex.code==404:return None
            refuse('github_http_'+str(ex.code) if 400<=ex.code<=599 else 'github_http_refused')
        except Refused:
            raise
        except (urllib.error.URLError, OSError, ValueError):
            refuse('github_api_unavailable')

    def upload(self,release_id:int,path:Path,asset_name:str):
        if type(release_id) is not int or release_id<1:
            refuse('release_id_refused')
        if asset_name not in (ASSET, MANIFEST_ASSET):
            refuse('asset_name_refused')
        data=path.read_bytes()
        con=http.client.HTTPSConnection('uploads.github.com',timeout=90)
        try:
            con.request('POST',f'/repos/{REPO}/releases/{release_id}/assets?name={asset_name}',body=data,headers={
                'Authorization':'Bearer '+self.token,'Content-Type':'application/octet-stream',
                'Accept':'application/vnd.github+json','User-Agent':'Alfred-Station-Publisher/1.0',
                'X-GitHub-Api-Version':'2026-03-10', 'Content-Length':str(len(data))})
            res=con.getresponse()
            if res.status!=201:refuse('asset_upload_refused')
            return json.loads(limit_read(res,500000))
        except Refused:
            raise
        except (OSError,ValueError,http.client.HTTPException):
            refuse('asset_upload_uncertain')
        finally:
            con.close()


def stable_manifest(tag:str,size:int,digest:str)->bytes:
    """Derive, never accept from the issue or staging, the fixed stable manifest."""
    if not TAG_RE.fullmatch(tag) or type(size) is not int or not (1 <= size <= LIMIT) or not SHA_RE.fullmatch(digest):
        refuse('stable_manifest_spec_invalid')
    raw=(f'ALFRED_STATION_STABLE_V1\n'
         f'tag={tag}\n'
         f'size={size}\n'
         f'sha256={digest}\n').encode('ascii')
    if len(raw)>200:
        refuse('stable_manifest_too_large')
    return raw


def _exact_asset_set(assets,expected):
    if not isinstance(assets,list) or len(assets)!=len(expected):
        refuse('release_asset_set_refused')
    seen=set()
    for asset in assets:
        name=asset.get('name') if isinstance(asset,dict) else None
        if name not in expected or name in seen:
            refuse('release_asset_set_refused')
        seen.add(name)
        size,digest=expected[name]
        if asset.get('size')!=size or asset.get('digest')!='sha256:'+digest:
            refuse('release_asset_readback_refused')


def require_fallback_release(pub):
    """Check stable, SHA-matching old Release before approving an update Release."""
    old=RELEASES['v2026.11.16']
    prev=pub.json_request('GET',f'/repos/{REPO}/releases/tags/v2026.11.16',absent_ok=True)
    if (not isinstance(prev,dict) or prev.get('draft') is not False
            or prev.get('prerelease') is not False
            or prev.get('tag_name')!='v2026.11.16'):
        refuse('fallback_release_missing_or_unstable')
    content=stable_manifest('v2026.11.16',old['size'],old['sha256'])
    _exact_asset_set(prev.get('assets'),
        {ASSET:(old['size'],old['sha256']),
         MANIFEST_ASSET:(len(content),hashlib.sha256(content).hexdigest())})


def refuse_existing_draft_or_release(pub,tag:str):
    """Find existing draft as well as stable releases using the same auth token.

    GitHub's tag lookup omits drafts, and drafts need not yet create refs.
    Never silently re-create the same tag after an uncertain prior attempt.
    """
    for page in range(1,11):
        entries=pub.json_request('GET',
            f'/repos/{REPO}/releases?per_page=100&page={page}')
        if not isinstance(entries,list) or len(entries)>100:
            refuse('release_list_unverified')
        for entry in entries:
            if not isinstance(entry,dict) or not isinstance(entry.get('tag_name'),str):
                refuse('release_list_unverified')
            if entry['tag_name']==tag:
                refuse('release_already_exists')
        if len(entries)<100:
            return
    refuse('release_list_truncated')


def publish(pub,tag:str,size:int,digest:str,artifact:Path):
    if tag not in RELEASES or RELEASES[tag]['size']!=size or RELEASES[tag]['sha256']!=digest:
        refuse('release_not_allowlisted')
    if tag=='v2026.11.17':
        require_fallback_release(pub)
    # One ELF executable, one tiny fixed-name text manifest; both digest verified.
    manifest=stable_manifest(tag,size,digest)
    manifest_digest=hashlib.sha256(manifest).hexdigest()
    expected={ASSET:(size,digest),MANIFEST_ASSET:(len(manifest),manifest_digest)}
    if pub.json_request('GET',f'/repos/{REPO}/releases/tags/{tag}',absent_ok=True) is not None:
        refuse('release_already_exists')
    if pub.json_request('GET',f'/repos/{REPO}/git/ref/tags/{tag}',absent_ok=True) is not None:
        refuse('tag_already_exists')
    refuse_existing_draft_or_release(pub,tag)
    if artifact.stat().st_size!=size or hashlib.sha256(artifact.read_bytes()).hexdigest()!=digest:
        refuse('artifact_changed_since_verification')
    inspect_elf(artifact)
    # Never create a Release until *both* fixed assets are known from source.
    with tempfile.TemporaryDirectory(prefix='station-manifest-',dir=artifact.parent) as td:
        manifest_path=Path(td)/MANIFEST_ASSET
        manifest_path.write_bytes(manifest)
        manifest_path.chmod(0o600)
        if manifest_path.stat().st_size!=len(manifest) or hashlib.sha256(manifest_path.read_bytes()).hexdigest()!=manifest_digest:
            refuse('stable_manifest_changed')
        release=pub.json_request('POST',f'/repos/{REPO}/releases',{
            'tag_name':tag,'target_commitish':'main','name':'Alfred Station '+tag,
            'body':'One offline-built ARMv7 MCP tunnel-client. SHA-256: '+digest,
            'draft':True,'prerelease':False,'generate_release_notes':False})
        release_id=release.get('id')
        if type(release_id) is not int or release_id<1 or release.get('draft') is not True or release.get('prerelease') is not False:
            refuse('draft_creation_uncertain')
        # No retries after draft creation: any uncertainty leaves it unpublished.
        for name,path in ((ASSET,artifact),(MANIFEST_ASSET,manifest_path)):
            uploaded=pub.upload(release_id,path,name)
            count,sha=expected[name]
            if uploaded.get('name')!=name or uploaded.get('size')!=count or uploaded.get('digest')!='sha256:'+sha:
                refuse('upload_proof_missing')
        readback=pub.json_request('GET',f'/repos/{REPO}/releases/{release_id}')
        if (readback.get('draft') is not True or readback.get('prerelease') is not False or
                readback.get('tag_name') != tag):
            refuse('release_readback_refused')
        _exact_asset_set(readback.get('assets'),expected)
        # The fallback may be removed or altered while this candidate uploads.
        # Recheck it immediately before making the candidate public.
        if tag=='v2026.11.17':
            require_fallback_release(pub)
        # GitHub may apply PATCH before its response is lost. Never call PATCH
        # twice. One read-only GET can positively reconcile a lost response.
        try:
            pub.json_request('PATCH',f'/repos/{REPO}/releases/{release_id}',
                {'tag_name':tag,'draft':False,'prerelease':False})
        except Exception:
            # This includes a lost response after GitHub committed publication.
            # We do not claim failure or attempt a second publication.
            pass
        try:
            result=pub.json_request('GET',f'/repos/{REPO}/releases/{release_id}')
            if (not isinstance(result,dict) or
                    result.get('id')!=release_id or
                    result.get('draft') is not False or
                    result.get('prerelease') is not False or
                    result.get('tag_name')!=tag):
                raise PublicationUncertain(release_id,tag)
            _exact_asset_set(result.get('assets'),expected)
        except Exception:
            raise PublicationUncertain(release_id,tag) from None
        return {'state':'PUBLISHED','release_id':release_id,'tag':tag,'bytes':size,
                'sha256':digest,'manifest_sha256':manifest_digest,'manifest_bytes':len(manifest)}


def main():
    artifact=None
    try:
        if len(sys.argv)!=2 or sys.argv[1] not in ('verify','publish'):
            refuse('mode_refused')
        if (os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch'
                or os.environ.get('GITHUB_REF')!='refs/heads/main'
                or os.environ.get('GITHUB_REPOSITORY')!=REPO
                or os.environ.get('GITHUB_ACTOR')!=OWNER
                or os.environ.get('GITHUB_TRIGGERING_ACTOR')!=OWNER):
            refuse('workflow_context_refused')
        event_file=Path(os.environ['GITHUB_EVENT_PATH'])
        if not event_file.is_file() or event_file.stat().st_size>100000:
            refuse('event_file_refused')
        spec=parse_event(event_file.read_bytes())
        artifact=Path(os.environ['RUNNER_TEMP'])/STAGED_NAME
        # Both jobs reconstruct and validate their own copy from immutable Git.
        # Nothing from an untrusted workflow artifact or external source is used.
        result=verify_staging(GitHubRead(token=os.environ.get('GITHUB_TOKEN','')),spec,artifact)
        if sys.argv[1]=='publish':
            result=publish(GitHubPublish(os.environ.get('GITHUB_TOKEN','')),
                           spec['tag'],spec['size'],spec['sha256'],artifact)
        print(json.dumps(result,sort_keys=True))
        return 0
    except PublicationUncertain as e:
        print(json.dumps({'state':'PUBLICATION_UNCERTAIN',
            'code':'publication_requires_manual_readback',
            'release_id':e.release_id,'tag':e.tag,'retry_allowed':False},sort_keys=True))
        return 1
    except Refused as e:
        print(json.dumps({'state':'REFUSED','code':str(e)},sort_keys=True))
        return 1
    except Exception:
        print(json.dumps({'state':'REFUSED','code':'unexpected_internal_error'},sort_keys=True))
        return 1
    finally:
        if artifact is not None:
            artifact.unlink(missing_ok=True)

if __name__=='__main__':
    sys.exit(main())
