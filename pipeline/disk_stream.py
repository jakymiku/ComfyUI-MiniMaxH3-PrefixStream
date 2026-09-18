"""Append decoded clips to disk and export without retaining the full IMAGE timeline."""

import json
import os
import re
import shutil
import subprocess
import uuid
from fractions import Fraction

try:
    from ..engine.clip_bin_manager import (
        atomic_write_json, encode_images_to_mp4, get_project_dir, project_locked,
        get_clip_dir, get_ffmpeg_exe,
    )
except ImportError:
    from engine.clip_bin_manager import (
        atomic_write_json, encode_images_to_mp4, get_project_dir, project_locked,
        get_clip_dir, get_ffmpeg_exe,
    )
from .seam_protector import trim_images_and_audio


@project_locked
def append_disk_clip(project_name, stream_name, images, audio=None, trim_frames=0,
                     fps=24.0, export=False):
    """Use PCM intermediate audio to avoid adding AAC encoder delay at every join."""
    if not re.fullmatch(r"[\w-]+", stream_name):
        raise ValueError("Stream name must contain only letters, digits, underscores or hyphens")
    directory = os.path.join(get_project_dir(project_name), ".streams", stream_name)
    if os.path.normcase(os.path.realpath(directory)) != os.path.normcase(os.path.abspath(directory)):
        raise ValueError("Linked stream directories are not supported")
    os.makedirs(directory, exist_ok=True)
    manifest_path = os.path.join(directory, "manifest.json")
    state = {"version": 1, "clips": [], "total_frames": 0}
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as source:
            state = json.load(source)
    if images is not None:
        images, audio = trim_images_and_audio(images, audio, trim_frames, fps)
        if images.shape[0] == 0:
            raise ValueError("No frames remain after trimming")
        # The encoder uses even RGB dimensions and a single audio batch.
        if images.shape[-1] not in (3, 4) or any(n % 2 for n in images.shape[1:3]):
            raise ValueError("Disk streams require RGB/RGBA images with even height and width")
        if audio is not None and (audio["waveform"].ndim != 3 or audio["waveform"].shape[0] != 1):
            raise ValueError("Disk streams require audio shape [1, channels, samples]")
        geometry = {"fps": float(fps), "height": images.shape[1], "width": images.shape[2],
                    "sample_rate": int(audio["sample_rate"]) if audio else None,
                    "channels": audio["waveform"].shape[1] if audio else None}
        if state["clips"] and state["geometry"] != geometry:
            raise ValueError("Stream resolution, fps, sample rate and channels must remain consistent")
        filename = f"segment_{uuid.uuid4().hex}.mkv"
        path = os.path.join(directory, filename)
        if not encode_images_to_mp4(images, path, fps, audio):
            raise RuntimeError("Segment encoding failed; stream manifest was not changed")
        state["geometry"] = geometry
        state["clips"].append({"file": filename, "frames": int(images.shape[0])})
        state["total_frames"] += int(images.shape[0])
        atomic_write_json(manifest_path, state)

    output_path = ""
    if export:
        if not state["clips"]:
            raise ValueError("Cannot export an empty stream")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg executable is required for disk stream export")
        # Only generated local segment names are accepted in the concat manifest.
        concat = os.path.join(directory, "concat.txt")
        with open(concat, "w", encoding="utf-8") as target:
            for clip in state["clips"]:
                if not re.fullmatch(r"segment_[0-9a-f]{32}\.mkv", clip["file"]):
                    raise ValueError("Invalid segment filename in stream manifest")
                target.write(f"file '{clip['file']}'\n")
                target.write(f"duration {clip['frames'] / state['geometry']['fps']:.12f}\n")
        temp_output = os.path.join(directory, f"export_{uuid.uuid4().hex}.mp4")
        command = [ffmpeg, "-v", "error", "-f", "concat", "-safe", "1", "-i", concat,
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", temp_output]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=3600)
            output_path = os.path.join(directory, "video.mp4")
            os.replace(temp_output, output_path)
        finally:
            if os.path.exists(temp_output):
                os.remove(temp_output)
    return manifest_path, output_path, state["total_frames"]


class MiniMaxDiskVideoStreamNode:
    """Disk-backed hard-cut assembly; use the IMAGE stitcher for seam effects."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "project_name": ("STRING", {"default": "Default_Project"}),
            "stream_name": ("STRING", {"default": "long_video"}),
            "trim_frames": ("INT", {"default": 0, "min": 0, "max": 192}),
            "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0}),
            "export": ("BOOLEAN", {"default": False}),
        }, "optional": {"images": ("IMAGE",), "audio": ("AUDIO",), "session": ("MINIMAX_SESSION",)}}

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("manifest_path", "video_path", "total_frames")
    FUNCTION = "append"
    CATEGORY = "MiniMaxH3/PrefixStream"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def append(self, project_name, stream_name, trim_frames=0, fps=24.0, export=False,
               images=None, audio=None, session=None):
        if trim_frames == 0 and session is not None:
            trim_frames = session.last_rolling_frames
        return append_disk_clip(project_name, stream_name, images, audio, trim_frames, fps, export)


def selected_clip_chain(project_name, clip_id):
    """Walk saved parent ids, excluding rejected sibling takes."""
    chain, seen = [], set()
    while clip_id and clip_id != "[INITIAL_GENERATION]":
        if clip_id in seen:
            raise ValueError("Clip history contains a parent cycle")
        seen.add(clip_id)
        directory = get_clip_dir(project_name, clip_id)
        with open(os.path.join(directory, "meta.json"), encoding="utf-8") as source:
            meta = json.load(source)
        filename = meta.get("video_file")
        if filename not in ("video.mp4", "video.mkv", "video.webm", "video.mov"):
            raise ValueError(f"Clip {clip_id} has no supported saved video; enable save_video")
        path = os.path.join(directory, filename)
        if os.path.normcase(os.path.realpath(path)) != os.path.normcase(path):
            raise ValueError("Linked video files are not supported")
        chain.append((clip_id, path))
        clip_id = meta.get("parent_clip_id")
    if not chain:
        raise ValueError("Select a saved final clip before exporting")
    return list(reversed(chain))


class MiniMaxSelectedClipExportNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "project_name": ("STRING", {"default": "H3_Easy_v5_001"}),
            "clip_id": ("STRING", {"default": "", "forceInput": True}),
        }}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("video_path", "selected_clips")
    FUNCTION = "export_selected"
    CATEGORY = "MiniMaxH3/PrefixStream"
    OUTPUT_NODE = True

    def export_selected(self, project_name, clip_id):
        chain = selected_clip_chain(project_name, clip_id)
        ffmpeg = get_ffmpeg_exe() if get_ffmpeg_exe else shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("FFmpeg and FFprobe are required for selected-clip export")
        directory = os.path.join(get_project_dir(project_name), "export_" + uuid.uuid4().hex)
        os.makedirs(directory)
        clips, geometry, total_frames = [], None, 0
        for i, (selected_id, path) in enumerate(chain):
            probe = subprocess.run([ffprobe, "-v", "error", "-count_frames", "-show_streams", "-of", "json", path],
                                   check=True, capture_output=True, text=True)
            streams = json.loads(probe.stdout)["streams"]
            video = next(s for s in streams if s["codec_type"] == "video")
            fps = float(Fraction(video["r_frame_rate"]))
            current_geometry = (video["width"], video["height"], fps)
            if geometry is not None and geometry != current_geometry:
                raise ValueError("Selected clips have different resolutions or frame rates")
            geometry = current_geometry
            frames = int(video["nb_read_frames"])
            duration = frames / fps
            name = f"clip_{i:04d}.mkv"
            command = [ffmpeg, "-v", "error", "-i", path]
            has_audio = any(s["codec_type"] == "audio" for s in streams)
            if not has_audio:
                command += ["-f", "lavfi", "-i", "anullsrc=r=32000:cl=stereo"]
            command += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
                        "-vf", f"fps={fps},setpts=N/({fps}*TB)",
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-af", f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS",
                        "-c:a", "pcm_s16le", "-ar", "32000", "-ac", "2",
                        os.path.join(directory, name)]
            subprocess.run(command, check=True, capture_output=True)
            clips.append({"clip_id": selected_id, "file": name, "frames": frames, "duration": duration})
            total_frames += frames
        concat = os.path.join(directory, "concat.txt")
        with open(concat, "w", encoding="utf-8") as output:
            for clip in clips:
                output.write(f"file '{clip['file']}'\nduration {clip['duration']:.12f}\n")
        final = os.path.join(directory, "selected_video.mp4")
        duration = total_frames / geometry[2]
        subprocess.run([ffmpeg, "-v", "error", "-f", "concat", "-safe", "1", "-i", concat,
                        "-vf", f"fps={geometry[2]},tpad=stop_mode=clone:stop_duration=0.1,trim=end_frame={total_frames},setpts=N/({geometry[2]}*TB)",
                        "-af", f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS",
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", "-frames:v", str(total_frames),
                        "-r", str(geometry[2]), "-movflags", "+faststart", final],
                       check=True, capture_output=True)
        atomic_write_json(os.path.join(directory, "selection.json"), {"project": project_name, "clips": clips,
                          "total_frames": total_frames, "fps": geometry[2], "video_path": final})
        return (final, "\n".join(clip["clip_id"] for clip in clips))
