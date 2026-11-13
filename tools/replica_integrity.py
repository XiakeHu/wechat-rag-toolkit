# SPDX-License-Identifier: Apache-2.0
"""Verify the pinned external accessibility dependency before loading it."""
import hashlib
import importlib.util
import json
from pathlib import Path


def verify_replica_sources():
    from bootstrap_vendor import activate
    activate()
    location=Path(importlib.util.find_spec('wechatauto').origin).parent
    manifest=json.loads((Path(__file__).resolve().parents[1]/'config/replica-audit.json').read_text(encoding='utf-8'))
    for name,expected in manifest['source_hashes'].items():
        source=(location/name).read_text(encoding='utf-8-sig').replace('\r\n','\n')
        if hashlib.sha256(source.encode()).hexdigest()!=expected:
            raise ValueError('无障碍依赖源码与已审查提交不同，请重新审查')
