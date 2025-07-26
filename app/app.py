import os
import uuid
import traceback
import requests
import re
import io
from flask import Flask, request, send_file, render_template
from pydub import AudioSegment
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC

# ffmpeg path
AudioSegment.converter = r"C:\ffmpeg\ffmpeg-7.1.1-essentials_build\bin\ffmpeg.exe"

app = Flask(__name__)
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('youtube_url', '').strip()
    if not url.startswith("http"):
        return "Error: Invalid YouTube URL", 400

    try:
        # Temporary .webm filename
        temp_filename = os.path.join(DOWNLOAD_FOLDER, f"{uuid.uuid4()}.webm")

        # yt-dlp options
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_filename,
            'quiet': True,
        }

        # Download and get metadata
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)

        # Extract metadata
        artist = info_dict.get('artist') or info_dict.get('uploader') or ''
        title = info_dict.get('title') or 'audio'

        # Clean title and remove extra words
        if artist and artist.lower() in title.lower():
            idx = title.lower().find(artist.lower())
            if idx != -1:
                title = title[:idx] + title[idx + len(artist):]

        bad_words = [
            "official music video", "official lyric video",
            "official video", "lyric video", "lyrics", "mv"
        ]
        for bad_word in bad_words:
            idx = title.lower().find(bad_word)
            if idx != -1:
                title = title[:idx] + title[idx + len(bad_word):]

        title = re.sub(r'[-|]+$', '', title).strip()
        title = re.sub(r'\s{2,}', ' ', title)

        # Sanitize
        safe_artist = "".join(c for c in artist if c.isalnum() or c in [' ', '-', '_']).strip()
        safe_title = "".join(c for c in title if c.isalnum() or c in [' ', '-', '_']).strip()
        safe_title = safe_title.rstrip('-').strip()

        final_name = f"{safe_artist} - {safe_title}.mp3" if safe_artist else f"{safe_title}.mp3"

        # ✅ Convert to MP3 in memory
        buffer = io.BytesIO()
        sound = AudioSegment.from_file(temp_filename)
        sound.export(buffer, format="mp3")
        buffer.seek(0)  # Move pointer to start
        os.remove(temp_filename)  # Clean up the .webm file

        # ✅ Add ID3 tags and album art in memory
        audio = EasyID3()
        audio['title'] = safe_title
        if safe_artist:
            audio['artist'] = safe_artist

        # Save tags into buffer
        audio.save(buffer)
        buffer.seek(0)

        # Add cover art
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

        return send_file(buffer, as_attachment=True, download_name=final_name, mimetype='audio/mpeg')

    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error(tb)
        return f"An error occurred: {e}"

if __name__ == '__main__':
    app.run(debug=True)
