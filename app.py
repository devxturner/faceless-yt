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

        # ✅ Use Google Drive URLs directly in FFmpeg
        ffmpeg_command = [
            "ffmpeg",
            "-y",
            "-i", audio_url,  # Use audio from Google Drive
            "-vf", f"subtitles={subtitle_url}",  # Use subtitles from Google Drive
            "-t", "30",  # Example: Trim to 30 seconds (Modify as needed)
            "-c:v", "libx264",
            "-c:a", "aac",
            "-strict", "experimental",
            "-b:a", "192k",
            "output/final_video.mp4"
        ]

        # Run FFmpeg command
        subprocess.run(ffmpeg_command, check=True)

        return jsonify({"message": "✅ Video processing started using Google Drive URLs!"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
