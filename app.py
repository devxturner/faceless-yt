import os
import re
import subprocess
import requests

from flask import Flask, request, jsonify
from google.cloud import storage  # Requires: pip install google-cloud-storage

app = Flask(__name__)

# ---------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------

def download_to_tmp(url, local_path):
    """
    Download a remote file (image, audio, or subtitle) from GCS (or any HTTP URL)
    to the ephemeral /tmp directory so FFmpeg can access it locally.
    """
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

def parse_srt_durations(srt_file):
    """
    Parse the SRT from `srt_file` and extract:
      - A list of durations (in seconds) for each subtitle
      - The last subtitle's end timestamp (in seconds)
      - The last subtitle's duration
    This logic is identical to your local script.
    """
    durations = []
    last_timestamp = 0
    last_subtitle_duration = 0

    with open(srt_file, "r", encoding="utf-8") as f:
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

def upload_to_gcs(local_file_path, bucket_name, gcs_dest_path):
    """
    Upload the final MP4 to GCS.
    Returns the public (or authenticated) link to that object.
    """
    client = storage.Client()  # uses GOOGLE_APPLICATION_CREDENTIALS
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_dest_path)
    blob.upload_from_filename(local_file_path)
    # Optionally make it public:
    # blob.make_public()
    return f"https://storage.googleapis.com/{bucket_name}/{gcs_dest_path}"

def safe_delete(path):
    """Helper to remove a file if it exists (ignore errors)."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except:
        pass

# ---------------------------------------------------
# FLASK ROUTE
# ---------------------------------------------------

@app.route("/create_video", methods=["POST"])
def create_video():
    """
    Expects JSON of the form:
    {
      "images_urls": ["https://storage.googleapis.com/my-bucket/image_1.jpg", ...],
      "audio_url": "https://storage.googleapis.com/my-bucket/audio.mp3",
      "subtitle_url": "https://storage.googleapis.com/my-bucket/subtitle.srt",
      "output_name": "final_video.mp4",     
      "bucket_name": "my-bucket",          
      "gcs_output_path": "final_video.mp4" 
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON payload provided"}), 400

    # Extract JSON fields
    images_urls = data.get("images_urls", [])
    audio_url = data.get("audio_url")
    subtitle_url = data.get("subtitle_url")
    output_name = data.get("output_name", "final_video.mp4")
    bucket_name = data.get("bucket_name")
    gcs_output_path = data.get("gcs_output_path", output_name)

    # Validate required
    if not images_urls or not audio_url or not subtitle_url or not bucket_name:
        return jsonify({"error": "Missing required fields in JSON"}), 400

    # Ephemeral local paths
    # We'll replicate your local filenames:
    local_subtitle_path = os.path.join("/tmp", "subtitle.srt")
    local_audio_path = os.path.join("/tmp", "audio.mp3")
    # final video in /tmp
    local_output_path = os.path.join("/tmp", output_name)
    # intermediate "temp_video.mp4"
    temp_video_path = os.path.join("/tmp", "temp_video.mp4")
    # image list file
    image_list_file = os.path.join("/tmp", "image_list.txt")
    # ephemeral "images" folder
    local_image_folder = os.path.join("/tmp", "images")

    # 1. Make sure /tmp/images exists
    os.makedirs(local_image_folder, exist_ok=True)

    # 2. Download audio and subtitle to /tmp
    try:
        download_to_tmp(audio_url, local_audio_path)
        download_to_tmp(subtitle_url, local_subtitle_path)
    except Exception as e:
        return jsonify({"error": f"Failed to download audio or subtitle: {e}"}), 500

    # 3. Parse the SRT to get durations
    subtitle_durations, last_subtitle_timestamp, last_subtitle_duration = parse_srt_durations(local_subtitle_path)

    if not subtitle_durations:
        return jsonify({"error": "No valid subtitles parsed from SRT"}), 400

    # Extend the last subtitle duration by 5 seconds
    subtitle_durations[-1] += 5

    # Add a 5-second buffer to the final video duration
    final_video_duration = last_subtitle_timestamp + 5

    # 4. Download all images to /tmp/images
    local_image_paths = []
    for idx, img_url in enumerate(images_urls, start=1):
        # derive an extension
        _, ext = os.path.splitext(img_url)
        if not ext:
            ext = ".jpg"  # fallback
        local_img_path = os.path.join(local_image_folder, f"image_{idx}{ext}")
        try:
            download_to_tmp(img_url, local_img_path)
            local_image_paths.append(os.path.basename(local_img_path))  # just store filename
        except Exception as e:
            return jsonify({"error": f"Failed to download image '{img_url}': {e}"}), 500

    # 5. Sort local_image_paths if you need a specific order.
    #    But here we keep them in the order provided by images_urls.

    num_images = len(local_image_paths)
    if num_images == 0:
        return jsonify({"error": "No images were downloaded."}), 400

    # 6. Calculate how many subtitles to map per image
    subtitles_per_image = max(1, len(subtitle_durations) // num_images)

    # 7. Assign durations to each image
    image_durations = []
    for i in range(num_images):
        start_index = i * subtitles_per_image
        end_index = start_index + subtitles_per_image
        duration_sum = sum(subtitle_durations[start_index:end_index])
        image_durations.append(duration_sum)

    # Extend the last image duration by 5 to match the final extension
    if image_durations:
        image_durations[-1] += 5

    # 8. Write the image_list.txt file for FFmpeg concat
    try:
        with open(image_list_file, "w") as f:
            for i, img_file in enumerate(local_image_paths):
                f.write(f"file '{os.path.join(local_image_folder, img_file)}'\n")
                f.write(f"duration {image_durations[i]}\n")
    except Exception as e:
        return jsonify({"error": f"Failed to write image list file: {e}"}), 500

    # 9. First FFmpeg command: create video from images
    ffmpeg_image_to_video = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", image_list_file,
        "-vf", "fps=30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        temp_video_path
    ]

    try:
        subprocess.run(ffmpeg_image_to_video, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg failed during image->video step",
            "stdout": e.stdout.decode("utf-8", errors="ignore"),
            "stderr": e.stderr.decode("utf-8", errors="ignore"),
        }), 500

    # 10. Second FFmpeg command: combine temp_video, audio, subtitles, trim
    ffmpeg_final_video = [
        "ffmpeg",
        "-y",
        "-i", temp_video_path,
        "-i", local_audio_path,
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
            "error": "FFmpeg failed during final mux step",
            "stdout": e.stdout.decode("utf-8", errors="ignore"),
            "stderr": e.stderr.decode("utf-8", errors="ignore"),
        }), 500

    # 11. Upload final_video.mp4 to GCS
    try:
        gcs_url = upload_to_gcs(local_output_path, bucket_name, gcs_output_path)
    except Exception as e:
        return jsonify({"error": f"Failed to upload final video to GCS: {e}"}), 500

    # 12. Cleanup ephemeral files
    safe_delete(temp_video_path)
    safe_delete(local_output_path)
    safe_delete(local_subtitle_path)
    safe_delete(local_audio_path)
    safe_delete(image_list_file)
    for img_file in os.listdir(local_image_folder):
        safe_delete(os.path.join(local_image_folder, img_file))
    # Remove the images folder itself
    try:
        os.rmdir(local_image_folder)
    except:
        pass

    return jsonify({
        "message": "Video created successfully",
        "final_duration_seconds": final_video_duration,
        "gcs_url": gcs_url
    }), 200

if __name__ == "__main__":
    # For local testing:
    #   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
    #   pip install flask requests google-cloud-storage
    #   python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
