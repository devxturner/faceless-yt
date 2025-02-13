import os
import re
import subprocess
import requests

from flask import Flask, request, jsonify
from google.cloud import storage  # Requires: pip install google-cloud-storage

app = Flask(__name__)

@app.route("/create_video", methods=["POST"])
def create_video_endpoint():
    """
    POST JSON of this form:
    {
      "images_urls": [
        "https://storage.googleapis.com/n8n-faceless-ph/image_1.jpg",
        "https://storage.googleapis.com/n8n-faceless-ph/image_2.jpg",
        ...
      ],
      "audio_url":    "https://storage.googleapis.com/n8n-faceless-ph/audio.mp3",
      "subtitle_url": "https://storage.googleapis.com/n8n-faceless-ph/subtitle.srt",

      "output_name":  "final_video.mp4",  # ephemeral local name, optional
      "bucket_name":  "n8n-faceless-ph",  # GCS bucket name
      "gcs_output_path": "final_video.mp4" 
          # The final object name in GCS. 
          # e.g. "final_video.mp4" for bucket root 
          # (no "output/" folder in GCS).
    }
    """

    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON payload"}), 400

    # 1) Collect form data
    images_urls = data.get("images_urls", [])
    audio_url   = data.get("audio_url")
    subtitle_url= data.get("subtitle_url")
    output_name = data.get("output_name", "final_video.mp4")  # ephemeral local filename
    bucket_name = data.get("bucket_name")
    gcs_output_path = data.get("gcs_output_path", output_name)

    if not images_urls or not audio_url or not subtitle_url or not bucket_name:
        return jsonify({"error": "Missing required fields"}), 400

    # 2) Download the .srt so FFmpeg subtitles filter can read it
    local_subtitle_path = "/tmp/subtitle.srt"
    download_file(subtitle_url, local_subtitle_path)

    temp_video_path = "/tmp/temp_video.mp4"      # ephemeral first-pass slideshow
    local_output_path = f"/tmp/{output_name}"    # ephemeral final pass

    try:
        # A) Parse durations from the SRT
        subtitle_durations, last_subtitle_timestamp = parse_srt_durations(local_subtitle_path)

        # Extend last subtitle by 5s, total final video also +5s
        if subtitle_durations:
            subtitle_durations[-1] += 5
        final_video_duration = last_subtitle_timestamp + 5

        # B) Distribute durations across the images
        num_images = len(images_urls)
        total_subs = len(subtitle_durations)
        if num_images == 0 or total_subs == 0:
            raise ValueError("No images or subtitle durations found.")

        subtitles_per_image = max(1, total_subs // num_images)
        image_durations = []
        for i in range(num_images):
            start_index = i * subtitles_per_image
            end_index   = start_index + subtitles_per_image
            duration_sum = sum(subtitle_durations[start_index:end_index])
            image_durations.append(duration_sum)
        # Also extend the last image by 5
        image_durations[-1] += 5

        # C) Build a concat script referencing remote images with computed durations
        concat_lines = []
        for i, url in enumerate(images_urls):
            concat_lines.append(f"file '{url}'")
            concat_lines.append(f"duration {image_durations[i]}")
        concat_script = "\n".join(concat_lines) + "\n"

        # D) First pass: create slideshow from images
        ffmpeg_img_cmd = [
            "ffmpeg",
            "-y",
            # For reading remote images in concat
            "-protocol_whitelist", "file,pipe,http,https,tcp,tls,crypto",
            "-f", "concat",
            "-safe", "0",
            "-i", "-",              # read the concat script from stdin
            "-vf", "fps=30",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            temp_video_path
        ]
        run_ffmpeg(ffmpeg_img_cmd, concat_input=concat_script)

        # E) Second pass: combine slideshow, remote audio, local subtitles, and trim
        ffmpeg_final_cmd = [
            "ffmpeg",
            "-y",
            "-i", temp_video_path,        # slideshow
            "-i", audio_url,              # remote audio
            "-vf", f"subtitles={local_subtitle_path}",
            "-t", str(final_video_duration),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-strict", "experimental",
            "-b:a", "192k",
            local_output_path
        ]
        run_ffmpeg(ffmpeg_final_cmd)

        # F) Upload final MP4 to GCS (no "output/" folder in the path)
        gcs_url = upload_to_gcs(local_output_path, bucket_name, gcs_output_path)

    except Exception as ex:
        return jsonify({"error": str(ex)}), 500

    finally:
        # Remove ephemeral files
        for path in (local_subtitle_path, temp_video_path, local_output_path):
            if os.path.exists(path):
                os.remove(path)

    return jsonify({
        "message": "Video successfully created (SRT-based durations).",
        "gcs_url": gcs_url
    }), 200


def download_file(url, local_path):
    """Download a remote file (.srt, etc.) to ephemeral local disk (/tmp)."""
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

def parse_srt_durations(srt_file):
    """
    Replicate your local parse_srt_durations() logic:
    Returns (list_of_durations, last_subtitle_end_time).
    """
    pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})")
    durations = []
    last_timestamp = 0.0

    with open(srt_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        for line in lines:
            match = pattern.match(line)
            if match:
                start_h, start_m, start_s, start_ms = match.group(1,2,3,4)
                end_h, end_m, end_s, end_ms = match.group(5,6,7,8)
                start_time = float(start_h)*3600 + float(start_m)*60 + float(start_s) + float(start_ms)/1000.0
                end_time   = float(end_h)*3600   + float(end_m)*60   + float(end_s)   + float(end_ms)/1000.0
                durations.append(round(end_time - start_time, 2))
                last_timestamp = end_time

    return durations, last_timestamp

def run_ffmpeg(ffmpeg_cmd, concat_input=None):
    """
    Run FFmpeg with optional concat script input.
    Raises CalledProcessError if FFmpeg fails.
    """
    if concat_input is not None:
        proc = subprocess.run(ffmpeg_cmd, input=concat_input, text=True, capture_output=True)
    else:
        proc = subprocess.run(ffmpeg_cmd, capture_output=True)

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, ffmpeg_cmd, output=proc.stdout, stderr=proc.stderr)

def upload_to_gcs(local_file_path, bucket_name, gcs_dest_path):
    """
    Upload ephemeral local file to GCS at gcs_dest_path (no folder unless specified).
    Returns the public https link (if the bucket is publicly readable).
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_dest_path)
    blob.upload_from_filename(local_file_path)
    # Optionally do: blob.make_public()
    return f"https://storage.googleapis.com/{bucket_name}/{gcs_dest_path}"


if __name__ == "__main__":
    # For local testing:
    #   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
    #   python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
