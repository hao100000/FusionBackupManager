"""Local byte verification only; never imports Fusion or changes backup files."""
import argparse
import json
import sys
from FusionBackupManager.core import verify

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    args = parser.parse_args()
    try:
        results = verify(args.directory)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(0 if results and all(x['valid'] for x in results) else 1)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
