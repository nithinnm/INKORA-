"""Bounded, read-only probe of an exact local CUPS queue. No print operations."""
import argparse
from dataclasses import asdict
import json
import re
import subprocess
import sys

from agent.hardware import printer_health

# Run native pycups calls in a disposable worker so a stuck CUPS daemon cannot
# block the agent. Use only the local UNIX socket, never a configured remote host.
WORKER = '''
import json, sys
try:
    import cups
    connection = cups.Connection(host="/run/cups/cups.sock")
    attrs = connection.getPrinterAttributes(sys.argv[1])
    keys = ("printer-state", "printer-is-accepting-jobs", "printer-state-reasons", "marker-levels")
    print(json.dumps({key: attrs[key] for key in keys if key in attrs}))
except Exception:
    sys.exit(1)
'''


def probe(queue, *, timeout=5):
    if not isinstance(queue, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,126}', queue):
        raise ValueError('Use an exact local CUPS queue name.')
    if not 0 < timeout <= 10:
        raise ValueError('Probe timeout must be between zero and ten seconds.')
    try:
        result = subprocess.run([sys.executable, '-c', WORKER, queue],
                                capture_output=True, timeout=timeout, check=True)
        if len(result.stdout) > 16384:
            return printer_health(None)
        attributes = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError, UnicodeError):
        return printer_health(None)
    return printer_health(attributes)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', default='Epson_WF_C5890')
    args = parser.parse_args()
    try:
        health = probe(args.queue)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({**asdict(health), 'may_start_job': health.may_start_job}))
    sys.exit(0 if health.may_start_job else 2)
