#!/bin/bash
set -e

if [ "$DEV_MODE" = "true" ]; then
    echo "Starting soundcork in development mode..."
    cd /app
    exec fastapi dev --host 0.0.0.0 soundcork/main.py
else
    echo "Starting soundcork in production mode..."
    cd /app/soundcork
    exec gunicorn -c gunicorn_conf.py --bind 0.0.0.0:8000 \
        --access-logfile - --error-logfile - \
        --workers 1 \
        main:app
fi
