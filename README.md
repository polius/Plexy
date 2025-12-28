# Plexy - Automated Torrent Downloader with Plex Integration

A web application for downloading torrents and automatically refreshing Plex libraries.

## Features

- Add torrent via torrent file, magnet link or search Nyaa.si
- Browse and select download folder dynamically
- Real-time download progress tracking
- Cancel downloads with automatic cleanup
- Automatically refresh Plex library when complete

## Setup with Docker

1. Edit `docker-compose.yml` and configure:
   - **Volume**: Change `/path/to/your/plex/media` to your actual Plex media folder
   - **PLEX_URL**: Set to your Plex server URL (use `http://host.docker.internal:32400` if Plex is on host)
   - **PLEX_TOKEN**: Set your Plex authentication token

   Get your Plex token: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

2. Build and run:
```bash
docker compose up --build
```

3. Open browser at `http://localhost:8000`

4. Stop:
```bash
docker compose down
```
