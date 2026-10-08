"""Loopback-only staging touchscreen. Permanent device credentials stay on the agent."""
import argparse
import io
import json
import logging
from pathlib import Path
import secrets
import threading
import urllib.error
from flask import Flask, abort, jsonify, render_template, request, send_file
import qrcode
import kiosk_simulator as agent


def create_terminal(identity_path, preview_host=None):
    path=Path(identity_path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('Identity must be a private regular file, not a symlink.')
    identity=json.loads(path.read_text())
    base=agent.endpoint(identity['url'])
    app=Flask(__name__,template_folder='kiosk_ui',static_folder='kiosk_ui',static_url_path='/assets')
    app.config['MAX_CONTENT_LENGTH']=1024
    nonce=secrets.token_urlsafe(32)
    state={}
    lock=threading.Lock()

    @app.before_request
    def protect_local_agent():
        host=request.host.split(':')[0].lower()
        if host not in ('127.0.0.1','localhost',preview_host):
            abort(403,'Preview hostname is not allowed. Restart with its exact --preview-host.')
        if request.method=='POST':
            origins={'http://'+request.host,'https://'+request.host}
            if preview_host:
                origins.add('https://'+preview_host)
            if request.headers.get('Origin') not in origins:
                abort(403,'Preview origin is not allowed. Restart with its exact --preview-host.')
            if not secrets.compare_digest(request.headers.get('X-Kiosk-CSRF',''),nonce):
                abort(403,'This touchscreen page expired. Reload it and try again.')

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'X-Content-Type-Options':'nosniff','Content-Security-Policy':
            "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @app.errorhandler(Exception)
    def errors(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error,HTTPException):
            return jsonify(error=error.description),error.code
        if isinstance(error,urllib.error.HTTPError):
            messages={401:'Device authentication failed. Ask the operator to check enrollment.',
                      403:'Cloud access was denied. Check the Cloud Shell login and device enrollment.',
                      409:'Kiosk is unavailable or a customer session is still active. Keep heartbeats running and wait for session expiry.'}
            return jsonify(error=messages.get(error.code,'Cloud request unavailable; HTTP '+str(error.code)+'. Ask the operator for help.')),503
        return jsonify(error='Connection unavailable. Check the agent connection and retry.'),503

    @app.get('/')
    def home():
        return render_template('terminal.html',nonce=nonce)

    @app.post('/start')
    def start():
        with lock:
            if state.get('session_id'):
                abort(409)
            result=agent.post(base,'/api/devices/customer-sessions',{},identity['device_token'])
            if not result.get('session_id'):
                return jsonify(error='Cloud service needs the kiosk-session status update before this UI can start.'),409
            state.update(session_id=result['session_id'],upload_url=result['upload_url'])
        return jsonify(state='waiting_scan',expires_in=result['expires_in'])

    @app.get('/qr.png')
    def qr():
        with lock:
            url=state.get('upload_url')
        if not url:
            abort(404)
        output=io.BytesIO();qrcode.make(url).save(output,format='PNG');output.seek(0)
        return send_file(output,mimetype='image/png')

    @app.post('/status')
    def status():
        with lock:
            session_id=state.get('session_id')
        if not session_id:
            return jsonify(state='idle')
        return jsonify(agent.post(base,'/api/devices/customer-sessions/status',
            {'session_id':session_id},identity['device_token']))

    @app.post('/reset')
    def reset():
        with lock:
            if state.get('session_id'):
                result=agent.post(base,'/api/devices/customer-sessions/status',
                    {'session_id':state['session_id']},identity['device_token'])
                if result['state'] not in ('completed','cancelled','expired','needs_review'):
                    return jsonify(error='The session is still active. Wait for expiry or ask the operator.'),409
            state.clear()
        return jsonify(state='idle')

    @app.post('/cancel')
    def cancel():
        with lock:
            if state.get('session_id'):
                agent.post(base,'/api/devices/customer-sessions/cancel',
                    {'session_id':state['session_id']},identity['device_token'])
            state.clear()
        return jsonify(state='idle')

    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity',required=True,type=Path)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--gcloud-auth',action='store_true')
    parser.add_argument('--preview-host',help='Exact Cloud Shell HTTPS preview hostname, without scheme/port')
    args=parser.parse_args()
    agent.cloud_run_auth=args.gcloud_auth
    app=create_terminal(args.identity,args.preview_host)
    logging.getLogger('werkzeug').disabled=True
    print('Staging touchscreen started on loopback. Device credentials remain on this agent.')
    app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False)
