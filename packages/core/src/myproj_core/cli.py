"""Command-line entrypoint, wired via [project.scripts] in pyproject.toml.

`uv run myproj` is the only invocation anyone types. Keep orchestration
and file I/O here; keep the importable modules pure.
"""

import argparse


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="myproj", description=__doc__.splitlines()[0])
    p.parse_args(argv)
    print("myproj: replace me with a real command")
