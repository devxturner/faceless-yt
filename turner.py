import os
import re
import subprocess

# Define file paths
image_folder = "images"  # Folder containing images like image_1.png, image_2.png...
audio_file = "audio/audio.mp3"  # Path to your audio file
subtitle_file = "subtitle.srt"  # Path to your .srt file
output_video = "output/final_video.mp4"  # Output file path
image_list_file = "image_list.txt"

# Ensure output directory exists
os.makedirs(os.path.dirname(output_video), exist_ok=True)

# Function to parse SRT and extract duration per subtitle & last subtitle timestamp
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

# ✅ Extend the last subtitle duration by 5 seconds
subtitle_durations[-1] += 5

# ✅ Add a fixed 5-second buffer to the final video duration
final_video_duration = last_subtitle_timestamp + 5

# Get the number of images available
image_files = sorted([img for img in os.listdir(image_folder) if img.endswith((".jpg", ".png"))])
num_images = len(image_files)

# Calculate how many subtitles should be mapped per image
subtitles_per_image = max(1, len(subtitle_durations) // num_images)

# Assign durations to images
image_durations = []
for i in range(num_images):
    start_index = i * subtitles_per_image
    end_index = start_index + subtitles_per_image
    duration_sum = sum(subtitle_durations[start_index:end_index])
    image_durations.append(duration_sum)

# ✅ Extend the last image duration to match the extra 5 seconds
if num_images > 0:
    image_durations[-1] += 5  # Extend last image duration

# Write to image list file for FFmpeg
with open(image_list_file, "w") as f:
    for i, img in enumerate(image_files):
        f.write(f"file '{image_folder}/{img}'\n")
        f.write(f"duration {image_durations[i]}\n")  # Set actual subtitle-based duration

# FFmpeg command to create video from images
ffmpeg_image_to_video = [
    "ffmpeg",
    "-y",
    "-f", "concat",
    "-safe", "0",
    "-i", image_list_file,
    "-vf", "fps=30",
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "temp_video.mp4"
]

# Run FFmpeg to create video from images
subprocess.run(ffmpeg_image_to_video, check=True)

# FFmpeg command to combine video, audio, and subtitles **and trim the final output**
ffmpeg_final_video = [
    "ffmpeg",
    "-y",
    "-i", "temp_video.mp4",  # Input video from images
    "-i", audio_file,  # Input audio
    "-vf", f"subtitles={subtitle_file}",  # Overlay subtitles
    "-t", str(final_video_duration),  # ✅ Trim video & audio to last subtitle timestamp + 5s
    "-c:v", "libx264",
    "-c:a", "aac",
    "-strict", "experimental",
    "-b:a", "192k",
    output_video
]

# Run FFmpeg to add audio, subtitles, and trim
subprocess.run(ffmpeg_final_video, check=True)

# Cleanup temporary video file
os.remove("temp_video.mp4")
print(f"✅ Video successfully created: {output_video} (trimmed to {final_video_duration} seconds)")
