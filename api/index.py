from cachetools import TTLCache
from flask import Flask, Response, abort, redirect
import yt_dlp

app = Flask(__name__)

STREAMS = {
    1: "https://www.youtube.com/@AlMayadeenLive01/live",
    #2: "",
}

# Cache up to 100 stream URLs for 5 minutes.
stream_cache = TTLCache(maxsize=100, ttl=300)


def is_h264(format_info):
    return (format_info.get("vcodec") or "").lower().startswith("avc1")


def extract_hls_url(page_url):
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(page_url, download=False)

    candidates = [
        f for f in info.get("formats", [])
        if f.get("protocol") in ("m3u8", "m3u8_native")
        and is_h264(f)
        and f.get("url")
    ]

    if not candidates:
        return None

    # Prefer formats with audio, then higher resolution and bitrate.
    selected = max(
        candidates,
        key=lambda f: (
            f.get("acodec") not in (None, "none"),
            f.get("height") or 0,
            f.get("tbr") or 0,
        ),
    )
    return selected["url"]


@app.get("/")
def index():
    return Response("Try /1 or /2\n", mimetype="text/plain")


@app.get("/<int:stream_id>")
def get_stream(stream_id):
    page_url = STREAMS.get(stream_id)
    if page_url is None:
        abort(404)

    try:
        stream_url = stream_cache.get(stream_id)

        if stream_url is None:
            stream_url = extract_hls_url(page_url)
            if stream_url is None:
                return Response(
                    "No H.264 HLS stream is currently available.\n",
                    status=404,
                    mimetype="text/plain",
                )
            stream_cache[stream_id] = stream_url

        return redirect(stream_url, code=302)

    except Exception:
        app.logger.exception("Failed to extract stream %s", stream_id)
        return Response(
            "Could not extract the live stream.\n",
            status=502,
            mimetype="text/plain",
        )
