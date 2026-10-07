"""Pin downloaded trusted-registry wheels by SHA-256 for offline container builds."""
import hashlib
from pathlib import Path
from packaging.utils import canonicalize_name,parse_wheel_filename
from packaging.requirements import Requirement
hashes={}
for wheel in Path('.build/wheels').glob('*.whl'):
    name,version,_,_=parse_wheel_filename(wheel.name)
    hashes[(canonicalize_name(name),str(version))]=hashlib.sha256(wheel.read_bytes()).hexdigest()
lines=[]
for line in Path('requirements-runtime-lock.txt').read_text().splitlines():
    requirement=Requirement(line);version=next(iter(requirement.specifier)).version
    lines.append(line+' --hash=sha256:'+hashes[canonicalize_name(requirement.name),version])
Path('requirements-runtime-hashed.txt').write_text('\n'.join(lines)+'\n')
Path('requirements-build-lock.txt').write_text('pip==26.2.1 --hash=sha256:'+hashes['pip','26.2.1']+'\n')
