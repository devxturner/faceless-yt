import os
import re
import subprocess
import io
import requests

from flask import Flask, request, jsonify
from google.cloud import storage  # for uploading to GCS (optional if you have a signed URL approach)

app = Flask(__name__)

def parse_srt_durations(srt_text):
    """
    Parses the subtitle text in memory (no local disk usage).
    Returns:
      - list of durations (float)
      - last subtitle end timestamp (float)
      - last subtitle duration (float)
    """
    durations = []
    last_timestamp = 0.0
    last_subtitle_duration = 0.0

    lines = srt_text.splitlines()
    # Regex to match "HH:MM:SS,mmm --> HH:MM:SS,mmm"
    pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})")

    for line in lines:
        match = pattern.match(line)
        if match:
            start_time = (
                int(match.group(1)) * 3600
                + int(match.group(2)) * 60
                + int(match.group(3))
                + float(match.group(4)) / 1000.0
            )
            end_time = (
                int(match.group(5)) * 3600
                + int(match.group(6)) * 60
                + int(match.group(7))
                + float(match.group(8)) / 1000.0
            )
            duration = round(end_time - start_time, 2)
            durations.append(duration)
            last_timestamp = end_time
            last_subtitle_duration = duration

    return durations, last_timestamp, last_subtitle_duration

def create_slideshow_video(
    image_urls,
    audio_url,
    subtitle_url,
    output_name="final_video.mp4"
):
    """
    Creates the final video by:
      1) Parsing SRT from remote URL in memory.
      2) Building a concat string referencing remote images.
      3) Single ffmpeg command that:
         - reads concat script from stdin
         - processes remote images, audio, subtitles
         - outputs an MP4
    Returns: local path to the final MP4 file (ephemeral).
    """
    # 1. Get the SRT text in memory (no local save)
    subtitle_resp = requests.get(subtitle_url)
    subtitle_resp.raise_for_status()
    srt_text = subtitle_resp.text

    # 2. Parse durations from that SRT text
    subtitle_durations, last_subtitle_timestamp, _ = parse_srt_durations(srt_text)

    # Extend the last subtitle by 5 seconds
    subtitle_durations[-1] += 5
    final_video_duration = last_subtitle_timestamp + 5

    # 3. We need to split the durations among the images.
    #    For simplicity, let's assume each image gets a chunk of durations,
    #    just like your original logic:
    num_images = len(image_urls)
    total_subs = len(subtitle_durations)
    subtitles_per_image = max(1, total_subs // num_images)

    # Calculate durations per image
    image_durations = []
    for i in range(num_images):
        start_index = i * subtitles_per_image
        end_index = start_index + subtitles_per_image
        # sum(...) will gracefully handle the last chunk even if shorter
        duration_sum = sum(subtitle_durations[start_index:end_index])
        image_durations.append(duration_sum)

    # Extend the last image by 5s
    if image_durations:
        image_durations[-1] += 5

    # 4. Build the concat script in memory (no local file).
    #    Each image references the direct GCS URL:
    #      file 'https://...image_1.jpg'
    #      duration 3.5
    concat_lines = []
    for i, url in enumerate(image_urls):
        concat_lines.append(f"file '{url}'")
        concat_lines.append(f"duration {image_durations[i]}")

    concat_text = "\n".join(concat_lines) + "\n"

    # 5. Prepare the single ffmpeg command that:
    #    - Reads the concat script from stdin (-f concat -i -)
    #    - Takes the remote audio as second input
    #    - Overlays remote subtitles
    #    - Trims to final_video_duration
    #    - Outputs an MP4 file to ephemeral local storage
    local_output_path = output_name  # ephemeral file in container
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-protocol_whitelist", "file,pipe,http,https,tcp,tls,crypto",
        "-f", "concat",
        "-safe", "0",
        "-i", "-",  # read concat script from stdin
        "-i", audio_url,  # remote audio
        "-vf", f"subtitles={subtitle_url}",  # remote subtitles
        "-t", str(final_video_duration),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-strict", "experimental",
        "-b:a", "192k",
        local_output_path,
    ]

    # 6. Run ffmpeg, piping our concat text in
    #    text=True => we'll pass a str to stdin
    subprocess.run(
        ffmpeg_cmd,
        input=concat_text,
        text=True,
        check=True
    )

    return local_output_path

def upload_to_gcs(local_file_path, bucket_name, gcs_dest_path):
    """
    Upload ephemeral MP4 to GCS. Then you can remove local_file_path if desired.
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_dest_path)
    blob.upload_from_filename(local_file_path)
    # Optionally make it public
    # blob.make_public()
    return f"https://storage.googleapis.com/{bucket_name}/{gcs_dest_path}"

@app.route("/create_video", methods=["POST"])
def create_video_endpoint():
    """
    POST JSON with:
    {
      "images_urls": ["https://storage.googleapis.com/BUCKET/image_1.jpg", ...],
      "audio_url": "https://storage.googleapis.com/BUCKET/audio.mp3",
      "subtitle_url": "https://storage.googleapis.com/BUCKET/subtitle.srt",
      "output_name": "final_video.mp4",
      "bucket_name": "n8n-faceless-ph",   # for uploading final
      "gcs_output_path": "output/final_video.mp4" 
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON payload"}), 400

    images_urls = data.get("images_urls")
    audio_url = data.get("audio_url")
    subtitle_url = data.get("subtitle_url")
    output_name = data.get("output_name", "final_video.mp4")

    if not images_urls or not audio_url or not subtitle_url:
        return jsonify({"error": "images_urls, audio_url, subtitle_url are required"}), 400

    bucket_name = data.get("bucket_name")
    gcs_output_path = data.get("gcs_output_path", output_name)

    # 1. Run FFmpeg entirely referencing remote URLs (no local download of inputs).
    try:
        final_video_path = create_slideshow_video(
            image_urls=images_urls,
            audio_url=audio_url,
            subtitle_url=subtitle_url,
            output_name=output_name
        )
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"FFmpeg failed: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # 2. Optionally upload final_video.mp4 to GCS
    gcs_url = None
    if bucket_name:
        try:
            gcs_url = upload_to_gcs(final_video_path, bucket_name, gcs_output_path)
        except Exception as e:
            # If upload fails, at least let user know we created the file
            return jsonify({"error": f"Created video but failed to upload: {e}"}), 500

    # 3. Remove the ephemeral file after upload (if you want to keep container clean)
    if os.path.exists(final_video_path):
        os.remove(final_video_path)

    return jsonify({
        "message": "Video successfully created in the cloud",
        "gcs_url": gcs_url,
    }), 200

if __name__ == "__main__":
    # For local testing: 
    #   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
    #   python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
