"""Resolve installed runtime-only dependency closure for container pinning."""
from pathlib import Path
import importlib.metadata as metadata
from packaging.requirements import Requirement
seen=set()


def visit(name):
    distribution=metadata.distribution(name)
    normalized=distribution.metadata['Name'].lower().replace('_','-')
    if normalized in seen:
        return
    seen.add(normalized)
    for spec in distribution.requires or []:
        requirement=Requirement(spec)
        if not requirement.marker or requirement.marker.evaluate({'extra':''}):
            visit(requirement.name)


for line in Path('requirements.txt').read_text().splitlines():
    if line:
        visit(Requirement(line).name)
for extra in ('psycopg-binary','pillow'):
    visit(extra)
lines=[]
for name in seen:
    distribution=metadata.distribution(name)
    lines.append(distribution.metadata['Name']+'=='+distribution.version)
Path('requirements-runtime-lock.txt').write_text('\n'.join(sorted(lines,key=str.lower))+'\n')
