import os
import re
import subprocess
import requests

from flask import Flask, request, jsonify
from google.cloud import storage  # Make sure you have google-cloud-storage installed

app = Flask(__name__)

@app.route("/create_video", methods=["POST"])
def create_video_endpoint():
    """
    Expects JSON of the form:
    {
      "images_urls": ["https://storage.googleapis.com/my-bucket/image_1.jpg", ...],
      "audio_url": "https://storage.googleapis.com/my-bucket/audio.mp3",
      "subtitle_url": "https://storage.googleapis.com/my-bucket/subtitle.srt",
      "output_name": "final_video.mp4",         # optional name for the ephemeral output
      "bucket_name": "my-bucket",              # which GCS bucket to upload to
      "gcs_output_path": "output/final.mp4"    # path/key in GCS for the final file
    }
    """

    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    # Grab the input data
    images_urls = data.get("images_urls", [])
    audio_url = data.get("audio_url")
    subtitle_url = data.get("subtitle_url")
    output_name = data.get("output_name", "final_video.mp4")
    bucket_name = data.get("bucket_name")
    gcs_output_path = data.get("gcs_output_path", output_name)

    if not images_urls or not audio_url or not subtitle_url or not bucket_name:
        return jsonify({"error": "Missing required fields"}), 400

    # 1. We must download subtitles to ephemeral local disk (FFmpeg filter limitation)
    local_subtitle_path = "/tmp/subtitle.srt"
    download_to_tmp(subtitle_url, local_subtitle_path)

    # 2. Build concat script in memory for images
    #    Example: each image has a fixed 2s just to show how it works
    concat_lines = []
    for url in images_urls:
        concat_lines.append(f"file '{url}'")
        concat_lines.append("duration 2")

    concat_script = "\n".join(concat_lines) + "\n"

    # 3. Run ffmpeg. 
    local_output_path = f"/tmp/{output_name}"  # ephemeral output in container
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        # For remote URLs in concat
        "-protocol_whitelist", "file,pipe,http,https,tcp,tls,crypto",
        "-f", "concat",
        "-safe", "0",
        "-i", "-",               # read concat script from stdin
        "-i", audio_url,         # remote audio
        "-vf", f"subtitles={local_subtitle_path}",  # ephemeral .srt
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-strict", "experimental",
        "-b:a", "192k",
        local_output_path
    ]

    try:
        proc = subprocess.run(ffmpeg_cmd, input=concat_script, text=True, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg failed",
            "stdout": e.stdout,
            "stderr": e.stderr
        }), 500
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500

    # 4. Upload ephemeral final_video.mp4 to the same GCS bucket
    try:
        gcs_url = upload_to_gcs(local_output_path, bucket_name, gcs_output_path)
    except Exception as ex:
        return jsonify({"error": f"Failed uploading to GCS: {ex}"}), 500

    # 5. Clean up ephemeral files
    if os.path.exists(local_subtitle_path):
        os.remove(local_subtitle_path)
    if os.path.exists(local_output_path):
        os.remove(local_output_path)

    return jsonify({
        "message": "Video created successfully",
        "gcs_url": gcs_url
    }), 200


def download_to_tmp(url, local_path):
    """Download a remote file (like subtitles) to ephemeral local storage."""
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

def upload_to_gcs(local_file_path, bucket_name, gcs_dest_path):
    """
    Upload ephemeral file to GCS. Returns the https link to the uploaded file.
    Requires google-cloud-storage and correct authentication.
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_dest_path)
    blob.upload_from_filename(local_file_path)
    # If you want it to be publicly readable:
    # blob.make_public()
    return f"https://storage.googleapis.com/{bucket_name}/{gcs_dest_path}"


if __name__ == "__main__":
    # For local testing, run: 
    #   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
    #   python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
