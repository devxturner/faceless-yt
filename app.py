import os
import re
import subprocess
import requests

from flask import Flask, request, jsonify
from google.cloud import storage  # Requires: pip install google-cloud-storage

app = Flask(__name__)

def parse_srt_durations(srt_file_path):
    """
    Parse the SRT file from `srt_file_path` and return:
      - A list of subtitle durations (in seconds).
      - The last subtitle's end timestamp (in seconds).
      - The last subtitle's duration (in seconds).
    """
    durations = []
    last_timestamp = 0.0
    last_subtitle_duration = 0.0
    
    with open(srt_file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        for i in range(len(lines)):
            match = re.match(
                r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})",
                lines[i]
            )
            if match:
                start_time = (
                    int(match[1]) * 3600
                    + int(match[2]) * 60
                    + int(match[3])
                    + int(match[4]) / 1000
                )
                end_time = (
                    int(match[5]) * 3600
                    + int(match[6]) * 60
                    + int(match[7])
                    + int(match[8]) / 1000
                )
                duration = round(end_time - start_time, 2)
                durations.append(duration)
                last_timestamp = end_time
                last_subtitle_duration = duration

    return durations, last_timestamp, last_subtitle_duration


def download_to_tmp(url, local_path):
    """Download a remote file (like image or audio or subtitles) to ephemeral local storage."""
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def upload_to_gcs(local_file_path, bucket_name, gcs_dest_path):
    """
    Upload ephemeral file to GCS.
    Returns the direct HTTPS link to the uploaded file.
    Requires google-cloud-storage and correct GCP credentials (service account).
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_dest_path)
    blob.upload_from_filename(local_file_path)
    # If you want it publicly accessible:
    # blob.make_public()
    return f"https://storage.googleapis.com/{bucket_name}/{gcs_dest_path}"


@app.route("/create_video", methods=["POST"])
def create_video_endpoint():
    """
    Expects JSON of the form:
    {
      "images_urls": ["https://storage.googleapis.com/my-bucket/image_1.jpg", ...],
      "audio_url": "https://storage.googleapis.com/my-bucket/audio.mp3",
      "subtitle_url": "https://storage.googleapis.com/my-bucket/subtitle.srt",
      "output_name": "final_video.mp4",     # optional ephemeral output name
      "bucket_name": "my-bucket",          # GCS bucket to upload to
      "gcs_output_path": "final_video.mp4" # final GCS object path (e.g. "final_video.mp4")
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    # Extract fields
    images_urls = data.get("images_urls", [])
    audio_url = data.get("audio_url")
    subtitle_url = data.get("subtitle_url")
    output_name = data.get("output_name", "final_video.mp4")
    bucket_name = data.get("bucket_name")
    gcs_output_path = data.get("gcs_output_path", output_name)

    # Basic checks
    if not images_urls or not audio_url or not subtitle_url or not bucket_name:
        return jsonify({"error": "Missing required fields"}), 400

    # Create ephemeral paths
    local_subtitle_path = os.path.join("/tmp", "subtitle.srt")
    local_output_path = os.path.join("/tmp", output_name)
    temp_video_path = os.path.join("/tmp", "temp_video.mp4")
    image_list_path = os.path.join("/tmp", "image_list.txt")

    # 1) Download subtitle to /tmp
    download_to_tmp(subtitle_url, local_subtitle_path)

    # 2) Parse SRT durations (like your local script)
    subtitle_durations, last_subtitle_timestamp, last_subtitle_duration = parse_srt_durations(
        local_subtitle_path
    )

    # Extend the last subtitle by 5s
    if subtitle_durations:
        subtitle_durations[-1] += 5

    # Final video duration = last subtitle end + 5s
    final_video_duration = last_subtitle_timestamp + 5

    # 3) Download each image to /tmp & gather them in the order provided
    #    (If you want them sorted by filename, you'd sort images_urls here.)
    local_image_paths = []
    for idx, url in enumerate(images_urls, start=1):
        ext = os.path.splitext(url)[1] or ".jpg"  # fallback .jpg if unknown
        local_img = os.path.join("/tmp", f"image_{idx}{ext}")
        download_to_tmp(url, local_img)
        local_image_paths.append(local_img)

    # 4) Distribute durations across images
    num_images = len(local_image_paths)
    num_subtitle_segments = len(subtitle_durations)

    # If no images or no durations, handle gracefully
    if num_images == 0:
        return jsonify({"error": "No images downloaded."}), 400
    if num_subtitle_segments == 0:
        return jsonify({"error": "No durations found in SRT."}), 400

    subtitles_per_image = max(1, num_subtitle_segments // num_images)
    image_durations = []
    for i in range(num_images):
        start_index = i * subtitles_per_image
        end_index = start_index + subtitles_per_image
        duration_sum = sum(subtitle_durations[start_index:end_index])
        image_durations.append(duration_sum)

    # Extend last image duration by 5 to match your local script logic
    if image_durations:
        image_durations[-1] += 5  # The last image matches final 5s extension

    # 5) Create a file list for ffmpeg concat
    with open(image_list_path, "w") as f:
        for i, img_path in enumerate(local_image_paths):
            f.write(f"file '{img_path}'\n")
            f.write(f"duration {image_durations[i]}\n")

    # 6) First FFmpeg command: create the slideshow video from images
    ffmpeg_image_to_video = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", image_list_path,
        "-vf", "fps=30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        temp_video_path
    ]

    try:
        subprocess.run(ffmpeg_image_to_video, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg (image->video) failed",
            "stdout": e.stdout.decode("utf-8", errors="ignore"),
            "stderr": e.stderr.decode("utf-8", errors="ignore"),
        }), 500

    # 7) Second FFmpeg command: combine video, audio, and subtitles, *and* trim
    ffmpeg_final_video = [
        "ffmpeg",
        "-y",
        "-i", temp_video_path,
        "-i", audio_url,
        "-vf", f"subtitles={local_subtitle_path}",
        "-t", str(final_video_duration),
        "-c:v", "libx264",
        "-c:a", "aac",
        "-strict", "experimental",
        "-b:a", "192k",
        local_output_path
    ]

    try:
        subprocess.run(ffmpeg_final_video, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg (final mux) failed",
            "stdout": e.stdout.decode("utf-8", errors="ignore"),
            "stderr": e.stderr.decode("utf-8", errors="ignore"),
        }), 500

    # 8) Upload final_video.mp4 to GCS
    try:
        gcs_url = upload_to_gcs(local_output_path, bucket_name, gcs_output_path)
    except Exception as ex:
        return jsonify({"error": f"Failed uploading to GCS: {ex}"}), 500

    # 9) Clean up ephemeral files
    safe_delete(local_subtitle_path)
    safe_delete(local_output_path)
    safe_delete(temp_video_path)
    safe_delete(image_list_path)
    for p in local_image_paths:
        safe_delete(p)

    return jsonify({
        "message": "Video created successfully",
        "gcs_url": gcs_url,
        "final_duration_seconds": final_video_duration
    }), 200


def safe_delete(path):
    """Helper to delete a file if it exists, ignoring errors."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except:
        pass


if __name__ == "__main__":
    # For local testing, set your credentials:
    #   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
    # Then run:
    #   python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
