FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# In dev, the directory is mounted as a volume (see docker-compose.yml).
# In prod, copy the source.
COPY . .
