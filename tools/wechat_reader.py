# SPDX-License-Identifier: Apache-2.0
"""Local CLI entry point, including init and contacts; no GitHub install needed."""
from bootstrap_vendor import activate
activate()
from wechat_cli.main import cli

if __name__ == '__main__':
    cli()
