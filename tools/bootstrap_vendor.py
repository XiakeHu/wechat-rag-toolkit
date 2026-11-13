# SPDX-License-Identifier: Apache-2.0
"""Use bundled dependencies without fetching their GitHub repositories."""
from pathlib import Path
import sys


def activate():
    root = Path(__file__).resolve().parents[1] / 'vendor'
    for folder, module in [('wechat-cli', 'wechat_cli'), ('wechatauto-replica', 'wechatauto')]:
        location = root / folder
        if not (location / module / '__init__.py').is_file():
            raise ImportError('Missing bundled dependency: ' + folder)
        loaded = sys.modules.get(module)
        if loaded is not None and not Path(loaded.__file__).resolve().is_relative_to(location):
            raise ImportError('A different dependency was already imported: ' + module)
        if str(location) in sys.path:
            sys.path.remove(str(location))
        sys.path.insert(0, str(location))
