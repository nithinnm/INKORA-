"""Private document adapters. No public URLs or browser credentials."""
from io import BytesIO
import os
from pathlib import Path
from flask import current_app, send_file


def local_root():
    root=Path(current_app.config['PRINT_STORAGE'])
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    return root


def bucket():
    from google.cloud import storage
    return storage.Client(project=current_app.config['CLOUD_PROJECT']).bucket(current_app.config['GCS_BUCKET'])


def put(job_id,path):
    if current_app.config['GCS_BUCKET']:
        blob=bucket().blob('jobs/'+job_id+'.pdf')
        blob.upload_from_filename(str(path),content_type='application/pdf',if_generation_match=0,timeout=30,checksum='crc32c')
    else:
        target=local_root()/(job_id+'.pdf')
        with target.open('xb') as output, path.open('rb') as source:
            os.chmod(target,0o600)
            import shutil
            shutil.copyfileobj(source,output)


def delete(job_id):
    if current_app.config['GCS_BUCKET']:
        from google.api_core.exceptions import NotFound
        try:
            blob=bucket().blob('jobs/'+job_id+'.pdf'); blob.reload(timeout=15)
            blob.delete(if_generation_match=blob.generation,timeout=15)
        except NotFound:
            pass
    else:
        (local_root()/(job_id+'.pdf')).unlink(missing_ok=True)


def response(job_id):
    if current_app.config['GCS_BUCKET']:
        blob=bucket().blob('jobs/'+job_id+'.pdf'); blob.reload(timeout=15)
        if not blob.size or blob.size>10*1024*1024:
            raise ValueError('Invalid object size')
        content=blob.download_as_bytes(if_generation_match=blob.generation,timeout=30,checksum='crc32c')
        source=BytesIO(content)
    else:
        source=local_root()/(job_id+'.pdf')
        if not source.is_file():
            raise FileNotFoundError()
    return send_file(source,mimetype='application/pdf',as_attachment=True,download_name='document.pdf')
