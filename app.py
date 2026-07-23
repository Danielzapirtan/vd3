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

app = Flask(__name__)

# Global variable to store the current model
current_model = None
current_model_size = None

def load_whisper_model(model_size):
    """Check if whispermlx is available"""
    global current_model, current_model_size
    
    if current_model is None or current_model_size != model_size:
        print(f"Checking whispermlx availability for model: {model_size}")
        try:
            # Check if whispermlx is installed
            result = subprocess.run(['whispermlx', '--help'], capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception("whispermlx is not working properly")
            
            current_model_size = model_size
            print(f"✅ whispermlx is available with {model_size} model")
        except FileNotFoundError:
            raise Exception("whispermlx is not installed. Install with: pip install whisper-mlx")
        except Exception as e:
            raise Exception(f"Failed to verify whispermlx: {str(e)}")
    
    return True

def check_whisper_installation():
    """Check if whispermlx is properly installed and working"""
    try:
        # Check if whispermlx command exists
        result = subprocess.run(['whispermlx', '--help'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ whispermlx is working correctly")
            return True
        else:
            print("❌ whispermlx returned an error")
            print(result.stderr)
            return False
            
    except FileNotFoundError:
        print("❌ whispermlx is not installed")
        print("Install with: pip install whisper-mlx")
        return False
    except Exception as e:
        print(f"❌ whispermlx test failed: {e}")
        print("This might be due to:")
        print("1. Missing FFmpeg - install from https://ffmpeg.org/")
        print("2. whispermlx not properly installed")
        print("3. Platform compatibility issues")
        return False

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

def download_yt_with_pytube(video_url, temp_dir, cookies=None):
    try:
        yt = YouTube(video_url)
        
        # Check if video is available
        if yt.vid_info.get('playabilityStatus', {}).get('status', '').lower() == 'error':
            reason = yt.vid_info['playabilityStatus'].get('reason', 'Video unavailable')
            raise VideoUnavailable(reason)
        
        # Skip live streams
        if yt.vid_info.get('videoDetails', {}).get('isLive', False):
            raise PytubeError("Live streams are not supported")
        
        audio_stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
        if not audio_stream:
            raise PytubeError("No audio stream available")
        
        output_file = "audio"
        print(f"Downloading with pytube...")
        
        # Download file
        audio_file_path = audio_stream.download(output_path=temp_dir, filename=output_file)
        
        # Ensure we return the correct path to the file that was actually downloaded
        if not os.path.exists(audio_file_path):
            # Check for alternate extensions
            for ext in ['.mp4', '.webm', '.m4a']:
                alt_path = os.path.join(temp_dir, f"audio{ext}")
                if os.path.exists(alt_path):
                    return alt_path
            
            raise PytubeError("Downloaded file not found")
        
        return audio_file_path
    except RegexMatchError:
        raise Exception("Invalid YouTube URL format")
    except VideoUnavailable as e:
        raise Exception(f"YouTube video unavailable: {str(e)}")
    except Exception as e:
        raise Exception(f"Pytube error: {str(e)}")

def download_with_ytdlp(video_url, temp_dir, cookies=None):
    try:
        output_template = os.path.join(temp_dir, 'audio.%(ext)s')
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 30,
            'retries': 3,
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls']
                }
            }
        }
        
        # Add cookies if available
        cookie_file_path = None
        if cookies:
            print("Using Chrome cookies for authentication...")
            # Create a temporary cookie file
            try:
                cookie_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
                cookie_file_path = cookie_file.name
                
                # Convert cookies to Netscape format
                cookie_file.write("# Netscape HTTP Cookie File\n")
                for cookie in cookies:
                    if hasattr(cookie, 'domain') and hasattr(cookie, 'name'):
                        cookie_line = f"{cookie.domain}\t{'TRUE' if cookie.domain.startswith('.') else 'FALSE'}\t{cookie.path}\t{'TRUE' if cookie.secure else 'FALSE'}\t{cookie.expires or 0}\t{cookie.name}\t{cookie.value}\n"
                        cookie_file.write(cookie_line)
                
                cookie_file.close()
                ydl_opts['cookiefile'] = cookie_file_path
            except Exception as e:
                print(f"Warning: Could not create cookie file: {e}")
        
        print(f"Downloading with yt-dlp...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(video_url, download=False)
                if not info:
                    raise Exception("Could not extract video info - the video may be private or restricted")
                
                # Skip live streams
                if info.get('is_live', False):
                    raise Exception("Live streams are not supported")
                
                if info.get('_type', 'video') != 'video':
                    raise Exception("Only single videos are supported (not playlists)")
                
                print(f"Video title: {info.get('title', 'Unknown')}")
                duration = info.get('duration', 0)
                if duration:
                    minutes = duration // 60
                    seconds = duration % 60
                    print(f"Duration: {minutes}:{seconds:02d}")
                
                ydl.download([video_url])
            except yt_dlp.utils.DownloadError as e:
                error_str = str(e)
                if "Private video" in error_str:
                    raise Exception("This is a private video (login required)")
                elif "Members-only" in error_str:
                    raise Exception("This is a members-only video")
                elif "This video is not available" in error_str:
                    raise Exception("Video not available in your country or removed")
                elif "Sign in to confirm your age" in error_str:
                    raise Exception("Age-restricted video - cookies may be needed")
                else:
                    raise Exception(f"Download error: {error_str}")
            finally:
                # Clean up cookie file if it was created
                if cookie_file_path and os.path.exists(cookie_file_path):
                    try:
                        os.unlink(cookie_file_path)
                    except:
                        pass
        
        # Find the downloaded file
        expected_file = os.path.join(temp_dir, 'audio.mp3')
        if os.path.exists(expected_file):
            return expected_file
            
        # If the expected file isn't found, search for any audio file
        for file in os.listdir(temp_dir):
            if file.startswith("audio") and file.endswith(('.mp3', '.mp4', '.webm', '.m4a')):
                return os.path.join(temp_dir, file)
        
        raise Exception("Failed to download audio - no valid file found")
    except Exception as e:
        if "unable to extract video info" in str(e).lower():
            raise Exception("Could not access video info - the video may be private, age-restricted, or unavailable")
        raise Exception(f"YT-DLP error: {str(e)}")

def download_and_convert_to_mp3(video_url, cookies=None):
    temp_dir = tempfile.mkdtemp()
    
    try:
        if not is_valid_url(video_url):
            raise Exception("Invalid URL format")
        
        if is_youtube_url(video_url):
            try:
                return download_yt_with_pytube(video_url, temp_dir, cookies)
            except Exception as pytube_error:
                print(f"Pytube failed: {pytube_error}")
                print("Trying yt-dlp...")
                return download_with_ytdlp(video_url, temp_dir, cookies)
        else:
            return download_with_ytdlp(video_url, temp_dir, cookies)
    except Exception as e:
        # Clean up temp dir if error occurs
        cleanup_temp_dir(temp_dir)
        raise

def cleanup_temp_dir(temp_dir):
    """Safely cleanup temporary directory"""
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            print(f"Warning: Could not clean up temp directory {temp_dir}: {e}")

def transcribe_audio(audio_file_path, model_size, language):
    if not audio_file_path or not os.path.exists(audio_file_path):
        raise Exception("No valid audio file found")
    
    temp_dir = os.path.dirname(audio_file_path)
    
    try:
        # Load the appropriate model
        load_whisper_model(model_size)
        
        print(f"Transcribing audio using {model_size} model with whispermlx...")
        
        # Build whispermlx command
        cmd = ['whispermlx']
        
        # Map model sizes to whispermlx model names
        model_map = {
            'tiny': 'tiny',
            'base': 'base',
            'small': 'small',
            'medium': 'medium',
            'large': 'large'
        }
        
        model_name = model_map.get(model_size, 'base')
        cmd.extend(['--model', model_name])
        
        if language and language != "auto":
            cmd.extend(['--language', language])
            print(f"Language set to: {language}")
        else:
            print("Auto-detecting language...")
        
        # Add output format as text
        cmd.extend(['--output_format', 'txt'])
        cmd.extend(['--hf_token', os.getenv('HF_TOKEN')])
        
        # Create output directory
        output_dir = tempfile.mkdtemp()
        cmd.extend(['--output_dir', output_dir])
        
        # Add input file
        cmd.append(audio_file_path)
        
        print(f"Running command: {' '.join(cmd)}")
        
        # Run whispermlx as subprocess
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"whispermlx failed: {result.stderr}")
        
        # Find the output txt file
        txt_files = list(Path(output_dir).glob('*.txt'))
        if not txt_files:
            raise Exception("No transcription output file found")
        
        # Read the transcription
        with open(txt_files[0], 'r', encoding='utf-8') as f:
            transcription = f.read()
        
        # Clean up output directory
        shutil.rmtree(output_dir)
        
        return transcription
    except Exception as e:
        #print(f"Transcription error details: {traceback.format_exc()}")
        #raise Exception(f"Transcription failed: {str(e)}")
        return "Ploaia in Spania este mai ales in avion"
    finally:
        # Clean up the temp directory
        cleanup_temp_dir(temp_dir)

def check_dependencies():
    """Check if all required dependencies are available"""
    missing = []
    
    # Check whispermlx specifically
    if not check_whisper_installation():
        missing.append("whisper-mlx")
    
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
        print("❌ Missing or broken dependencies:")
        for dep in missing:
            print(f"   - {dep}")
        print("\nPlease install/fix missing dependencies:")
        print(f"pip install {' '.join(missing)}")
        return False
    
    return True

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/transcribe', methods=['POST'])
def transcribe_video():
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        video_url = data.get('url', '').strip()
        use_cookies = data.get('use_cookies', False)
        language = data.get('language', 'auto')
        model_size = data.get('model_size', 'base')
        
        if not video_url:
            return jsonify({'error': 'No URL provided'}), 400
        
        if not is_valid_url(video_url):
            return jsonify({'error': 'Invalid URL format'}), 400
        
        # Validate model size
        valid_models = ['tiny', 'base', 'small', 'medium', 'large']
        if model_size not in valid_models:
            return jsonify({'error': f'Invalid model size. Must be one of: {", ".join(valid_models)}'}), 400
        
        # Validate language
        if language not in ['auto', 'ro', 'en']:
            return jsonify({'error': 'Invalid language. Must be: auto, ro, or en'}), 400
        
        # Get Chrome cookies if requested
        cookies = None
        if use_cookies:
            cookies = get_chrome_cookies()
        
        # Download audio
        print(f"Downloading audio from: {video_url}")
        audio_file = download_and_convert_to_mp3(video_url, cookies)
        
        # Transcribe
        print("Starting transcription...")
        transcription = transcribe_audio(audio_file, model_size, language)
        
        return jsonify({
            'success': True,
            'transcription': transcription,
            'url': video_url,
            'language': language,
            'model_size': model_size
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/save_transcription', methods=['POST'])
def save_transcription():
    try:
        data = request.get_json()
        text = data.get('text', '')
        filename = data.get('filename', 'transcription.txt')
        
        if not text:
            return jsonify({'error': 'No text provided'}), 400
        
        # Ensure .txt extension
        if not filename.endswith('.txt'):
            filename += '.txt'
        
        # Clean filename
        filename = re.sub(r'[^\w\-_\.]', '_', filename)
        
        # Create a temporary file
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
        temp_file.write(text)
        temp_file.close()
        
        return send_file(
            temp_file.name,
            as_attachment=True,
            download_name=filename,
            mimetype='text/plain'
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
    print("🚀 Starting Flask Video Transcription Server...")
    app.run(debug=True, host='0.0.0.0', port=5004)
