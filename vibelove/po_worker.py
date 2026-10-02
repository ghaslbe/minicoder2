"""Interruptible PO process. Credentials enter via stdin, never argv or disk."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import po


def emit(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    try:
        args = json.load(sys.stdin)
        if args.pop('_operation', 'refine') == 'evaluate':
            emit({'type': 'result', 'evaluation': po.evaluate(**args)})
        else:
            decision, history = po.refine_retrying(**args, on_event=emit)
            emit({'type': 'result', 'decision': decision, 'history': history})
    except Exception as exc:
        emit({'type': 'error', 'error': str(exc)})
        raise SystemExit(1)
