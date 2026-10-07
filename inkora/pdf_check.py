"""Run only as a bounded subprocess, never in a web worker."""
import resource
import sys
from pypdf import PdfReader

resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
try:
    reader = PdfReader(sys.argv[1], strict=True)
    if reader.is_encrypted:
        raise ValueError('Encrypted PDF')
    pages = len(reader.pages)
    if not 1 <= pages <= 250:
        raise ValueError('Page limit')
    print(pages)
except Exception:
    sys.exit(1)
