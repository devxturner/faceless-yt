from flask import Flask, request, jsonify
import os
import re
import subprocess

app = Flask(__name__)

# Ensure necessary directories exist
os.makedirs("images", exist_ok=True)
os.makedirs("audio", exist_ok=True)
os.makedirs("output", exist_ok=True)

@app.route("/test", methods=["GET"])
def test_api():
    """Simple test endpoint to check if the API is running."""
    return jsonify({"message": "✅ API is running successfully!"}), 200

@app.route("/generate-video", methods=["POST"])
def generate_video():
    try:
        # Get JSON input (expects files to be already uploaded in the right folders)
        data = request.get_json()

        subtitle_file = "subtitle.srt"  # Path to subtitle file
        audio_file = "audio/audio.mp3"  # Path to audio file
        output_video = "output/final_video.mp4"  # Output file path
        image_folder = "images"  # Folder containing images
        image_list_file = "image_list.txt"

        # Function to parse SRT and extract durations
        def parse_srt_durations(srt_file):
            durations = []
            last_timestamp = 0
            last_subtitle_duration = 0
            with open(srt_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for i in range(len(lines)):
                    match = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})", lines[i])
                    if match:
                        start_time = int(match[1]) * 3600 + int(match[2]) * 60 + int(match[3]) + int(match[4]) / 1000
                        end_time = int(match[5]) * 3600 + int(match[6]) * 60 + int(match[7]) + int(match[8]) / 1000
                        duration = round(end_time - start_time, 2)
                        durations.append(duration)
                        last_timestamp = end_time  # Update last subtitle's end timestamp
                        last_subtitle_duration = duration  # Store last subtitle's duration

            return durations, last_timestamp, last_subtitle_duration

        # Get subtitle durations & last subtitle timestamp
        subtitle_durations, last_subtitle_timestamp, last_subtitle_duration = parse_srt_durations(subtitle_file)

        # Extend last subtitle duration by 5 seconds
        subtitle_durations[-1] += 5
        final_video_duration = last_subtitle_timestamp + 5  # Add fixed 5s buffer

        # Get images
        image_files = sorted([img for img in os.listdir(image_folder) if img.endswith((".jpg", ".png"))])
        num_images = len(image_files)

        # Assign durations per image
        subtitles_per_image = max(1, len(subtitle_durations) // num_images)
        image_durations = []
        for i in range(num_images):
            start_index = i * subtitles_per_image
            end_index = start_index + subtitles_per_image
            duration_sum = sum(subtitle_durations[start_index:end_index])
            image_durations.append(duration_sum)

        # Extend last image duration
        if num_images > 0:
            image_durations[-1] += 5

        # Write image sequence for FFmpeg
        with open(image_list_file, "w") as f:
            for i, img in enumerate(image_files):
                f.write(f"file '{image_folder}/{img}'\n")
                f.write(f"duration {image_durations[i]}\n")

        # FFmpeg: Create video from images
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", image_list_file,
            "-vf", "fps=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "temp_video.mp4"
        ], check=True)

        # FFmpeg: Combine with audio & subtitles
        subprocess.run([
            "ffmpeg", "-y", "-i", "temp_video.mp4", "-i", audio_file,
            "-vf", f"subtitles={subtitle_file}", "-t", str(final_video_duration),
            "-c:v", "libx264", "-c:a", "aac", "-strict", "experimental", "-b:a", "192k", output_video
        ], check=True)

        # Cleanup temp files
        os.remove("temp_video.mp4")

        return jsonify({"message": "✅ Video successfully created", "output": output_video}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
