"""Account-only preflight: no search, raw response logging or credential storage."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wgforge.sources import SourceError, serpapi_account_status


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    try:
        report = serpapi_account_status(config)
    except SourceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("SerpAPI account check: " + json.dumps(report))
    if not report["ready"]:
        print("SerpAPI 账号未就绪：请检查账号状态、剩余搜索次数和每小时限额。未启动造题。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
