from flask import Flask, request, jsonify
import subprocess

app = Flask(__name__)

@app.route("/test", methods=["GET"])
def test_api():
    """Simple test endpoint to check if the API is running."""
    return jsonify({"message": "✅ API is running successfully!"}), 200

@app.route("/generate-video", methods=["POST"])
def generate_video():
    try:
        # Receive Google Drive URLs from n8n
        data = request.get_json()
        subtitle_url = data.get("subtitle_url")  # Subtitle file URL
        audio_url = data.get("audio_url")  # Audio file URL
        image_urls = data.get("image_urls", [])  # List of image URLs
        output_url = data.get("output_url")  # Google Drive folder to store the output video

        # FFmpeg command to create video from images (using URLs)
        ffmpeg_image_to_video = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", image_urls[0],  # Use first image URL
            "-vf", "fps=30",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "temp_video.mp4"
        ]

        # Run FFmpeg to create video from images
        subprocess.run(ffmpeg_image_to_video, check=True)

        # FFmpeg command to combine video, audio, and subtitles (using URLs)
        ffmpeg_final_video = [
            "ffmpeg",
            "-y",
            "-i", "temp_video.mp4",  # Input video from images
            "-i", audio_url,  # Input audio from Google Drive
            "-vf", f"subtitles={subtitle_url}",  # Use subtitles from Google Drive
            "-c:v", "libx264",
            "-c:a", "aac",
            "-strict", "experimental",
            "-b:a", "192k",
            output_url  # Save directly to Google Drive
        ]

        # Run FFmpeg to process final video
        subprocess.run(ffmpeg_final_video, check=True)

        return jsonify({
            "message": "✅ Video processing started using Google Drive URLs!",
            "output_video": output_url
        }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg processing failed",
            "details": e.stderr
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
