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
        subtitle_url = data.get("subtitle_url")
        audio_url = data.get("audio_url")
        image_urls = data.get("image_urls", [])
        output_url = data.get("output_url")  # This should be a Google Drive folder

        # ✅ Ensure URLs are correctly formatted for direct download
        def fix_gdrive_url(url):
            if "drive.google.com" in url and "id=" in url:
                return url.replace("uc?export=download&id=", "uc?id=").strip() + "&export=download"
            return url

        subtitle_url = fix_gdrive_url(subtitle_url)
        audio_url = fix_gdrive_url(audio_url)
        image_urls = [fix_gdrive_url(img) for img in image_urls]

        # ✅ FFmpeg command to create video from images (using URLs)
        ffmpeg_image_to_video = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", image_urls[0],  # First image URL
            "-vf", "fps=30",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "temp_video.mp4"
        ]

        # Run FFmpeg to create video from images
        subprocess.run(ffmpeg_image_to_video, check=True)

        # ✅ FFmpeg command to combine video, audio, and subtitles
        ffmpeg_final_video = [
            "ffmpeg",
            "-y",
            "-i", "temp_video.mp4",  # Video from images
            "-i", audio_url,  # Audio from Google Drive
            "-vf", f"subtitles={subtitle_url}",  # Subtitles from Google Drive
            "-c:v", "libx264",
            "-c:a", "aac",
            "-strict", "experimental",
            "-b:a", "192k",
            "final_video.mp4"
        ]

        # Run FFmpeg
        subprocess.run(ffmpeg_final_video, check=True)

        return jsonify({
            "message": "✅ Video processing completed using Google Drive URLs!",
            "output_video": "final_video.mp4"
        }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg processing failed",
            "details": e.stderr
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
