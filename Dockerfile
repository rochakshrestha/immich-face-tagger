FROM python:3.12-slim

WORKDIR /app
COPY immich_face_tagger.py .

RUN pip install --no-cache-dir requests

CMD ["python", "-u", "immich_face_tagger.py"]
