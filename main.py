import os
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

# Plex connection from environment variables
plex = None
try:
    plex_url = os.getenv('PLEX_URL', 'http://localhost:32400')
    plex_token = os.getenv('PLEX_TOKEN', '')
    if plex_token:
        # Create a custom session with SSL verification disabled
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        session = requests.Session()
        session.verify = False
        plex = PlexServer(plex_url, plex_token, session=session, timeout=30)
except Exception as e:
    print(f"Warning: Could not connect to Plex: {e}")

# Base path for downloads (internal container path)
BASE_PATH = "/downloads"

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


class CancelRequest(BaseModel):
    download_id: str


class PlexRefreshRequest(BaseModel):
    library_name: str


class TorrentInfoRequest(BaseModel):
    magnet_link: str = None


@app.get("/")
async def read_index():
    return FileResponse('static/index.html')


@app.get("/api/search/nyaa")
async def search_nyaa(query: str):
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
async def get_torrent_info(request: TorrentInfoRequest):
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
        for i in range(torrent_info.num_files()):
            file_entry = torrent_info.files().at(i)
            files.append({
                'index': i,
                'name': file_entry.path,
                'size': file_entry.size
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
        for i in range(torrent_info.num_files()):
            file_entry = torrent_info.files().at(i)
            files.append({
                'index': i,
                'name': file_entry.path,
                'size': file_entry.size
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
async def get_base_path():
    """Get the default download base path from config"""
    return {
        "base_path": "/",
        "display_path": "/"
    }


@app.get("/api/folders")
async def list_folders(path: str = None):
    """List folders in the given path"""
    # Use config base path if no path provided
    if path is None:
        path = "/"
    
    # Convert display path to internal path
    internal_path = get_internal_path(path)
    
    try:
        # Security: ensure path is absolute and exists
        if not os.path.isabs(internal_path):
            internal_path = os.path.abspath(internal_path)
        
        # Security: prevent navigating above BASE_PATH
        if not internal_path.startswith(BASE_PATH):
            raise HTTPException(status_code=403, detail="Access denied: Cannot navigate outside download directory")
        
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
        
        parent_internal = os.path.dirname(internal_path) if internal_path != BASE_PATH else None
        parent_display = get_display_path(parent_internal) if parent_internal else None
        
        return {
            "current_path": path,
            "display_path": path,
            "parent_path": parent_display,
            "folders": folders,
            "files": files,
            "folder_count": len(folders),
            "file_count": len(files)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download")
async def start_download(request: MagnetRequest):
    """Start downloading a torrent from magnet link"""
    try:
        # Validate magnet link format
        if not request.magnet_link or not request.magnet_link.startswith('magnet:'):
            raise HTTPException(status_code=400, detail="Invalid magnet link format")
        
        # Convert display path to internal path
        internal_path = get_internal_path(request.download_path)
        
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
        
        # Handle skip parent folder option
        if request.skip_parent_folder and handle.has_metadata():
            # This is done by renaming files to remove the first directory component
            torrent_info = handle.torrent_file()
            if torrent_info:
                for i in range(torrent_info.num_files()):
                    file_entry = torrent_info.files().at(i)
                    original_path = file_entry.path
                    # Remove the first directory from the path
                    path_parts = original_path.split('/', 1)
                    if len(path_parts) > 1:
                        new_path = path_parts[1]
                        handle.rename_file(i, new_path)
        
        # Generate download ID
        download_id = str(hash(request.magnet_link))[:16]
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
    skip_parent_folder: bool = Form(False)
):
    """Start downloading a torrent from an uploaded .torrent file"""
    try:
        # Validate file extension
        if not file.filename.endswith('.torrent'):
            raise HTTPException(status_code=400, detail="Invalid file type. Please upload a .torrent file")
        
        # Convert display path to internal path
        internal_path = get_internal_path(download_path)
        
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
        
        # Handle skip parent folder option
        if skip_parent_folder:
            for i in range(torrent_info.num_files()):
                file_entry = torrent_info.files().at(i)
                original_path = file_entry.path
                # Remove the first directory from the path
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


@app.get("/api/progress/{download_id}")
async def get_progress(download_id: str):
    """Get download progress for a specific torrent"""
    if download_id not in active_downloads:
        raise HTTPException(status_code=404, detail="Download not found")
    
    handle = active_downloads[download_id]
    
    # Check if handle is valid
    if not handle.is_valid():
        # Remove invalid handle
        del active_downloads[download_id]
        if download_id in download_info:
            download_info[download_id]["status"] = "error"
        raise HTTPException(status_code=410, detail="Download failed or was removed")
    
    status = handle.status()
    
    # Check for errors
    if status.error:
        error_msg = status.error
        download_info[download_id] = {
            "status": "error",
            "progress": status.progress * 100,
            "name": status.name or "Unknown",
            "download_rate": 0,
            "upload_rate": 0,
            "num_seeds": 0,
            "num_peers": 0,
            "total_download": 0,
            "total_upload": 0,
            "error": error_msg
        }
        # Remove from active downloads
        del active_downloads[download_id]
        return download_info[download_id]
    
    # Get total size from torrent info (only for files with priority > 0)
    total_size = 0
    try:
        torrent_info = handle.torrent_file()
        if torrent_info:
            num_files = torrent_info.num_files()
            # Calculate size only for files that are being downloaded
            for i in range(num_files):
                if handle.file_priority(i) > 0:
                    file_entry = torrent_info.files().at(i)
                    total_size += file_entry.size
            total_size = total_size / (1024 * 1024)  # Convert to MB
            
            # If no files have priority set (all selected), use total size
            if total_size == 0:
                total_size = torrent_info.total_size() / (1024 * 1024)
    except:
        pass
    
    # Calculate ETA
    eta_seconds = 0
    if status.download_rate > 0 and total_size > 0:
        remaining_mb = total_size - (status.total_download / (1024 * 1024))
        remaining_bytes = remaining_mb * 1024 * 1024
        eta_seconds = int(remaining_bytes / status.download_rate)
    
    # Calculate elapsed time
    elapsed_seconds = 0
    start_time = None
    if download_id in download_info and 'start_time' in download_info[download_id]:
        start_time = download_info[download_id]['start_time']
        elapsed_seconds = int(datetime.now().timestamp() - start_time)
    
    info = {
        "status": "downloading" if not status.is_seeding else "completed",
        "progress": status.progress * 100,
        "name": status.name,
        "download_rate": status.download_rate / 1024,  # KB/s
        "upload_rate": status.upload_rate / 1024,  # KB/s
        "num_seeds": status.num_seeds,
        "num_peers": status.num_peers,
        "total_download": status.total_download / (1024 * 1024),  # MB
        "total_upload": status.total_upload / (1024 * 1024),  # MB
        "total_size": total_size,  # MB
        "eta_seconds": eta_seconds,  # Estimated time remaining in seconds
        "elapsed_seconds": elapsed_seconds,  # Time elapsed since download started
        "start_time": start_time,  # Preserve start time for future calculations
    }
    
    download_info[download_id] = info
    
    # Auto-cleanup if completed
    if status.is_seeding and status.progress >= 1.0:
        info["status"] = "completed"
    
    return info


@app.post("/api/cancel")
async def cancel_download(request: CancelRequest):
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
async def check_plex_health():
    """Check if Plex server is accessible and working"""
    if plex is None:
        raise HTTPException(status_code=503, detail="Plex server not configured or token missing")
    
    try:
        # Try to access the library sections to verify connection
        plex.library.sections()
        return {
            "status": "ok",
            "message": "Plex server is connected and working"
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot connect to Plex server: {str(e)}")


@app.get("/api/plex/libraries")
async def get_plex_libraries():
    """Get list of Plex libraries"""
    if plex is None:
        raise HTTPException(status_code=503, detail="Plex server not configured")
    
    try:
        libraries = []
        for section in plex.library.sections():
            libraries.append({
                "key": section.key,
                "title": section.title,
                "type": section.type
            })
        return {"libraries": libraries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plex/refresh")
async def refresh_plex_library(request: PlexRefreshRequest):
    """Refresh a specific Plex library"""
    if plex is None:
        raise HTTPException(status_code=503, detail="Plex server not configured")
    
    try:
        section = plex.library.section(request.library_name)
        section.update()
        
        # Clean up any completed downloads from active tracking
        # This helps free up memory for completed downloads
        completed_ids = []
        for download_id, info in download_info.items():
            if info.get('status') == 'completed':
                completed_ids.append(download_id)
        
        for download_id in completed_ids:
            if download_id in active_downloads:
                del active_downloads[download_id]
            if download_id in download_info:
                del download_info[download_id]
        
        return {"message": f"Library '{request.library_name}' refresh started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    import uvicorn
    # Create static directory if it doesn't exist
    os.makedirs("static", exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)
