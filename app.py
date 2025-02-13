from flask import Flask, request, jsonify
import subprocess
import os
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

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
        # Receive Google Drive Folder ID where the output video should be uploaded
        data = request.get_json()
        gdrive_folder_id = data.get("output_folder_id")  # Google Drive folder ID

        # ✅ FFmpeg Command to Process Video
        ffmpeg_command = [
            "ffmpeg",
            "-y",
            "-i", "temp_video.mp4",
            "-i", "audio.mp3",
            "-vf", "subtitles=subtitle.srt",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-strict", "experimental",
            "-b:a", "192k",
            "output/final_video.mp4"
        ]
        
        subprocess.run(ffmpeg_command, check=True)  # Run FFmpeg

        # ✅ Upload Video to Google Drive
        file_url = upload_to_gdrive("output/final_video.mp4", gdrive_folder_id)

        return jsonify({
            "message": "✅ Video processing completed!",
            "video_url": file_url
        }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg processing failed",
            "details": str(e)
        }), 500
    except Exception as e:
        return jsonify({
            "error": "Unexpected error",
            "details": str(e)
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
