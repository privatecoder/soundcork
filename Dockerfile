FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and entrypoint script
COPY soundcork/ soundcork/
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh

# The app imports "from soundcork.bmx import ..." so /app must be on PYTHONPATH
# The app reads bmx_services.json, swupdate.xml, and media/ from CWD
ENV PYTHONPATH=/app
WORKDIR /app/soundcork

# Use entrypoint script to support both dev and production modes
ENTRYPOINT ["/docker-entrypoint.sh"]
