from __future__ import annotations
import click
from penpal.config import Config
from . import main


@main.group("spike", hidden=True, help="Throwaway spike tools (M2-4 / #64). Not part of the product.")
def spike_group():
    pass

@spike_group.command(
    "ax-text",
    help="Spike: read committed text of the focused field via AXValue diffs. Needs Accessibility permission.",
)
@click.option("--app", "app_name", required=True, help="Target app name, e.g. Obsidian or 'Google Chrome'")
@click.option("--seconds", default=60.0, show_default=True, type=click.FloatRange(min=1.0, max=3600.0))
@click.option("--out", "out_path", default=None,
              help="JSONL output path, outside any git work tree (default ~/.penpal/spike/ax-<app>-<ts>.jsonl)")
def spike_ax_text(app_name, seconds, out_path):
    from penpal.capture.ax_spike import run_ax_text_spike
    from penpal.capture.ax_spike_adapter import PyObjCAXAdapter

    code = run_ax_text_spike(
        app_name=app_name,
        seconds=seconds,
        out=out_path,
        adapter=PyObjCAXAdapter(),
        cfg_loader=Config.load,
        echo=lambda message: click.echo(message, err=True),
    )
    if code:
        raise SystemExit(code)
