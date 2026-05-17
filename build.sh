#!/bin/bash
# Build script for Railway deployment.
# The production frontend is committed in backend/static.

set -e

if [ ! -f backend/static/index.html ]; then
  echo "ERROR: backend/static/index.html is missing"
  exit 1
fi

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Build complete!"
