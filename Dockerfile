FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY migrations ./migrations
# Los tests van dentro para poder ejecutarlos contra el contenedor real
# (docker exec finance-tracker pytest -q) sin tener que copiarlos a mano.
COPY tests ./tests

# La imagen lleva dentro la fuente Inter y Chart.js, así que los redistribuye:
# la OFL exige que su aviso viaje con la fuente, no solo en el repositorio.
COPY LICENSE NOTICE ./
COPY licencias ./licencias

# Usuario sin privilegios: la app no necesita root, y /data tiene que
# pertenecerle para poder escribir la BD y los backups.
RUN mkdir -p /data && useradd --system --uid 1000 finance && chown -R finance:finance /data /app

# El contenedor arranca como root solo para que el entrypoint pueda ajustar la
# propiedad de /data (un volumen ya existente conserva la suya, y este chown no
# le llega); acto seguido baja a "finance" y la app nunca corre como root.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]

VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/salud', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
