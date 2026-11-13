# Local modification (2026-11-13): Windows workflow trim and/or secret-free diagnostics; see NOTICE.
"""wechat-cli 入口"""

import sys


def _configure_stdio():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_stdio()

import click

from .core.context import AppContext

_VERSION = "0.2.6"


@click.group()
@click.version_option(version=_VERSION, prog_name="wechat-cli")
@click.option("--config", "config_path", default=None, envvar="WECHAT_CLI_CONFIG",
              help="config.json 路径（默认自动查找）")
@click.pass_context
def cli(ctx, config_path):
    """WeChat CLI — 查询微信消息、联系人等数据

    \b
    wechat-cli init                     # Extract and save local database keys
    wechat-cli contacts --query "name"  # Look up account/contact identifiers
    """
    # init/version 命令不需要 AppContext
    if ctx.invoked_subcommand in ("init", "version"):
        return

    try:
        ctx.obj = AppContext(config_path)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"初始化失败: {e}", err=True)
        sys.exit(1)


# Local trimmed command surface; upstream source attribution retained in NOTICE.
from .commands.init import init
from .commands.contacts import contacts
cli.add_command(init)
cli.add_command(contacts)

if __name__ == "__main__":
    cli()
