"""Turn CI output into GitHub annotations, readable in the run summary and through the API.

    annotate.py log <file> <title>     last 60 lines of a log as one error annotation
    annotate.py trivy <report.json>    one error per finding; exit 1 when there is any
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _escape(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def log(path: str, title: str) -> int:
    p = Path(path)
    lines = p.read_text(errors="replace").splitlines()[-60:] if p.exists() else ["(no log written)"]
    print(f"::error title={_escape(title)}::{_escape(chr(10).join(lines))}")
    return 0


def trivy(path: str) -> int:
    report = json.loads(Path(path).read_text())
    found = 0
    for result in report.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            found += 1
            print(
                f"::error file={result.get('Target')},title={v.get('Severity')} {v.get('VulnerabilityID')}::"
                f"{v.get('PkgName')} {v.get('InstalledVersion')} fixed in {v.get('FixedVersion') or 'n/a'}"
                f" ({_escape((v.get('Title') or '')[:120])})"
            )
        for s in result.get("Secrets") or []:
            found += 1
            print(f"::error file={result.get('Target')},title=secret {s.get('RuleID')}::line {s.get('StartLine')}")
    print(f"{found} finding(s)")
    return 1 if found else 0


if __name__ == "__main__":
    mode, *args = sys.argv[1:]
    sys.exit({"log": log, "trivy": trivy}[mode](*args))
