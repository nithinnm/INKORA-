"""Credential rotation with a brief retry window; no remote shell access."""
import time
from flask import abort, jsonify, request
from sqlalchemy import select
from . import db, csrf, Kiosk, User, digest


def register_devices(app):
    @app.get('/api/devices/config')
    def configuration():
        from .security import authenticate_device
        kiosk=authenticate_device()
        return jsonify(kiosk_id=kiosk.id,name=kiosk.name,configuration_version=kiosk.configuration_version,
            prices_paise=kiosk.prices,max_pdf_bytes=10*1024*1024,max_pdf_pages=250,
            customer_session_seconds=180,simulation=not app.config['PRODUCTION'])

    @app.post('/api/devices/rotate')
    @csrf.exempt
    def rotate():
        from .security import authenticate_device
        kiosk=authenticate_device()
        body=request.get_json(silent=True)
        new=body.get('new_token') if isinstance(body,dict) else None
        if not isinstance(new,str) or not 48<=len(new)<=100 or not all(c.isalnum() or c in '-_' for c in new):
            abort(400,'Supply a securely generated 48–100 character token.')
        supplied=digest(request.headers['Authorization'][7:]);replacement=digest(new)
        if kiosk.previous_device_hash==supplied and kiosk.device_hash==replacement:
            db.session.commit();return jsonify(status='rotated')
        if kiosk.device_hash!=supplied or supplied==replacement:
            abort(409,'Use the current credential and a fresh replacement.')
        kiosk.previous_device_hash=kiosk.device_hash;kiosk.previous_device_until=time.time()+60
        kiosk.device_hash=replacement;db.session.commit()
        return jsonify(status='rotated')
