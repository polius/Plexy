import os
import urllib3
import libtorrent as lt
import shutil
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict
from plexapi.server import PlexServer

app = FastAPI()

# Global variables for torrent session
torrent_session = lt.session()
settings = torrent_session.get_settings()
settings['listen_interfaces'] = '0.0.0.0:6881'
torrent_session.apply_settings(settings)
active_downloads: Dict[str, lt.torrent_handle] = {}
download_info: Dict[str, dict] = {}

# Plex connection from environment variables (lazily established / reconnected)
plex = None


def get_plex():
    """Return a connected PlexServer, attempting to (re)connect if needed.

    Connection is lazy so Plexy keeps working when Plex is started after Plexy,
    and so the rest of the app never depends on Plex being reachable.
    """
    global plex
    if plex is not None:
        return plex

    token = os.getenv('PLEX_TOKEN', '')
    if not token:
        return None

    url = os.getenv('PLEX_URL', 'http://localhost:32400')
    try:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        session = requests.Session()
        session.verify = False
        plex = PlexServer(url, token, session=session, timeout=30)
    except Exception as e:
        print(f"Warning: Could not connect to Plex: {e}")
        plex = None
    return plex


# Base path for downloads (internal container path)
BASE_PATH = "/downloads"


def resolve_safe_path(display_path: str) -> str:
    """Map a display path to a real filesystem path, refusing anything outside BASE_PATH.

    Resolves symlinks and '..' segments before validating, which closes the
    path-traversal hole where '/../etc' -> '/downloads/../etc' passed a naive
    startswith() check but resolved to '/etc'.
    """
    internal = get_internal_path(display_path or "/")
    real = os.path.realpath(internal)
    base = os.path.realpath(BASE_PATH)
    if real != base and not real.startswith(base + os.sep):
        raise HTTPException(status_code=403, detail="Access denied: path is outside the downloads directory")
    return real

def get_display_path(internal_path: str) -> str:
    """Convert internal path to display path by removing /downloads prefix"""
    if internal_path.startswith(BASE_PATH):
        display = internal_path[len(BASE_PATH):]
        return display if display else "/"
    return internal_path

def get_internal_path(display_path: str) -> str:
    """Convert display path to internal path by adding /downloads prefix"""
    if display_path == "/":
        return BASE_PATH
    return BASE_PATH + display_path


class MagnetRequest(BaseModel):
    magnet_link: str
    download_path: str
    selected_files: list = None  # List of file indices to download
    skip_parent_folder: bool = False  # Skip creating parent folder
    flatten_all: bool = False  # Flatten all subdirectories


class CancelRequest(BaseModel):
    download_id: str


class PlexRefreshRequest(BaseModel):
    library_name: str


class CreateFolderRequest(BaseModel):
    path: str
    name: str


class TorrentInfoRequest(BaseModel):
    magnet_link: str = None


@app.get("/")
def read_index():
    return FileResponse('web/index.html')


@app.get("/api/search/nyaa")
def search_nyaa(query: str):
    """Search nyaa.si for torrents using RSS feed"""
    try:
        # Use RSS feed instead of scraping HTML
        # &o=desc&s=seeders orders results by seeders in descending order
        url = f"https://nyaa.si/?page=rss&q={requests.utils.quote(query)}&s=seeders&o=desc"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Parse RSS XML with ElementTree
        root = ET.fromstring(response.content)
        
        # Define namespaces
        namespaces = {
            'nyaa': 'https://nyaa.si/xmlns/nyaa'
        }
        
        results = []
        
        # Find all items in the RSS feed
        for item in root.findall('.//item')[:20]:  # Limit to 20 results
            try:
                title_elem = item.find('title')
                title_text = title_elem.text if title_elem is not None else 'Unknown'
                
                guid_elem = item.find('guid')
                guid_text = guid_elem.text if guid_elem is not None else ''
                
                # Get magnet link from infoHash (nyaa namespace)
                info_hash_elem = item.find('nyaa:infoHash', namespaces)
                magnet_link = None
                
                if info_hash_elem is not None and info_hash_elem.text:
                    info_hash = info_hash_elem.text.strip()
                    magnet_link = f"magnet:?xt=urn:btih:{info_hash}&dn={requests.utils.quote(title_text)}&tr=http://nyaa.tracker.wf:7777/announce&tr=udp://open.stealth.si:80/announce&tr=udp://tracker.opentrackr.org:1337/announce"
                
                # Get size (nyaa namespace)
                size_elem = item.find('nyaa:size', namespaces)
                size = size_elem.text if size_elem is not None else 'Unknown'
                
                # Get seeders and leechers (nyaa namespace)
                seeders_elem = item.find('nyaa:seeders', namespaces)
                leechers_elem = item.find('nyaa:leechers', namespaces)
                seeders = int(seeders_elem.text.strip()) if seeders_elem is not None and seeders_elem.text else 0
                leechers = int(leechers_elem.text.strip()) if leechers_elem is not None and leechers_elem.text else 0
                
                # Get category (nyaa namespace)
                category_elem = item.find('nyaa:category', namespaces)
                category_name = category_elem.text if category_elem is not None else 'Unknown'
                
                # Get publication date
                pubdate_elem = item.find('pubDate')
                date_str = 'Unknown'
                if pubdate_elem is not None and pubdate_elem.text:
                    try:
                        # Parse RFC 2822 date format
                        dt = datetime.strptime(pubdate_elem.text, '%a, %d %b %Y %H:%M:%S %z')
                        date_str = dt.strftime('%Y-%m-%d %H:%M')
                    except:
                        date_str = pubdate_elem.text
                
                # Get torrent ID from guid
                torrent_id = guid_text.split('/')[-1] if guid_text else ''
                
                if magnet_link:  # Only add if we have a magnet link
                    results.append({
                        'id': torrent_id,
                        'name': title_text,
                        'magnet': magnet_link,
                        'size': size,
                        'seeders': seeders,
                        'leechers': leechers,
                        'category': category_name,
                        'date': date_str,
                        'link': guid_text
                    })
            except Exception as e:
                print(f"Error parsing RSS item: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        return {
            'query': query,
            'results': results
        }
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching from nyaa.si: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/torrent/info")
def get_torrent_info(request: TorrentInfoRequest):
    """Get file list from a magnet link by downloading metadata"""
    try:
        if not request.magnet_link or not request.magnet_link.startswith('magnet:'):
            raise HTTPException(status_code=400, detail="Invalid magnet link format")
        
        # Create a temporary session to fetch metadata
        temp_session = lt.session()
        temp_settings = temp_session.get_settings()
        temp_settings['listen_interfaces'] = '0.0.0.0:0'  # Random port
        temp_session.apply_settings(temp_settings)
        
        # Add magnet with minimal settings just to get metadata
        params = {
            'save_path': '/tmp',
            'storage_mode': lt.storage_mode_t.storage_mode_allocate,
            'flags': lt.torrent_flags.upload_mode,  # Don't download, just get metadata
        }
        
        handle = lt.add_magnet_uri(temp_session, request.magnet_link, params)
        
        # Wait for metadata (max 30 seconds)
        import time
        max_wait = 30
        start_time = time.time()
        
        while not handle.has_metadata():
            if time.time() - start_time > max_wait:
                temp_session.remove_torrent(handle)
                raise HTTPException(status_code=408, detail="Timeout waiting for torrent metadata")
            time.sleep(0.1)
        
        # Get torrent info
        torrent_info = handle.torrent_file()
        if not torrent_info:
            temp_session.remove_torrent(handle)
            raise HTTPException(status_code=400, detail="Could not retrieve torrent information")
        
        # Extract file information
        files = []
        file_storage = torrent_info.files()
        for i in range(torrent_info.num_files()):
            files.append({
                'index': i,
                'name': file_storage.file_path(i),
                'size': file_storage.file_size(i)
            })
        
        torrent_name = torrent_info.name()
        total_size = torrent_info.total_size()
        
        # Clean up
        temp_session.remove_torrent(handle)
        
        return {
            'name': torrent_name,
            'total_size': total_size,
            'num_files': len(files),
            'files': files
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting torrent info: {str(e)}")


@app.post("/api/torrent/info/file")
async def get_torrent_info_from_file(file: UploadFile = File(...)):
    """Get file list from an uploaded .torrent file"""
    try:
        # Validate file extension
        if not file.filename.endswith('.torrent'):
            raise HTTPException(status_code=400, detail="Invalid file type. Please upload a .torrent file")
        
        # Read the file content
        torrent_data = await file.read()
        
        if not torrent_data:
            raise HTTPException(status_code=400, detail="Torrent file is empty")
        
        # Create torrent info from the file data
        try:
            torrent_info = lt.torrent_info(torrent_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid torrent file: {str(e)}")
        
        # Extract file information
        files = []
        file_storage = torrent_info.files()
        for i in range(torrent_info.num_files()):
            files.append({
                'index': i,
                'name': file_storage.file_path(i),
                'size': file_storage.file_size(i)
            })
        
        torrent_name = torrent_info.name()
        total_size = torrent_info.total_size()
        
        return {
            'name': torrent_name,
            'total_size': total_size,
            'num_files': len(files),
            'files': files
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting torrent info: {str(e)}")


@app.get("/api/config/base-path")
def get_base_path():
    """Get the default download base path from config"""
    return {
        "base_path": "/",
        "display_path": "/"
    }


@app.get("/api/folders")
def list_folders(path: str = None):
    """List folders in the given path"""
    # Use config base path if no path provided
    if path is None:
        path = "/"

    try:
        # Resolve + validate against traversal outside BASE_PATH
        internal_path = resolve_safe_path(path)

        if not os.path.exists(internal_path):
            raise HTTPException(status_code=404, detail="Path not found")

        folders = []
        files = []
        try:
            entries = os.listdir(internal_path)
            for entry in sorted(entries):
                full_internal_path = os.path.join(internal_path, entry)
                if os.path.isdir(full_internal_path):
                    folders.append({
                        "name": entry,
                        "path": get_display_path(full_internal_path)
                    })
                elif os.path.isfile(full_internal_path):
                    # Get file size
                    size = os.path.getsize(full_internal_path)
                    files.append({
                        "name": entry,
                        "size": size
                    })
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied")
        
        at_root = internal_path == os.path.realpath(BASE_PATH)
        parent_internal = os.path.dirname(internal_path) if not at_root else None
        parent_display = get_display_path(parent_internal) if parent_internal else None
        display = get_display_path(internal_path)

        return {
            "current_path": display,
            "display_path": display,
            "parent_path": parent_display,
            "folders": folders,
            "files": files,
            "folder_count": len(folders),
            "file_count": len(files)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/folders/create")
def create_folder(request: CreateFolderRequest):
    """Create a new subfolder inside the current (validated) directory."""
    name = (request.name or "").strip()
    if not name or "/" in name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid folder name")

    parent = resolve_safe_path(request.path)
    target = resolve_safe_path(os.path.join(request.path or "/", name))
    try:
        os.makedirs(target, exist_ok=False)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="A folder with that name already exists")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create folder: {str(e)}")

    return {"path": get_display_path(target), "name": name}


@app.post("/api/download")
def start_download(request: MagnetRequest):
    """Start downloading a torrent from magnet link"""
    try:
        # Validate magnet link format
        if not request.magnet_link or not request.magnet_link.startswith('magnet:'):
            raise HTTPException(status_code=400, detail="Invalid magnet link format")

        # Resolve + validate download path (blocks traversal)
        internal_path = resolve_safe_path(request.download_path)

        # Validate download path
        if not os.path.exists(internal_path):
            raise HTTPException(status_code=404, detail="Download path not found")
        
        # Add torrent
        params = {
            'save_path': internal_path,
            'storage_mode': lt.storage_mode_t.storage_mode_sparse,
        }
        
        try:
            handle = lt.add_magnet_uri(torrent_session, request.magnet_link, params)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid magnet link: {str(e)}")
        
        # Verify handle is valid
        if not handle.is_valid():
            raise HTTPException(status_code=400, detail="Failed to add magnet link - invalid torrent")
        
        # Wait for metadata if we need to select files
        if request.selected_files is not None or request.skip_parent_folder:
            import time
            max_wait = 30
            start_time = time.time()
            
            while not handle.has_metadata():
                if time.time() - start_time > max_wait:
                    torrent_session.remove_torrent(handle)
                    raise HTTPException(status_code=408, detail="Timeout waiting for torrent metadata")
                time.sleep(0.1)
        
        # Handle file selection
        if request.selected_files is not None and handle.has_metadata():
            torrent_info = handle.torrent_file()
            if torrent_info:
                num_files = torrent_info.num_files()
                # Set file priorities: 0 = don't download, 4 = normal priority
                for i in range(num_files):
                    if i in request.selected_files:
                        handle.file_priority(i, 4)
                    else:
                        handle.file_priority(i, 0)
        
        # Handle skip parent folder option or flatten all
        if handle.has_metadata():
            torrent_info = handle.torrent_file()
            if torrent_info:
                file_storage = torrent_info.files()
                for i in range(torrent_info.num_files()):
                    original_path = file_storage.file_path(i)
                    
                    if request.flatten_all:
                        # Flatten all - keep only the filename
                        new_path = os.path.basename(original_path)
                        handle.rename_file(i, new_path)
                    elif request.skip_parent_folder:
                        # Remove only the first directory from the path
                        path_parts = original_path.split('/', 1)
                        if len(path_parts) > 1:
                            new_path = path_parts[1]
                            handle.rename_file(i, new_path)
        
        # Generate a stable download ID from the torrent's info hash
        try:
            download_id = str(handle.info_hash())[:16]
        except Exception:
            download_id = str(abs(hash(request.magnet_link)))[:16]
        active_downloads[download_id] = handle
        download_info[download_id] = {
            "status": "downloading",
            "progress": 0,
            "name": "Fetching metadata...",
            "download_rate": 0,
            "upload_rate": 0,
            "path": internal_path,
            "start_time": datetime.now().timestamp()
        }
        
        return {
            "download_id": download_id,
            "message": "Download started"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting download: {str(e)}")


@app.post("/api/download/file")
async def start_download_from_file(
    file: UploadFile = File(...), 
    download_path: str = Form(...),
    selected_files: str = Form(None),
    skip_parent_folder: bool = Form(False),
    flatten_all: bool = Form(False)
):
    """Start downloading a torrent from an uploaded .torrent file"""
    try:
        # Validate file extension
        if not file.filename.endswith('.torrent'):
            raise HTTPException(status_code=400, detail="Invalid file type. Please upload a .torrent file")

        # Resolve + validate download path (blocks traversal)
        internal_path = resolve_safe_path(download_path)

        # Validate download path
        if not os.path.exists(internal_path):
            raise HTTPException(status_code=404, detail="Download path not found")

        # Read the file content into memory (not saving to disk)
        torrent_data = await file.read()
        
        if not torrent_data:
            raise HTTPException(status_code=400, detail="Torrent file is empty")
        
        # Create torrent info from the file data
        try:
            torrent_info = lt.torrent_info(torrent_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid torrent file: {str(e)}")
        
        # Add torrent to session
        params = {
            'save_path': internal_path,
            'storage_mode': lt.storage_mode_t.storage_mode_sparse,
            'ti': torrent_info
        }
        
        try:
            handle = torrent_session.add_torrent(params)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to add torrent: {str(e)}")
        
        # Verify handle is valid
        if not handle.is_valid():
            raise HTTPException(status_code=400, detail="Failed to add torrent - invalid torrent")
        
        # Parse selected_files from JSON string
        selected_files_list = None
        if selected_files:
            import json
            try:
                selected_files_list = json.loads(selected_files)
            except:
                pass
        
        # Handle file selection
        if selected_files_list is not None:
            num_files = torrent_info.num_files()
            # Set file priorities: 0 = don't download, 4 = normal priority
            for i in range(num_files):
                if i in selected_files_list:
                    handle.file_priority(i, 4)
                else:
                    handle.file_priority(i, 0)
        
        # Handle skip parent folder option or flatten all
        file_storage = torrent_info.files()
        for i in range(torrent_info.num_files()):
            original_path = file_storage.file_path(i)
            
            if flatten_all:
                # Flatten all - keep only the filename
                new_path = os.path.basename(original_path)
                handle.rename_file(i, new_path)
            elif skip_parent_folder:
                # Remove only the first directory from the path
                path_parts = original_path.split('/', 1)
                if len(path_parts) > 1:
                    new_path = path_parts[1]
                    handle.rename_file(i, new_path)
        
        # Generate download ID from torrent info hash
        download_id = str(torrent_info.info_hash())[:16]
        active_downloads[download_id] = handle
        download_info[download_id] = {
            "status": "downloading",
            "progress": 0,
            "name": torrent_info.name(),
            "download_rate": 0,
            "upload_rate": 0,
            "path": internal_path,
            "start_time": datetime.now().timestamp()
        }
        
        return {
            "download_id": download_id,
            "message": "Download started"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting download: {str(e)}")


def _build_progress(download_id: str, handle) -> dict:
    """Compute a fresh progress snapshot for a handle and cache it in download_info.

    Preserves start_time / path / save_dir across polls. Never raises — returns a
    dict whose 'status' reflects errors so list endpoints can include every item.
    """
    prev = download_info.get(download_id, {})

    if not handle.is_valid():
        info = {**prev, "status": "error", "error": "Download handle is no longer valid"}
        download_info[download_id] = info
        active_downloads.pop(download_id, None)
        return info

    status = handle.status()

    if status.error:
        info = {
            **prev,
            "status": "error",
            "progress": status.progress * 100,
            "name": status.name or prev.get("name", "Unknown"),
            "download_rate": 0, "upload_rate": 0,
            "num_seeds": 0, "num_peers": 0,
            "total_download": 0, "total_upload": 0,
            "error": str(status.error),
        }
        download_info[download_id] = info
        active_downloads.pop(download_id, None)
        return info

    # Size of just the files we're actually downloading (priority > 0)
    total_size = 0
    try:
        torrent_info = handle.torrent_file()
        if torrent_info:
            file_storage = torrent_info.files()
            for i in range(torrent_info.num_files()):
                if handle.file_priority(i) > 0:
                    total_size += file_storage.file_size(i)
            total_size = total_size / (1024 * 1024)  # MB
            if total_size == 0:
                total_size = torrent_info.total_size() / (1024 * 1024)
    except Exception:
        pass

    eta_seconds = 0
    if status.download_rate > 0 and total_size > 0:
        remaining_bytes = (total_size - status.total_download / (1024 * 1024)) * 1024 * 1024
        eta_seconds = max(0, int(remaining_bytes / status.download_rate))

    start_time = prev.get("start_time")
    elapsed_seconds = int(datetime.now().timestamp() - start_time) if start_time else 0

    completed = status.is_seeding or status.progress >= 1.0
    info = {
        "status": "completed" if completed else "downloading",
        "progress": status.progress * 100,
        "name": status.name or prev.get("name", "Fetching metadata…"),
        "download_rate": status.download_rate / 1024,  # KB/s
        "upload_rate": status.upload_rate / 1024,       # KB/s
        "num_seeds": status.num_seeds,
        "num_peers": status.num_peers,
        "total_download": status.total_download / (1024 * 1024),  # MB
        "total_upload": status.total_upload / (1024 * 1024),       # MB
        "total_size": total_size,  # MB
        "eta_seconds": eta_seconds,
        "elapsed_seconds": elapsed_seconds,
        "start_time": start_time,
        "path": prev.get("path"),
    }
    download_info[download_id] = info
    return info


@app.get("/api/downloads")
def list_downloads():
    """Return every known download (active, completed, cancelled, errored)."""
    items = []
    for did in list(active_downloads.keys()):
        info = _build_progress(did, active_downloads[did])
        items.append({"id": did, **info})
    # Terminal items that are no longer in the session (cancelled / errored)
    for did, info in download_info.items():
        if did not in active_downloads:
            items.append({"id": did, **info})
    items.sort(key=lambda x: x.get("start_time") or 0)
    return {"downloads": items}


@app.get("/api/progress/{download_id}")
def get_progress(download_id: str):
    """Get download progress for a single torrent."""
    if download_id not in active_downloads:
        if download_id in download_info:
            return {"id": download_id, **download_info[download_id]}
        raise HTTPException(status_code=404, detail="Download not found")
    return {"id": download_id, **_build_progress(download_id, active_downloads[download_id])}


@app.post("/api/dismiss")
def dismiss_download(request: CancelRequest):
    """Remove a finished/errored download from the list WITHOUT deleting files."""
    handle = active_downloads.pop(request.download_id, None)
    if handle is not None:
        try:
            torrent_session.remove_torrent(handle)  # no delete_files -> keep data
        except Exception as e:
            print(f"Error removing torrent on dismiss: {e}")
    download_info.pop(request.download_id, None)
    return {"message": "Download removed from list"}


@app.post("/api/cancel")
def cancel_download(request: CancelRequest):
    """Cancel an active download and delete partial files"""
    if request.download_id not in active_downloads:
        raise HTTPException(status_code=404, detail="Download not found")
    
    handle = active_downloads[request.download_id]
    
    # Get torrent info before removing
    try:
        status = handle.status()
        torrent_info = handle.torrent_file()
        save_path = status.save_path
        
        # Get the name/folder of the download
        if torrent_info:
            download_name = torrent_info.name()
        else:
            download_name = status.name
        
        # Remove torrent from session with delete files option
        torrent_session.remove_torrent(handle, lt.options_t.delete_files)
        
        # Additional cleanup: manually delete the folder/file if it still exists
        if download_name and save_path:
            download_path = os.path.join(save_path, download_name)
            if os.path.exists(download_path):
                try:
                    if os.path.isfile(download_path):
                        os.remove(download_path)
                    elif os.path.isdir(download_path):
                        shutil.rmtree(download_path)
                except Exception as e:
                    print(f"Error deleting files: {e}")
    except Exception as e:
        print(f"Error during cleanup: {e}")
        # Still remove from tracking even if cleanup fails
        torrent_session.remove_torrent(handle)
    
    del active_downloads[request.download_id]
    if request.download_id in download_info:
        download_info[request.download_id]["status"] = "cancelled"
    
    return {"message": "Download cancelled and files deleted"}


@app.get("/api/plex/health")
def check_plex_health():
    """Report Plex connectivity. Always 200 — Plex is optional, so a missing or
    unreachable server is a normal state, not an HTTP error."""
    p = get_plex()
    if p is None:
        return {"connected": False, "message": "Plex server not configured or token missing"}

    try:
        p.library.sections()
        return {"connected": True, "message": "Plex server is connected and working"}
    except Exception as e:
        return {"connected": False, "message": f"Cannot connect to Plex server: {str(e)}"}


@app.get("/api/plex/libraries")
def get_plex_libraries():
    """Get list of Plex libraries"""
    p = get_plex()
    if p is None:
        raise HTTPException(status_code=503, detail="Plex server not configured")

    try:
        libraries = []
        for section in p.library.sections():
            libraries.append({
                "key": section.key,
                "title": section.title,
                "type": section.type
            })
        return {"libraries": libraries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plex/refresh")
def refresh_plex_library(request: PlexRefreshRequest):
    """Refresh a specific Plex library"""
    p = get_plex()
    if p is None:
        raise HTTPException(status_code=503, detail="Plex server not configured")

    try:
        section = p.library.section(request.library_name)
        section.update()
        return {"message": f"Library '{request.library_name}' refresh started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Mount web files
app.mount("/", StaticFiles(directory="web", html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
