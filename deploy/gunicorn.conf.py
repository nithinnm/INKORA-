import os
bind='0.0.0.0:'+os.environ.get('PORT','8080')
workers=2
threads=1
worker_class='gthread'
timeout=60
graceful_timeout=30
keepalive=5
max_requests=1000
max_requests_jitter=100
accesslog=None  # customer capability paths must not be written into access logs
errorlog='-'
capture_output=False
forwarded_allow_ips=''  # never trust arbitrary forwarded headers
