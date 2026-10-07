FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app
COPY requirements-runtime-hashed.txt requirements-build-lock.txt /app/
COPY .build/wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels --require-hashes --upgrade -r requirements-build-lock.txt && python -m pip install --no-index --find-links=/wheels --require-hashes -r requirements-runtime-hashed.txt && python -m pip check && rm -rf /wheels
RUN groupadd --gid 10001 inkora && useradd --uid 10001 --gid 10001 --no-create-home inkora && mkdir -p /app/instance && chown inkora:inkora /app/instance
COPY --chown=inkora:inkora inkora /app/inkora
COPY --chown=inkora:inkora migrations /app/migrations
COPY --chown=inkora:inkora deploy/gunicorn.conf.py /app/gunicorn.conf.py
USER 10001:10001
EXPOSE 8080
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "inkora:create_app()"]
