from flask import Flask, request, jsonify, render_template, send_file
import os
import sys
import subprocess
import tempfile
from pytube import YouTube
from pytube.exceptions import PytubeError, VideoUnavailable, RegexMatchError
import yt_dlp
import traceback
from pathlib import Path
import re
import browser_cookie3
import requests
import shutil
import json
import time

app = Flask(__name__)

def get_chrome_cookies():
    """Extract cookies from Chrome browser"""
    try:
        print("Extracting Chrome cookies...")
        cookies = browser_cookie3.chrome(domain_name='youtube.com')
        cookie_list = list(cookies)
        print(f"Successfully extracted {len(cookie_list)} YouTube cookies from Chrome")
        return cookie_list
    except Exception as e:
        print(f"Warning: Could not extract Chrome cookies: {e}")
        print("You may need to:")
        print("1. Close Chrome completely")
        print("2. Run this script as administrator/with sudo")
        print("3. Install browser_cookie3: pip install browser_cookie3")
        return None

def is_valid_url(url):
    """Basic URL validation"""
    youtube_regex = (
        r'(https?://)?(www\.)?'
        '(youtube|youtu|youtube-nocookie)\.(com|be)/'
        '(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})')
    
    generic_url_regex = (
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|'
        r'[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
    
    return bool(re.match(youtube_regex, url) or re.match(generic_url_regex, url))

def is_youtube_url(url):
    return "youtube.com" in url or "youtu.be" in url

def get_video_info(video_url, cookies=None):
    """Get video information without downloading"""
    try:
        cookie_file_path = None
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 30,
            'retries': 3,
        }
        
        if cookies:
            try:
                cookie_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
                cookie_file_path = cookie_file.name
                
                cookie_file.write("# Netscape HTTP Cookie File\n")
                for cookie in cookies:
                    if hasattr(cookie, 'domain') and hasattr(cookie, 'name'):
                        cookie_line = f"{cookie.domain}\t{'TRUE' if cookie.domain.startswith('.') else 'FALSE'}\t{cookie.path}\t{'TRUE' if cookie.secure else 'FALSE'}\t{cookie.expires or 0}\t{cookie.name}\t{cookie.value}\n"
                        cookie_file.write(cookie_line)
                
                cookie_file.close()
                ydl_opts['cookiefile'] = cookie_file_path
            except Exception as e:
                print(f"Warning: Could not create cookie file: {e}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            video_info = {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'views': info.get('view_count', 0),
                'uploader': info.get('uploader', 'Unknown'),
                'thumbnail': info.get('thumbnail', ''),
                'description': info.get('description', '')[:200] + '...' if info.get('description') else '',
                'formats': []
            }
            
            # Get available formats
            formats = info.get('formats', [])
            for fmt in formats:
                format_info = {
                    'format_id': fmt.get('format_id', ''),
                    'ext': fmt.get('ext', ''),
                    'resolution': fmt.get('resolution', 'audio only') if fmt.get('height') else 'audio only',
                    'filesize': fmt.get('filesize', 0),
                    'format_note': fmt.get('format_note', ''),
                    'vcodec': fmt.get('vcodec', 'none'),
                    'acodec': fmt.get('acodec', 'none'),
                    'tbr': fmt.get('tbr', 0),  # Total bitrate
                    'fps': fmt.get('fps', 0),
                }
                
                # Only include formats that have either video or audio
                if format_info['vcodec'] != 'none' or format_info['acodec'] != 'none':
                    video_info['formats'].append(format_info)
            
            return video_info
            
    except Exception as e:
        raise Exception(f"Failed to get video info: {str(e)}")
    finally:
        if cookie_file_path and os.path.exists(cookie_file_path):
            try:
                os.unlink(cookie_file_path)
            except:
                pass

def download_video_pytube(video_url, format_id, temp_dir, cookies=None):
    """Download video using pytube"""
    try:
        yt = YouTube(video_url)
        
        if not format_id or format_id == 'best':
            # Get highest quality progressive stream (video + audio in one file)
            stream = yt.streams.filter(progressive=True).order_by('resolution').desc().first()
        elif format_id == 'audio':
            # Audio only
            stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
        else:
            # Specific format
            stream = yt.streams.get_by_itag(format_id)
        
        if not stream:
            raise PytubeError("No suitable stream found")
        
        # Sanitize filename
        safe_title = re.sub(r'[^\w\-_\.]', '_', yt.title)[:100]
        output_file = f"{safe_title}.{stream.subtype}"
        
        print(f"Downloading: {yt.title}")
        print(f"Format: {stream.resolution or 'audio only'} - {stream.subtype}")
        
        # Download the file
        file_path = stream.download(output_path=temp_dir, filename=safe_title)
        
        if not os.path.exists(file_path):
            # Check for the file with original extension
            for f in os.listdir(temp_dir):
                if f.startswith(safe_title):
                    return os.path.join(temp_dir, f)
            raise PytubeError("Downloaded file not found")
        
        return file_path
        
    except RegexMatchError:
        raise Exception("Invalid YouTube URL format")
    except VideoUnavailable as e:
        raise Exception(f"YouTube video unavailable: {str(e)}")
    except Exception as e:
        raise Exception(f"Pytube download error: {str(e)}")

def download_video_ytdlp(video_url, format_id, temp_dir, cookies=None):
    """Download video using yt-dlp"""
    try:
        safe_title = "downloaded_video"
        
        ydl_opts = {
            'outtmpl': os.path.join(temp_dir, '%(title).100s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls']
                }
            }
        }
        
        # Set format selection
        if not format_id or format_id == 'best':
            ydl_opts['format'] = 'best[height<=1080]/best'  # Prefer 1080p or lower for compatibility
        elif format_id == 'audio':
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        else:
            # Try to match format by ID or create a format selector
            ydl_opts['format'] = format_id
        
        # Add cookies if available
        cookie_file_path = None
        if cookies:
            print("Using Chrome cookies for authentication...")
            try:
                cookie_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
                cookie_file_path = cookie_file.name
                
                cookie_file.write("# Netscape HTTP Cookie File\n")
                for cookie in cookies:
                    if hasattr(cookie, 'domain') and hasattr(cookie, 'name'):
                        cookie_line = f"{cookie.domain}\t{'TRUE' if cookie.domain.startswith('.') else 'FALSE'}\t{cookie.path}\t{'TRUE' if cookie.secure else 'FALSE'}\t{cookie.expires or 0}\t{cookie.name}\t{cookie.value}\n"
                        cookie_file.write(cookie_line)
                
                cookie_file.close()
                ydl_opts['cookiefile'] = cookie_file_path
            except Exception as e:
                print(f"Warning: Could not create cookie file: {e}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            safe_title = re.sub(r'[^\w\-_\.]', '_', info.get('title', 'video'))[:100]
            
            # Find the downloaded file
            downloaded_file = None
            for file in os.listdir(temp_dir):
                if file.startswith(safe_title) or file.endswith(('.mp4', '.webm', '.mkv', '.mp3', '.m4a')):
                    downloaded_file = os.path.join(temp_dir, file)
                    break
            
            if not downloaded_file:
                # Search more broadly
                for file in os.listdir(temp_dir):
                    if file.endswith(('.mp4', '.webm', '.mkv', '.mp3', '.m4a', '.flv', '.3gp')):
                        downloaded_file = os.path.join(temp_dir, file)
                        break
            
            if not downloaded_file:
                raise Exception("Downloaded file not found")
            
            return downloaded_file
            
    except Exception as e:
        raise Exception(f"yt-dlp download error: {str(e)}")
    finally:
        if cookie_file_path and os.path.exists(cookie_file_path):
            try:
                os.unlink(cookie_file_path)
            except:
                pass

def download_video(video_url, format_id='best', cookies=None):
    """Main download function"""
    temp_dir = tempfile.mkdtemp()
    
    try:
        if not is_valid_url(video_url):
            raise Exception("Invalid URL format")
        
        if is_youtube_url(video_url):
            try:
                return download_video_pytube(video_url, format_id, temp_dir, cookies)
            except Exception as pytube_error:
                print(f"Pytube failed: {pytube_error}")
                print("Trying yt-dlp...")
                return download_video_ytdlp(video_url, format_id, temp_dir, cookies)
        else:
            return download_video_ytdlp(video_url, format_id, temp_dir, cookies)
    except Exception as e:
        # Clean up temp dir if error occurs
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
        raise

def check_dependencies():
    """Check if required dependencies are available"""
    missing = []
    
    try:
        import yt_dlp
    except ImportError:
        missing.append("yt-dlp")
    
    try:
        import pytube
    except ImportError:
        missing.append("pytube")
    
    try:
        import browser_cookie3
    except ImportError:
        missing.append("browser_cookie3")
    
    if missing:
        print("❌ Missing dependencies:")
        for dep in missing:
            print(f"   - {dep}")
        print(f"\nPlease install: pip install {' '.join(missing)}")
        return False
    
    return True

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_info', methods=['POST'])
def get_info():
    """Get video information"""
    try:
        data = request.get_json()
        video_url = data.get('url', '').strip()
        use_cookies = data.get('use_cookies', False)
        
        if not video_url:
            return jsonify({'error': 'No URL provided'}), 400
        
        if not is_valid_url(video_url):
            return jsonify({'error': 'Invalid URL format'}), 400
        
        cookies = None
        if use_cookies:
            cookies = get_chrome_cookies()
        
        video_info = get_video_info(video_url, cookies)
        
        return jsonify({
            'success': True,
            'video_info': video_info
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/download', methods=['POST'])
def download():
    """Download video"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        video_url = data.get('url', '').strip()
        format_id = data.get('format_id', 'best')
        use_cookies = data.get('use_cookies', False)
        
        if not video_url:
            return jsonify({'error': 'No URL provided'}), 400
        
        if not is_valid_url(video_url):
            return jsonify({'error': 'Invalid URL format'}), 400
        
        # Get Chrome cookies if requested
        cookies = None
        if use_cookies:
            cookies = get_chrome_cookies()
        
        # Download video
        print(f"Downloading video from: {video_url}")
        print(f"Format: {format_id}")
        
        video_file = download_video(video_url, format_id, cookies)
        
        # Get original filename
        filename = os.path.basename(video_file)
        
        # Determine mimetype
        ext = os.path.splitext(filename)[1].lower()
        mimetype_map = {
            '.mp4': 'video/mp4',
            '.webm': 'video/webm',
            '.mkv': 'video/x-matroska',
            '.mp3': 'audio/mpeg',
            '.m4a': 'audio/mp4',
            '.flv': 'video/x-flv',
            '.3gp': 'video/3gpp',
        }
        mimetype = mimetype_map.get(ext, 'application/octet-stream')
        
        print(f"Sending file: {filename} ({mimetype})")
        
        # Schedule cleanup after send
        '''
        @app.after_request
        def cleanup(response):
            try:
                if os.path.exists(video_file):
                    os.unlink(video_file)
                temp_dir = os.path.dirname(video_file)
                if os.path.exists(temp_dir) and not os.listdir(temp_dir):
                    os.rmdir(temp_dir)
            except Exception as e:
                print(f"Cleanup error: {e}")
            return response
        '''
        return send_file(
            video_file,
            as_attachment=True,
            download_name=filename,
            mimetype=mimetype
        )
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/check_dependencies', methods=['GET'])
def check_dependencies_route():
    """API endpoint to check dependencies"""
    try:
        deps_ok = check_dependencies()
        return jsonify({
            'success': deps_ok,
            'message': 'All dependencies OK' if deps_ok else 'Missing dependencies'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    print("🚀 Starting YouTube Video Downloader Server...")
    print("📋 Features:")
    print("   - Video information preview")
    print("   - Multiple format support")
    print("   - Chrome cookie authentication")
    print("   - Audio extraction")
    app.run(debug=True, host='0.0.0.0', port=5005)
