from flask import Flask, request, jsonify
import subprocess
import os
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# ✅ Function to fix Google Drive URLs
def fix_gdrive_url(url):
    if "drive.google.com" in url and "id=" in url:
        return url.replace("uc?export=download&id=", "uc?id=").strip() + "&export=download"
    return url

# ✅ Google Drive Upload Function
def upload_to_gdrive(file_path, gdrive_folder_id):
    """Uploads the final video to Google Drive"""
    creds = Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/drive.file"])
    service = build("drive", "v3", credentials=creds)

    file_metadata = {
        "name": os.path.basename(file_path),
        "parents": [gdrive_folder_id]  # Folder ID in Google Drive
    }
    media = MediaFileUpload(file_path, mimetype="video/mp4")

    uploaded_file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = uploaded_file.get("id")

    # Generate a public URL for the uploaded file
    service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
    file_url = f"https://drive.google.com/uc?id={file_id}&export=download"

    return file_url

@app.route("/generate-video", methods=["POST"])
def generate_video():
    try:
        # Receive Google Drive File URLs from n8n
        data = request.get_json()
        subtitle_url = fix_gdrive_url(data.get("subtitle_url"))  # Subtitle file URL
        audio_url = fix_gdrive_url(data.get("audio_url"))  # Audio file URL
        image_urls = [fix_gdrive_url(url) for url in data.get("image_urls", [])]  # List of image URLs
        output_folder_id = data.get("output_folder_id")  # Google Drive folder to store output

        # ✅ FFmpeg Command to Create Video from Images (Using URLs)
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

        # ✅ FFmpeg Command to Combine Video, Audio, and Subtitles
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
            "output/final_video.mp4"
        ]

        # Run FFmpeg to process final video
        subprocess.run(ffmpeg_final_video, check=True)

        # ✅ Upload Final Video to Google Drive
        file_url = upload_to_gdrive("output/final_video.mp4", output_folder_id)

        return jsonify({
            "message": "✅ Video processing completed!",
            "video_url": file_url
        }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg processing failed",
            "details": e.stderr
        }), 500
    except Exception as e:
        return jsonify({
            "error": "Unexpected error",
            "details": str(e)
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
