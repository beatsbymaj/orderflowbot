"""Fail if any source file enables the Nautilus RiskEngine bypass (DECISIONS.md D7)."""

import re
import sys
from pathlib import Path

PATTERN = re.compile(r"bypass\s*[=:]\s*True")


def main(roots: list[str]) -> int:
    hits = [
        f"{path}:{n}: {line.strip()}"
        for root in roots
        for path in Path(root).rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if PATTERN.search(line)
    ]
    for hit in hits:
        print(f"RiskEngine bypass enabled: {hit}", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["src"]))
