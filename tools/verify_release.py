"""Verify an offline agent artifact. Does not install or execute it."""
import argparse
import hashlib
import json
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def verify(manifest,signature,artifact,trusted_public_key):
    raw=manifest.read_bytes()
    if len(raw)>16384:
        raise ValueError('Manifest too large')
    key=load_pem_public_key(trusted_public_key.read_bytes())
    if not isinstance(key,Ed25519PublicKey):
        raise ValueError('Expected an Ed25519 trust key')
    key.verify(signature.read_bytes(),raw)
    metadata=json.loads(raw)
    if set(metadata)!={'version','sha256','size'} or not isinstance(metadata['version'],str) or not 1<=len(metadata['version'])<=40:
        raise ValueError('Invalid release metadata')
    if not isinstance(metadata['size'],int) or not 1<=metadata['size']<=100*1024*1024 or artifact.stat().st_size!=metadata['size']:
        raise ValueError('Invalid artifact size')
    hasher=hashlib.sha256()
    with artifact.open('rb') as file:
        while data:=file.read(65536):hasher.update(data)
    if hasher.hexdigest()!=metadata['sha256']:
        raise ValueError('Artifact checksum mismatch')
    return metadata['version']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','signature','artifact','trusted_public_key'):
        parser.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    args=parser.parse_args()
    version=verify(args.manifest,args.signature,args.artifact,args.trusted_public_key)
    print('Signature and checksum verified for version '+version+'. No installation performed.')
