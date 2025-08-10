import os
import uuid
import traceback
import requests
import re
import io
import zipfile
import time
import random
from flask import Flask, request, send_file, render_template
from pydub import AudioSegment
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC

# ffmpeg path
AudioSegment.converter = r"C:\\ffmpeg\\ffmpeg-7.1.1-essentials_build\\bin\\ffmpeg.exe"

app = Flask(__name__)
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def download_single_video(url):
    try:
        temp_filename = os.path.join(DOWNLOAD_FOLDER, f"{uuid.uuid4()}.webm")

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_filename,
            'quiet': True,
            'noplaylist': False,
            'sleep_interval': 8,  # wait at least 8 seconds between requests
            'max_sleep_interval': 15,  # up to 15 seconds
            'user_agent': random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Mozilla/5.0 (X11; Linux x86_64)"
            ])
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)

        artist = info_dict.get('artist') or info_dict.get('uploader') or ''
        title = info_dict.get('title') or 'audio'

        # clean title
        bad_words = [
            "official music video", "official lyric video",
            "official video", "lyric video", "lyrics", "mv", "music video",
        ]
        for bad_word in bad_words:
            title = re.sub(bad_word, '', title, flags=re.IGNORECASE)
        title = re.sub(r'[-|]+$', '', title).strip()
        title = re.sub(r'\s{2,}', ' ', title)

        safe_artist = "".join(c for c in artist if c.isalnum() or c in [' ', '_']).strip()
        safe_title = "".join(c for c in title if c.isalnum() or c in [' ', '_']).strip()

        final_name = f"{safe_artist} - {safe_title}.mp3" if safe_artist else f"{safe_title}.mp3"

        buffer = io.BytesIO()
        sound = AudioSegment.from_file(temp_filename)
        sound.export(buffer, format="mp3")
        buffer.seek(0)
        os.remove(temp_filename)

        audio = EasyID3()
        audio['title'] = safe_title
        if safe_artist:
            audio['artist'] = safe_artist
        audio.save(buffer)
        buffer.seek(0)

        thumbnail_url = info_dict.get('thumbnail')
        if thumbnail_url:
            img_data = requests.get(thumbnail_url).content
            tags = ID3()
            tags.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,
                desc='Cover',
                data=img_data
            ))
            tags.save(buffer)
            buffer.seek(0)

        return (final_name, buffer)

    except Exception as e:
        return (f"ERROR_{uuid.uuid4()}.txt", io.BytesIO(str(e).encode()))

@app.route('/')
def index():
    return render_template('index.html')

def extract_playlist_urls(url):
    """Extracts all video URLs from a playlist without downloading."""
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,  # Don't download, just get metadata
        'skip_download': True,
        'force_generic_extractor': False
    }
    urls = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        if 'entries' in info_dict:
            for entry in info_dict['entries']:
                if entry and 'url' in entry:
                    urls.append(f"https://www.youtube.com/watch?v={entry['url']}")
        else:
            urls.append(url)  # single video
    return urls

@app.route('/get_urls', methods=['POST'])
def get_urls():
    """Accepts a single URL (video or playlist) and returns all video URLs."""
    url = request.form.get('youtube_url', '').strip()
    if not url:
        return {"error": "No YouTube URL provided"}, 400
    urls = extract_playlist_urls(url)
    return {"urls": urls}

@app.route('/download', methods=['POST'])
def download():
    """Downloads a single YouTube video as MP3."""
    url = request.form.get('youtube_url', '').strip()
    if not url:
        return "Error: No YouTube URL provided", 400

    filename, file_buffer = download_single_video(url)
    file_buffer.seek(0)

    return send_file(
        file_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='audio/mpeg'
    )

if __name__ == '__main__':
    app.run(debug=True)
