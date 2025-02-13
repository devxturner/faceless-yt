import os
import re
import subprocess
import requests

from flask import Flask, request, jsonify
from google.cloud import storage

app = Flask(__name__)

def create_slideshow_video(image_urls, audio_url, subtitle_url, output_name="final_video.mp4"):
    # 1. Download the .srt to an ephemeral file so the subtitles filter can handle it
    subtitle_path = "/tmp/subtitle.srt"
    download_to_tmp(subtitle_url, subtitle_path)

    # 2. Build the concat script in-memory for images
    #    Still referencing remote URLs for images, so no local download needed.
    concat_lines = []
    # (You might have your own logic to compute durations; example is minimal)
    # For demonstration, assume each image gets 2s:
    for url in image_urls:
        concat_lines.append(f"file '{url}'")
        concat_lines.append("duration 2")

    concat_script = "\n".join(concat_lines) + "\n"

    # 3. Run ffmpeg. Note that for subtitles, we use the local ephemeral file:
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        # Whitelist needed protocols for concat to fetch remote images:
        "-protocol_whitelist", "file,pipe,http,https,tcp,tls,crypto",
        "-f", "concat",
        "-safe", "0",
        "-i", "-",            # read concat script from stdin
        "-i", audio_url,      # remote audio
        "-vf", f"subtitles={subtitle_path}",  # local ephemeral srt
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-strict", "experimental",
        "-b:a", "192k",
        output_name
    ]

    proc = subprocess.run(
        ffmpeg_cmd,
        input=concat_script,  # pass the concat text in
        text=True,
        capture_output=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")

    # 4. Clean up ephemeral subtitle file
    if os.path.exists(subtitle_path):
        os.remove(subtitle_path)

    return output_name


def download_to_tmp(url, local_path):
    """Download a remote file to an ephemeral local path (/tmp/...)."""
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


# Example route
@app.route("/create_video", methods=["POST"])
def create_video_endpoint():
    data = request.get_json()
    images_urls = data.get("images_urls", [])
    audio_url = data.get("audio_url")
    subtitle_url = data.get("subtitle_url")
    output_name = data.get("output_name", "final_video.mp4")

    try:
        final_path = create_slideshow_video(images_urls, audio_url, subtitle_url, output_name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"message": "Success", "video_path": final_path}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
