import os
import uuid
import traceback
import requests
import re
import io
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, send_file, render_template_string, jsonify
from pydub import AudioSegment
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC

# ffmpeg path (adjust to your environment)
AudioSegment.converter = r"C:\ffmpeg\ffmpeg-7.1.1-essentials_build\bin\ffmpeg.exe"

app = Flask(__name__)
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# --- Simple HTML template with textarea + playlist input ---
INDEX_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>YouTube to MP3 Batch</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  </head>
  <body class="bg-light">
    <div class="container py-5">
      <h1 class="mb-4">YouTube → MP3 (batch)</h1>

      <form method="post" action="/download">
        <div class="mb-3">
          <label class="form-label">Playlist URL (optional)</label>
          <input type="text" name="playlist_url" class="form-control" placeholder="https://www.youtube.com/playlist?list=...">
        </div>

        <div class="mb-3">
          <label class="form-label">Or paste multiple video links (one per line)</label>
          <textarea name="youtube_urls" rows="6" class="form-control" placeholder="https://youtu.be/abc123\nhttps://youtu.be/xyz789"></textarea>
        </div>

        <div class="mb-3">
          <label class="form-label">Parallel workers (optional)</label>
          <input type="number" name="workers" class="form-control" placeholder="Default: 4" min="1" max="16">
        </div>

        <button type="submit" class="btn btn-primary">Download ZIP</button>
      </form>

      <hr>
      <p class="text-muted small">Notes: Supports playlist URL or newline-separated links. Packaging results into a single ZIP. Failed items are recorded in <code>errors.txt</code>.</p>
    </div>
  </body>
</html>
"""


def sanitize(text: str) -> str:
    """Keep only alphanumeric, space and underscore. Remove dashes and collapse whitespace."""
    if not text:
        return ''
    cleaned = "".join(c for c in text if c.isalnum() or c in [' ', '_']).strip()
    cleaned = re.sub(r"\s{2,}", ' ', cleaned)
    return cleaned


def extract_links_from_playlist(playlist_url: str):
    """Use yt_dlp to extract video entries from a playlist URL (no download)."""
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'extract_flat': True,  # faster extraction
    }
    urls = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(playlist_url, download=False)
            # info['entries'] may contain dicts with 'url' or 'id'
            entries = info.get('entries') or []
            for e in entries:
                # prefer full webpage_url if present
                if isinstance(e, dict):
                    if e.get('url') and e.get('ie_key'):
                        # construct a watch link for extraction phase
                        urls.append(f"https://www.youtube.com/watch?v={e['url']}")
                    elif e.get('id'):
                        urls.append(f"https://www.youtube.com/watch?v={e['id']}")
                elif isinstance(e, str):
                    # sometimes flat extracts yield direct ids
                    urls.append(f"https://www.youtube.com/watch?v={e}")
        except Exception:
            # If extract as playlist fails, return empty and let caller handle
            return []
    return urls


def download_and_convert_to_mp3(video_url: str):
    """Download a single video audio, convert to mp3, add tags, and return filename + bytes.
    Returns tuple: (filename, bytes) on success or (None, error_message) on failure.
    """
    temp_webm = None
    temp_mp3 = None
    try:
        # Create unique temp paths
        temp_webm = os.path.join(DOWNLOAD_FOLDER, f"{uuid.uuid4()}.webm")
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_webm,
            'quiet': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)

        artist = info.get('artist') or info.get('uploader') or ''
        title = info.get('title') or 'audio'

        # remove artist from title if present
        if artist and artist.lower() in title.lower():
            idx = title.lower().find(artist.lower())
            if idx != -1:
                title = (title[:idx] + title[idx + len(artist):]).strip()

        # remove some common bad words
        bad_words = [
            "official music video", "official lyric video",
            "official video", "lyric video", "lyrics", "mv", "- -"
        ]
        for bad_word in bad_words:
            idx = title.lower().find(bad_word)
            if idx != -1:
                title = title[:idx] + title[idx + len(bad_word):]

        title = re.sub(r'[-|]+$', '', title).strip()
        title = re.sub(r'\s{2,}', ' ', title)

        safe_artist = sanitize(artist)
        safe_title = sanitize(title)

        final_filename = f"{safe_artist} - {safe_title}.mp3" if safe_artist else f"{safe_title}.mp3"

        # Convert to mp3 using pydub
        sound = AudioSegment.from_file(temp_webm)

        # write to a temp mp3 file to be able to tag it with mutagen easily
        temp_mp3 = os.path.join(DOWNLOAD_FOLDER, f"{uuid.uuid4()}.mp3")
        sound.export(temp_mp3, format='mp3')

        # Add ID3 tags
        try:
            audio = EasyID3(temp_mp3)
        except Exception:
            audio = EasyID3()
        audio['title'] = safe_title
        if safe_artist:
            audio['artist'] = safe_artist
        audio.save(temp_mp3)

        # Add cover art if available
        thumbnail_url = info.get('thumbnail')
        if thumbnail_url:
            try:
                img_data = requests.get(thumbnail_url, timeout=15).content
                tags = ID3(temp_mp3)
                tags.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=img_data
                ))
                tags.save(temp_mp3)
            except Exception:
                # thumbnail failure shouldn't break the whole process
                pass

        # Read mp3 bytes
        with open(temp_mp3, 'rb') as f:
            mp3_bytes = f.read()

        return final_filename, mp3_bytes

    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error(tb)
        return None, f"Failed {video_url}: {e}"

    finally:
        # Clean up temp files if they exist
        for p in (temp_webm, temp_mp3):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


@app.route('/')
def index():
    return render_template_string(INDEX_HTML)


@app.route('/download', methods=['POST'])
def download():
    playlist_url = request.form.get('playlist_url', '').strip()
    urls_text = request.form.get('youtube_urls', '').strip()
    workers = request.form.get('workers')

    # Gather list of links
    links = []

    if playlist_url:
        links = extract_links_from_playlist(playlist_url)
        if not links:
            return "Failed to extract playlist or playlist is empty", 400

    if urls_text:
        # split by lines and strip
        for line in urls_text.splitlines():
            line = line.strip()
            if not line:
                continue
            # accept raw ids too
            if re.match(r'^[A-Za-z0-9_-]{11}$', line):
                links.append(f"https://www.youtube.com/watch?v={line}")
            else:
                links.append(line)

    if not links:
        return "No YouTube links provided", 400

    try:
        workers = int(workers) if workers else 4
    except Exception:
        workers = 4

    workers = max(1, min(workers, 16))

    results = []
    errors = []

    # Process in parallel
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_to_url = {ex.submit(download_and_convert_to_mp3, url): url for url in links}
        for fut in as_completed(future_to_url):
            url = future_to_url[fut]
            try:
                filename, data = fut.result()
                if filename and data:
                    results.append((filename, data))
                else:
                    # returned an error tuple
                    errors.append(data or f"Unknown error for {url}")
            except Exception as e:
                tb = traceback.format_exc()
                app.logger.error(tb)
                errors.append(f"{url} -> {e}")

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for filename, data in results:
            # ensure unique names inside zip
            safe_name = filename
            counter = 1
            while safe_name in zf.namelist():
                name_root, ext = os.path.splitext(filename)
                safe_name = f"{name_root} ({counter}){ext}"
                counter += 1
            zf.writestr(safe_name, data)

        # Add errors.txt if any
        if errors:
            zf.writestr('errors.txt', '\n'.join(errors))

    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name='youtube_batch.zip', mimetype='application/zip')


if __name__ == '__main__':
    app.run(debug=True)
