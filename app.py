from cachetools import TTLCache
from flask import Flask, Response, abort, redirect, jsonify
import yt_dlp
import re

app = Flask(__name__)

STREAMS = {
    1: "https://www.youtube.com/@AlMayadeenLive01/live",
    #2: "",
}

# Cache up to 100 stream URLs for 5 minutes.
stream_cache = TTLCache(maxsize=100, ttl=300)

def redact_urls(message):
    return re.sub(r"https?://\S+", "[URL]", str(message))

def is_h264(format_info):
    return (format_info.get("vcodec") or "").lower().startswith("avc1")

def is_hls(format_info):
    return format_info.get("protocol") in ("m3u8", "m3u8_native")


def best_format(formats):
    return max(
        formats,
        key=lambda f: (
            f.get("height"),
            f.get("tbr"),
            # for audio
            f.get('format_id'),
        ),
    )


def make_master_playlist(video, audio):
    video_codec = video.get("vcodec") or "avc1"
    audio_codec = audio.get("acodec") or "mp4a.40.2"
    bandwidth = int((video.get("tbr") or 2000) * 1000)
    resolution = ""

    if video.get("width") and video.get("height"):
        resolution = f',RESOLUTION={video["width"]}x{video["height"]}'

    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="AAC",'
        f'DEFAULT=YES,AUTOSELECT=YES,URI="{audio["url"]}"\n'
        f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},'
        f'CODECS="{video_codec},{audio_codec}"'
        f'{resolution},AUDIO="audio"\n'
        f'{video["url"]}\n'
    )


def extract_stream(page_url):
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(page_url, download=False)

    formats = [
        f for f in info.get("formats", [])
        if is_hls(f) and f.get("url")
    ]

    # Prefer a single H.264 HLS format that already includes audio.
    combined = [
        f for f in formats
        if is_h264(f) and f.get("acodec") not in (None, "none")
    ]
    if combined:
        return ("redirect", best_format(combined)["url"])

    # Otherwise pair an H.264 video-only HLS format with an AAC audio-only one.
    video_only = [
        f for f in formats
        if is_h264(f) and f.get("acodec") in (None, "none")
    ]
    audio_only = [
        f for f in formats
        if f.get("vcodec") in (None, "none")
        #and (f.get("acodec") or "").lower().startswith("mp4a")
        # no acodec info, use non-empty audio_ext instead, and ignore codec requirments
        and not f.get("audio_ext") in (None, "none")
    ]


    if video_only and audio_only:
        playlist = make_master_playlist(
            best_format(video_only),
            best_format(audio_only),
        )
        return ("playlist", playlist)

    return None


@app.get("/<int:stream_id>")
def get_stream(stream_id):
    page_url = STREAMS.get(stream_id)
    if page_url is None:
        return Response(
                "Invalid stream index.\n",
                status=404,
                mimetype="text/plain",
                )

    try:
        result = stream_cache.get(stream_id)

        if result is None:
            result = extract_stream(page_url)
            if result is None:
                return Response(
                    "No compatible H.264 HLS stream and AAC audio were found.\n",
                    status=404,
                    mimetype="text/plain",
                )
            stream_cache[stream_id] = result

        kind, value = result

        if kind == "redirect":
            return redirect(value, code=302)

        return Response(
            value,
            mimetype="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )
    except yt_dlp.utils.DownloadError as exc:
        message = redact_urls(exc)
        app.logger.error(
            "yt-dlp extraction failed for stream_id=%s: %s",
            stream_id,
            message,
        )
        return jsonify({
            "error": "yt_dlp_extraction_failed",
            "message": message,
        }), 502

    except Exception as exc:
        message = redact_urls(exc)
        app.logger.error(
            "Unexpected stream error for stream_id=%s (%s): %s",
            stream_id,
            type(exc).__name__,
            message,
        )
        return jsonify({
            "error": "stream_processing_failed",
            "message": message,
        }), 500

