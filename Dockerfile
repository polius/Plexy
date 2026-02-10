FROM python:3.13-slim

WORKDIR /app

# Install system libtorrent package (pre-compiled, no build needed)
RUN apt-get update && \
    apt-get install -y python3-libtorrent && \
    rm -rf /var/lib/apt/lists/*

# Add Debian's dist-packages to Python path so system packages are accessible
ENV PYTHONPATH="/usr/lib/python3/dist-packages:${PYTHONPATH}"

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create downloads directory
RUN mkdir -p /downloads

# Expose port
EXPOSE 8000

# Run the application
CMD ["python", "main.py"]
