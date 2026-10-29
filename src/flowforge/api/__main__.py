"""Run the dashboard: python -m flowforge.api [--demo DIR] [--host H] [--port P]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowForge dashboard (read-only, plus the kill switch)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", type=Path, default=Path("."), help="folder with data/, reports/, config/")
    parser.add_argument("--run-dir", type=Path, default=None, help="heartbeat + kill switch folder")
    parser.add_argument("--demo", type=Path, default=None, help="write made-up data here and show it")
    args = parser.parse_args(argv)

    import uvicorn

    from flowforge.api.app import ApiPaths, create_app

    if args.demo is not None:
        from flowforge.api.demo import keep_beating, write_demo

        keep_beating(args.demo, write_demo(args.demo))
        paths = ApiPaths(
            root=args.demo, run_dir=args.demo / "run", demo=True, kill_file=args.demo / "run" / "KILL"
        )
        app = create_app(paths, port_check=lambda host, port: True)  # demo: pretend IB Gateway is up
    else:
        paths = ApiPaths(root=args.root, **({"run_dir": args.run_dir} if args.run_dir else {}))
        app = create_app(paths)
    print(f"FlowForge dashboard on http://{args.host}:{args.port}" + (" (demo data)" if paths.demo else ""))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
