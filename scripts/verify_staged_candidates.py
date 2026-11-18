#!/usr/bin/env python3
"""Read-only acceptance: reconstruct both immutable public GitHub stages.

This never obtains a write token, creates a Release, or talks to Station.
Uses only an optional same-repository read token to avoid API rate limits.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_station_release import GitHubRead, RELEASES, verify_staging

def main():
    reader=GitHubRead(token=os.environ.get('GITHUB_TOKEN',''))
    with tempfile.TemporaryDirectory(prefix='station-ci-stage-') as td:
        for tag, spec in RELEASES.items():
            output=Path(td)/('artifact-'+tag)
            verified=verify_staging(reader, spec, output)
            if (verified['sha256'] != spec['sha256'] or
                    verified['bytes'] != spec['size'] or verified['status']!='VERIFIED'):
                raise RuntimeError('immutable_stage_mismatch')
            print(json.dumps({'tag':tag,'status':'VERIFIED','bytes':verified['bytes'],
                              'sha256':verified['sha256'],'chunks':verified['parts']},
                             sort_keys=True))
    return 0

if __name__=='__main__':
    sys.exit(main())
