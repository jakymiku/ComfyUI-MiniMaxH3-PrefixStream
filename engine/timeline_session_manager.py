"""Engine for MiniMax H3 Video Timeline & Patch Reassembly Session Management.

Provides:
- Project-isolated session state tracking for long video editing (>15s).
- Direct on-demand video file streaming (Lazy Chunk Decoding) via FFmpeg.
- Zero-drift integer frame slicing and sample-accurate audio locking.
- In-place video patch replacement with optional cosine seam micro-blending.
- Gap and coverage analysis to highlight missing/unedited chunks.
- CPU/Disk streaming storage to prevent VRAM explosion during long video assembly.
"""

import os
import json
import time
import math
import shutil
import logging
import threading
import subprocess
from typing import Dict, Any, List, Optional, Tuple, Union

import torch
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from safetensors.torch import load_file as st_load, save_file as st_save
except ImportError:
    st_load = None
    st_save = None

from .clip_bin_manager import (
    get_project_dir,
    project_locked,
    atomic_write_json,
    pil_to_tensor,
)

logger = logging.getLogger("minimax_timeline")

# In-memory LRU session cache for fast interactive ComfyUI execution
_SESSION_CACHE: Dict[str, "TimelineSession"] = {}
_SESSION_CACHE_LOCK = threading.RLock()
_VIDEO_PROBE_CACHE: Dict[str, Dict[str, Any]] = {}

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".m4v")


def get_ffmpeg_path() -> Optional[str]:
    """Finds ffmpeg executable from PATH or common ComfyUI install locations."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    for cand in [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"f:\ComfyUI-aki-v3\ComfyUI\ffmpeg.exe",
        r"f:\ComfyUI-aki-v3\ffmpeg\bin\ffmpeg.exe",
        r"f:\ComfyUI-aki-v3\python_embeded\ffmpeg.exe",
    ]:
        if os.path.isfile(cand):
            return cand
    return None


def get_ffprobe_path() -> Optional[str]:
    """Finds ffprobe executable from PATH or common ComfyUI install locations."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe
    for cand in [
        r"C:\ffmpeg\bin\ffprobe.exe",
        r"f:\ComfyUI-aki-v3\ComfyUI\ffprobe.exe",
        r"f:\ComfyUI-aki-v3\ffmpeg\bin\ffprobe.exe",
        r"f:\ComfyUI-aki-v3\python_embeded\ffprobe.exe",
    ]:
        if os.path.isfile(cand):
            return cand
    return None


def get_input_video_files() -> List[str]:
    """Scans and returns all video files in ComfyUI input directory."""
    files = []
    try:
        import folder_paths
        input_dir = folder_paths.get_input_directory()
        if os.path.isdir(input_dir):
            for root, _, filenames in os.walk(input_dir):
                for fn in filenames:
                    if any(fn.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                        rel = os.path.relpath(os.path.join(root, fn), input_dir)
                        files.append(rel.replace("\\", "/"))
    except Exception:
        pass
    return sorted(files) if files else ["none"]


def resolve_video_path(video_name_or_path: str) -> str:
    """Resolves relative input path or absolute path to an existing video file."""
    if not video_name_or_path or video_name_or_path == "none":
        return ""
    # Check absolute path
    if os.path.isabs(video_name_or_path) and os.path.isfile(video_name_or_path):
        return video_name_or_path
    # Check inside ComfyUI input directory
    try:
        import folder_paths
        input_dir = folder_paths.get_input_directory()
        cand = os.path.join(input_dir, video_name_or_path)
        if os.path.isfile(cand):
            return os.path.normpath(cand)
    except Exception:
        pass
    # Local relative
    if os.path.isfile(video_name_or_path):
        return os.path.abspath(video_name_or_path)
    return ""


def probe_video_info(video_path: str) -> Dict[str, Any]:
    """Probes resolution, duration, fps, and total frames using ffprobe."""
    if not video_path or not os.path.isfile(video_path):
        return {"width": 0, "height": 0, "fps": 24.0, "total_frames": 0, "duration": 0.0, "has_audio": False}

    mtime = os.path.getmtime(video_path)
    cache_key = f"{video_path}:{mtime}"
    if cache_key in _VIDEO_PROBE_CACHE:
        return _VIDEO_PROBE_CACHE[cache_key]

    ffprobe = get_ffprobe_path()
    info = {"width": 0, "height": 0, "fps": 24.0, "total_frames": 0, "duration": 0.0, "has_audio": False}
    if ffprobe:
        try:
            cmd = [
                ffprobe, "-v", "error",
                "-show_streams",
                "-show_format",
                "-of", "json",
                video_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            data = json.loads(res.stdout)
            streams = data.get("streams", [])
            for s in streams:
                if s.get("codec_type") == "video" and info["width"] == 0:
                    info["width"] = int(s.get("width", 0))
                    info["height"] = int(s.get("height", 0))
                    r_fps = s.get("r_frame_rate", "24/1")
                    try:
                        num, den = map(float, r_fps.split("/"))
                        info["fps"] = num / den if den != 0 else 24.0
                    except Exception:
                        info["fps"] = 24.0
                    if "nb_frames" in s and s["nb_frames"].isdigit():
                        info["total_frames"] = int(s["nb_frames"])
                    if "duration" in s:
                        try:
                            info["duration"] = float(s["duration"])
                        except Exception:
                            pass
                elif s.get("codec_type") == "audio":
                    info["has_audio"] = True
            if "format" in data and "duration" in data["format"]:
                try:
                    fmt_dur = float(data["format"]["duration"])
                    if fmt_dur > 0 and info["duration"] == 0:
                        info["duration"] = fmt_dur
                except Exception:
                    pass
            if info["total_frames"] == 0 and info["duration"] > 0 and info["fps"] > 0:
                info["total_frames"] = int(round(info["duration"] * info["fps"]))
        except Exception as e:
            logger.warning("[Timeline] ffprobe failed for %s: %s", video_path, e)

    _VIDEO_PROBE_CACHE[cache_key] = info
    return info


def extract_video_chunk_ffmpeg(
    video_path: str,
    start_frame: int,
    chunk_length: int,
    force_fps: float = 24.0,
    target_width: int = 0,
    target_height: int = 0,
) -> torch.Tensor:
    """Extracts on-demand chunk frames directly from video file via FFmpeg without loading full video into RAM."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg executable not found in PATH or environment")

    probe = probe_video_info(video_path)
    src_w = probe.get("width", 1920) or 1920
    src_h = probe.get("height", 1080) or 1080

    out_w = target_width if target_width > 0 else src_w
    out_h = target_height if target_height > 0 else src_h
    # Even dimensions
    out_w = (out_w // 2) * 2
    out_h = (out_h // 2) * 2

    start_sec = max(0.0, start_frame / float(force_fps))
    vf_filters = [f"fps={force_fps}"]
    if target_width > 0 or target_height > 0:
        vf_filters.append(f"scale={out_w}:{out_h}:flags=bicubic")
    else:
        vf_filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    cmd = [
        ffmpeg, "-v", "error",
        "-ss", f"{start_sec:.4f}",
        "-i", video_path,
        "-vf", ",".join(vf_filters),
        "-vframes", str(chunk_length),
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "pipe:1"
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw_bytes, stderr_bytes = proc.communicate()

    if proc.returncode != 0 and len(raw_bytes) == 0:
        err_msg = stderr_bytes.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg video chunk extraction failed: {err_msg}")

    frame_size = out_w * out_h * 3
    num_frames = len(raw_bytes) // frame_size
    if num_frames == 0:
        return torch.zeros((1, out_h, out_w, 3), dtype=torch.float32)

    arr = np.frombuffer(raw_bytes[:num_frames * frame_size], dtype=np.uint8).copy()
    arr = arr.reshape((num_frames, out_h, out_w, 3))
    tensor = torch.from_numpy(arr).float() / 255.0
    return tensor


def standardize_audio_dict(
    audio: Any,
    default_sr: int = 44100,
    fallback_samples: int = 0,
) -> Dict[str, Any]:
    """Ensures audio is in standard ComfyUI format:
    {"waveform": torch.Tensor [1, channels, samples], "sample_rate": int}
    
    1. Strictly guarantees waveform is 3-dimensional [1, C, N], so movedim(1, -1)
       in VAEEncodeAudio / NKDAVLatent never triggers 'IndexError: tuple index out of range'.
    2. If audio is None/empty, synthesizes silent stereo audio of length fallback_samples.
    """
    if audio is not None and isinstance(audio, dict) and "waveform" in audio:
        waveform = audio["waveform"]
        sr = int(audio.get("sample_rate", default_sr))
        if isinstance(waveform, torch.Tensor):
            if waveform.ndim == 1:
                waveform = waveform.unsqueeze(0).unsqueeze(0)
            elif waveform.ndim == 2:
                waveform = waveform.unsqueeze(0)
            elif waveform.ndim > 3:
                waveform = waveform.view(1, waveform.shape[-2], waveform.shape[-1])
            return {"waveform": waveform.float(), "sample_rate": sr}
    elif audio is not None and isinstance(audio, torch.Tensor):
        waveform = audio
        if waveform.ndim == 1:
            waveform = waveform.unsqueeze(0).unsqueeze(0)
        elif waveform.ndim == 2:
            waveform = waveform.unsqueeze(0)
        elif waveform.ndim > 3:
            waveform = waveform.view(1, waveform.shape[-2], waveform.shape[-1])
        return {"waveform": waveform.float(), "sample_rate": default_sr}

    # Fallback silence for videos without audio stream
    samples = max(1024, int(fallback_samples)) if fallback_samples > 0 else 1024
    silent_wave = torch.zeros((1, 2, samples), dtype=torch.float32)
    return {"waveform": silent_wave, "sample_rate": default_sr}


def extract_audio_chunk_ffmpeg(
    video_path: str,
    start_frame: int,
    chunk_length: int,
    force_fps: float = 24.0,
    sample_rate: int = 44100,
) -> Optional[Dict[str, Any]]:
    """Extracts on-demand synchronized audio waveform for the chunk duration."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return None

    start_sec = max(0.0, start_frame / float(force_fps))
    dur_sec = max(0.01, chunk_length / float(force_fps))

    cmd = [
        ffmpeg, "-v", "error",
        "-ss", f"{start_sec:.4f}",
        "-t", f"{dur_sec:.4f}",
        "-i", video_path,
        "-vn",
        "-ar", str(sample_rate),
        "-ac", "2",
        "-f", "f32le",
        "pipe:1"
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw_bytes, _ = proc.communicate()
    if len(raw_bytes) < 4:
        return None

    arr = np.frombuffer(raw_bytes, dtype=np.float32)
    num_samples = len(arr) // 2
    if num_samples == 0:
        return None

    arr = arr[:num_samples * 2].reshape((num_samples, 2)).T
    waveform = torch.from_numpy(arr.copy()).float()
    if waveform.ndim == 2:
        waveform = waveform.unsqueeze(0)  # Standard ComfyUI AUDIO: [1, 2, num_samples]
    elif waveform.ndim == 1:
        waveform = waveform.unsqueeze(0).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": sample_rate}


def _get_timeline_dir(project_name: str) -> str:
    """Returns directory path for timeline session assets."""
    p_dir = get_project_dir(project_name)
    tl_dir = os.path.join(p_dir, ".timeline")
    os.makedirs(tl_dir, exist_ok=True)
    return tl_dir


def _get_meta_path(project_name: str) -> str:
    return os.path.join(_get_timeline_dir(project_name), "session_meta.json")


def _get_master_safetensor_path(project_name: str) -> str:
    return os.path.join(_get_timeline_dir(project_name), "master_assembled.safetensors")


class TimelineSession:
    """Encapsulates the persistent assembly state of a single project's long video."""

    def __init__(self, project_name: str):
        self.project_name = project_name
        self.meta: Dict[str, Any] = {
            "project_name": project_name,
            "source_video_path": "",
            "total_frames": 0,
            "fps": 24.0,
            "width": 0,
            "height": 0,
            "chunk_length": 124,
            "total_chunks": 0,
            "chunks": [],
            "gaps": [],
            "coverage_ratio": 0.0,
            "is_fully_assembled": False,
            "last_updated": time.time(),
        }
        self.master_frames: Optional[torch.Tensor] = None  # Always held in CPU memory [T, H, W, C]
        self.master_audio: Optional[Dict[str, Any]] = None  # {"waveform": [1, C, S] (CPU), "sample_rate": int}
        self._load_from_disk()

    def _load_from_disk(self):
        meta_file = _get_meta_path(self.project_name)
        if os.path.isfile(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.meta.update(data)
            except Exception as e:
                logger.warning("[Timeline] Failed to load session meta for '%s': %s", self.project_name, e)

        st_path = _get_master_safetensor_path(self.project_name)
        if st_load is not None and os.path.isfile(st_path):
            try:
                tensors = st_load(st_path)
                if "frames" in tensors:
                    raw = tensors["frames"]
                    if raw.dtype == torch.uint8:
                        self.master_frames = raw.float() / 255.0
                    else:
                        self.master_frames = raw.float()
                if "audio_waveform" in tensors and "audio_sample_rate" in tensors:
                    sr = int(tensors["audio_sample_rate"].item())
                    w = tensors["audio_waveform"].float()
                    if w.ndim == 1:
                        w = w.unsqueeze(0).unsqueeze(0)
                    elif w.ndim == 2:
                        w = w.unsqueeze(0)
                    elif w.ndim > 3:
                        w = w.view(1, w.shape[-2], w.shape[-1])
                    self.master_audio = {
                        "waveform": w,
                        "sample_rate": sr,
                    }
            except Exception as e:
                logger.warning("[Timeline] Failed to load master tensor for '%s': %s", self.project_name, e)

    def save_to_disk(self):
        """Atomically persist meta and master tensors to disk."""
        meta_file = _get_meta_path(self.project_name)
        self.meta["last_updated"] = time.time()
        atomic_write_json(meta_file, self.meta)

        st_path = _get_master_safetensor_path(self.project_name)
        if st_save is not None and self.master_frames is not None:
            try:
                tensors = {}
                clamped = torch.clamp(self.master_frames, 0.0, 1.0)
                tensors["frames"] = (clamped * 255.0).round().to(torch.uint8)

                if self.master_audio is not None and "waveform" in self.master_audio:
                    tensors["audio_waveform"] = self.master_audio["waveform"].cpu()
                    tensors["audio_sample_rate"] = torch.tensor([int(self.master_audio["sample_rate"])], dtype=torch.int32)

                st_save(tensors, st_path)
            except Exception as e:
                logger.warning("[Timeline] Failed to save master tensor for '%s': %s", self.project_name, e)

    def initialize_from_video_file(
        self,
        video_path: str,
        force_fps: float,
        chunk_length: int,
        target_width: int = 0,
        target_height: int = 0,
    ) -> bool:
        """Initializes session directly from video file metadata without loading all frames into RAM."""
        probe = probe_video_info(video_path)
        fps = float(force_fps) if force_fps > 0 else (probe.get("fps", 24.0) or 24.0)
        dur = float(probe.get("duration", 0.0))
        total_frames = int(round(dur * fps)) if dur > 0 else probe.get("total_frames", 0)
        total_frames = max(1, total_frames)

        w = target_width if target_width > 0 else (probe.get("width", 1920) or 1920)
        h = target_height if target_height > 0 else (probe.get("height", 1080) or 1080)
        w = (w // 2) * 2
        h = (h // 2) * 2

        chunk_length = max(16, int(chunk_length))
        total_chunks = math.ceil(total_frames / chunk_length)

        needs_init = (
            self.meta.get("source_video_path") != video_path
            or self.meta.get("total_frames") != total_frames
            or self.meta.get("chunk_length") != chunk_length
            or self.meta.get("width") != w
            or self.meta.get("height") != h
        )

        if needs_init:
            self.master_frames = None
            self.master_audio = None
            st_path = _get_master_safetensor_path(self.project_name)
            if os.path.isfile(st_path):
                try:
                    os.remove(st_path)
                except Exception as e:
                    logger.warning("[Timeline] Failed to remove obsolete master safetensor: %s", e)

            chunks = []
            for i in range(total_chunks):
                sf = i * chunk_length
                ef = min(total_frames, sf + chunk_length)
                chunks.append({
                    "chunk_index": i,
                    "start_frame": sf,
                    "end_frame": ef,
                    "frame_count": ef - sf,
                    "status": "unprocessed",
                    "updated_at": 0.0,
                    "version": 0,
                })

            self.meta.update({
                "source_video_path": video_path,
                "total_frames": total_frames,
                "fps": round(fps, 3),
                "width": w,
                "height": h,
                "chunk_length": chunk_length,
                "total_chunks": total_chunks,
                "chunks": chunks,
            })
            self._update_gap_analysis()
            self.save_to_disk()
            return True
        return False

    def initialize_base(
        self,
        images: torch.Tensor,
        audio: Optional[Dict[str, Any]],
        fps: float,
        chunk_length: int,
    ) -> bool:
        """Initializes or updates base video reference from raw Tensor."""
        T, H, W, C = images.shape
        fps = float(fps) if fps > 0 else 24.0
        chunk_length = max(16, int(chunk_length))
        total_chunks = math.ceil(T / chunk_length) if T > 0 else 0

        needs_init = (
            self.master_frames is None
            or self.master_frames.shape != (T, H, W, C)
            or self.meta.get("total_frames") != T
            or self.meta.get("chunk_length") != chunk_length
        )

        if needs_init:
            self.master_frames = images.detach().to(device="cpu", dtype=torch.float32).clone()
            if audio is not None and "waveform" in audio and "sample_rate" in audio:
                norm_aud = standardize_audio_dict(audio)
                self.master_audio = {
                    "waveform": norm_aud["waveform"].detach().to(device="cpu", dtype=torch.float32).clone(),
                    "sample_rate": int(norm_aud["sample_rate"]),
                }
            else:
                total_samples = int(round(T / fps * 44100))
                self.master_audio = {
                    "waveform": torch.zeros((1, 2, max(1024, total_samples)), dtype=torch.float32),
                    "sample_rate": 44100,
                }

            chunks = []
            for i in range(total_chunks):
                sf = i * chunk_length
                ef = min(T, sf + chunk_length)
                chunks.append({
                    "chunk_index": i,
                    "start_frame": sf,
                    "end_frame": ef,
                    "frame_count": ef - sf,
                    "status": "unprocessed",
                    "updated_at": 0.0,
                    "version": 0,
                })

            self.meta.update({
                "total_frames": T,
                "fps": round(fps, 3),
                "width": W,
                "height": H,
                "chunk_length": chunk_length,
                "total_chunks": total_chunks,
                "chunks": chunks,
            })
            self._update_gap_analysis()
            self.save_to_disk()
            return True
        return False

    def _update_gap_analysis(self):
        """Computes coverage and locates any unedited gaps."""
        chunks = self.meta.get("chunks", [])
        total_frames = self.meta.get("total_frames", 0)
        if total_frames == 0 or not chunks:
            self.meta["gaps"] = []
            self.meta["coverage_ratio"] = 0.0
            self.meta["is_fully_assembled"] = False
            return

        completed_frames = 0
        gaps = []
        for c in chunks:
            if c.get("status") == "completed":
                completed_frames += c.get("frame_count", 0)
            else:
                gaps.append({
                    "chunk_index": c["chunk_index"],
                    "start_frame": c["start_frame"],
                    "end_frame": c["end_frame"],
                    "frame_count": c.get("frame_count", 0),
                })

        ratio = completed_frames / float(total_frames) if total_frames > 0 else 0.0
        self.meta["coverage_ratio"] = round(ratio, 4)
        self.meta["gaps"] = gaps
        self.meta["is_fully_assembled"] = (len(gaps) == 0 and ratio >= 0.999)

    def patch_chunk(
        self,
        chunk_index: int,
        start_frame: int,
        end_frame: int,
        edited_images: torch.Tensor,
        edited_audio: Optional[Dict[str, Any]] = None,
        seam_blend_frames: int = 2,
    ) -> Dict[str, Any]:
        """In-place replaces frames in the master video tensor with edited content."""
        total_frames = int(self.meta.get("total_frames") or edited_images.shape[0])
        meta_w = int(self.meta.get("width") or 0)
        meta_h = int(self.meta.get("height") or 0)
        W = meta_w if meta_w > 0 else int(edited_images.shape[2])
        H = meta_h if meta_h > 0 else int(edited_images.shape[1])

        # If master_frames not yet populated (e.g. loaded lazily from video file), lazy-initialize base on CPU
        if self.master_frames is None:
            src_video = self.meta.get("source_video_path", "")
            if src_video and os.path.isfile(src_video):
                # Populate base frames from video file
                fps = self.meta.get("fps", 24.0)
                try:
                    self.master_frames = extract_video_chunk_ffmpeg(
                        video_path=src_video,
                        start_frame=0,
                        chunk_length=total_frames,
                        force_fps=fps,
                        target_width=W,
                        target_height=H,
                    )
                except Exception as e:
                    logger.warning("[Timeline] Failed to lazy-load master video base: %s", e)
                    self.master_frames = torch.zeros((total_frames, H, W, 3), dtype=torch.float32)
            else:
                self.master_frames = torch.zeros((total_frames, H, W, 3), dtype=torch.float32)
        else:
            # If master_frames already exists, ensure frame count matches total_frames
            if self.master_frames.shape[0] != total_frames:
                if self.master_frames.shape[0] > total_frames:
                    self.master_frames = self.master_frames[:total_frames]
                else:
                    pad_count = total_frames - self.master_frames.shape[0]
                    last_frame = self.master_frames[-1:].repeat(pad_count, 1, 1, 1)
                    self.master_frames = torch.cat([self.master_frames, last_frame], dim=0)

            # Check spatial resolution alignment with meta
            m_h, m_w = self.master_frames.shape[1], self.master_frames.shape[2]
            if (m_h != H or m_w != W) and W > 0 and H > 0:
                unprocessed = all(c.get("status") == "unprocessed" for c in self.meta.get("chunks", []))
                src_video = self.meta.get("source_video_path", "")
                if unprocessed and src_video and os.path.isfile(src_video):
                    fps = self.meta.get("fps", 24.0)
                    try:
                        self.master_frames = extract_video_chunk_ffmpeg(
                            video_path=src_video,
                            start_frame=0,
                            chunk_length=total_frames,
                            force_fps=fps,
                            target_width=W,
                            target_height=H,
                        )
                    except Exception as e:
                        logger.warning("[Timeline] Failed to reload master video base: %s", e)
                        perm = self.master_frames.permute(0, 3, 1, 2)
                        self.master_frames = torch.nn.functional.interpolate(
                            perm, size=(H, W), mode="bilinear", align_corners=False
                        ).permute(0, 2, 3, 1).contiguous().clamp(0.0, 1.0)
                else:
                    perm = self.master_frames.permute(0, 3, 1, 2)
                    self.master_frames = torch.nn.functional.interpolate(
                        perm, size=(H, W), mode="bilinear", align_corners=False
                    ).permute(0, 2, 3, 1).contiguous().clamp(0.0, 1.0)

        start_frame = max(0, min(start_frame, total_frames))
        end_frame = max(start_frame, min(end_frame, total_frames))
        expected_len = end_frame - start_frame

        if edited_images.shape[0] != expected_len:
            if edited_images.shape[0] > expected_len:
                edited_images = edited_images[:expected_len]
            else:
                pad_count = expected_len - edited_images.shape[0]
                last_frame = edited_images[-1:].repeat(pad_count, 1, 1, 1)
                edited_images = torch.cat([edited_images, last_frame], dim=0)

        patch_cpu = edited_images.detach().to(device="cpu", dtype=torch.float32)

        # Spatial resolution matching between patch_cpu and master_frames:
        target_h = self.master_frames.shape[1]
        target_w = self.master_frames.shape[2]
        if patch_cpu.shape[1] != target_h or patch_cpu.shape[2] != target_w:
            logger.info(
                "[Timeline] Auto-resizing patch from (%d, %d) to match master timeline (%d, %d)",
                patch_cpu.shape[2], patch_cpu.shape[1], target_w, target_h
            )
            perm = patch_cpu.permute(0, 3, 1, 2)
            resized = torch.nn.functional.interpolate(
                perm,
                size=(target_h, target_w),
                mode="bilinear",
                align_corners=False
            )
            patch_cpu = resized.permute(0, 2, 3, 1).contiguous().clamp(0.0, 1.0)

        blend = max(0, int(seam_blend_frames))
        if blend > 0 and expected_len > 0:
            blend = min(blend, expected_len // 2)

        if blend > 0:
            if start_frame > 0 and blend <= expected_len:
                t = torch.linspace(0.0, math.pi, blend, device="cpu", dtype=torch.float32)
                alpha = (0.5 - 0.5 * torch.cos(t)).view(blend, 1, 1, 1)
                orig_left = self.master_frames[start_frame:start_frame + blend]
                patch_cpu[:blend] = orig_left * (1.0 - alpha) + patch_cpu[:blend] * alpha

            if end_frame < total_frames and blend <= expected_len:
                t = torch.linspace(0.0, math.pi, blend, device="cpu", dtype=torch.float32)
                alpha = (0.5 + 0.5 * torch.cos(t)).view(blend, 1, 1, 1)
                orig_right = self.master_frames[end_frame - blend:end_frame]
                patch_cpu[-blend:] = orig_right * (1.0 - alpha) + patch_cpu[-blend:] * alpha

        self.master_frames[start_frame:end_frame] = patch_cpu

        fps = float(self.meta.get("fps", 24.0))
        # Ensure master_audio is initialized if session was started lazily from video file
        if self.master_audio is None:
            src_video = self.meta.get("source_video_path", "")
            if src_video and os.path.isfile(src_video):
                full_aud = extract_audio_chunk_ffmpeg(
                    video_path=src_video,
                    start_frame=0,
                    chunk_length=total_frames,
                    force_fps=fps,
                )
                if full_aud is not None:
                    self.master_audio = standardize_audio_dict(full_aud)
            if self.master_audio is None:
                aud_sr = int(edited_audio.get("sample_rate", 44100)) if (edited_audio and isinstance(edited_audio, dict)) else 44100
                total_s = int(round(total_frames / fps * aud_sr))
                self.master_audio = {
                    "waveform": torch.zeros((1, 2, max(1024, total_s)), dtype=torch.float32),
                    "sample_rate": aud_sr,
                }

        if edited_audio is not None and isinstance(edited_audio, dict) and "waveform" in edited_audio:
            norm_edited = standardize_audio_dict(edited_audio)
            sr = int(norm_edited["sample_rate"])
            wave = norm_edited["waveform"].detach().to(device="cpu", dtype=torch.float32)
            if self.master_audio is not None:
                master_wave = self.master_audio["waveform"]
                s_start = int(round(start_frame / fps * sr))
                s_end = int(round(end_frame / fps * sr))
                target_samples = max(0, s_end - s_start)
                if wave.shape[-1] != target_samples:
                    if wave.shape[-1] > target_samples:
                        wave = wave[..., :target_samples]
                    else:
                        pad = torch.zeros(
                            list(wave.shape[:-1]) + [target_samples - wave.shape[-1]],
                            device="cpu", dtype=wave.dtype
                        )
                        wave = torch.cat([wave, pad], dim=-1)
                # Channel matching if needed (e.g. mono vs stereo)
                if master_wave.shape[1] != wave.shape[1]:
                    if master_wave.shape[1] == 2 and wave.shape[1] == 1:
                        wave = wave.repeat(1, 2, 1)
                    elif master_wave.shape[1] == 1 and wave.shape[1] == 2:
                        master_wave = master_wave.repeat(1, 2, 1)
                        self.master_audio["waveform"] = master_wave
                if s_end <= master_wave.shape[-1]:
                    master_wave[..., s_start:s_end] = wave

        chunks = self.meta.get("chunks", [])
        updated_chunk = None
        for c in chunks:
            if c.get("chunk_index") == chunk_index or (c["start_frame"] == start_frame and c["end_frame"] == end_frame):
                c["status"] = "completed"
                c["updated_at"] = time.time()
                c["version"] = c.get("version", 0) + 1
                updated_chunk = c
                break

        if updated_chunk is None:
            updated_chunk = {
                "chunk_index": chunk_index,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "frame_count": expected_len,
                "status": "completed",
                "updated_at": time.time(),
                "version": 1,
            }
            chunks.append(updated_chunk)

        self._save_chunk_thumbnail(chunk_index, patch_cpu)
        self._update_gap_analysis()
        self.save_to_disk()
        return updated_chunk

    def _save_chunk_thumbnail(self, chunk_index: int, patch_cpu: torch.Tensor):
        try:
            tl_dir = _get_timeline_dir(self.project_name)
            thumb_path = os.path.join(tl_dir, f"chunk_{chunk_index}_thumb.jpg")
            mid_idx = patch_cpu.shape[0] // 2
            mid_frame = patch_cpu[mid_idx].clamp(0.0, 1.0).numpy()
            img = Image.fromarray((mid_frame * 255).astype("uint8"))
            img.thumbnail((320, 180), Image.Resampling.BILINEAR)
            img.save(thumb_path, "JPEG", quality=85)
        except Exception as e:
            logger.debug("[Timeline] Failed to save chunk thumbnail: %s", e)

    def reset_chunk(self, chunk_index: int) -> bool:
        chunks = self.meta.get("chunks", [])
        found = False
        for c in chunks:
            if c.get("chunk_index") == chunk_index:
                c["status"] = "unprocessed"
                c["updated_at"] = time.time()
                found = True
                break
        if found:
            self._update_gap_analysis()
            self.save_to_disk()
        return found

    def reset_project(self):
        for c in self.meta.get("chunks", []):
            c["status"] = "unprocessed"
            c["updated_at"] = 0.0
            c["version"] = 0
        self.master_frames = None
        self.master_audio = None
        st_path = _get_master_safetensor_path(self.project_name)
        if os.path.isfile(st_path):
            try:
                os.remove(st_path)
            except Exception as e:
                logger.warning("[Timeline] Failed to remove master safetensor on reset: %s", e)
        self._update_gap_analysis()
        self.save_to_disk()

    def get_previous_reference_frames(
        self,
        start_frame: int,
        ref_frames_count: int = 16,
        first_chunk_mode: str = "Current Chunk First Frame (当前片段首帧)",
        current_chunk_images: Optional[torch.Tensor] = None,
        optional_first_frame_ref: Optional[torch.Tensor] = None,
        target_width: int = 0,
        target_height: int = 0,
        force_fps: float = 24.0,
    ) -> Tuple[torch.Tensor, torch.Tensor, bool]:
        """Extracts the last frame (1 frame) and sequence (N frames) preceding start_frame.

        If the preceding segment has already been patched/reassembled into master_frames,
        the returned frames will be the EDITED frames.

        Returns:
            Tuple[torch.Tensor, torch.Tensor, bool]:
                - prev_last_frame: [1, H, W, 3] (single frame for image reference)
                - prev_ref_frames: [N, H, W, 3] (multi-frame sequence for video reference)
                - is_edited: bool, whether the reference frames come from completed/edited chunks
        """
        N = max(1, int(ref_frames_count))

        # Determine target spatial resolution
        if current_chunk_images is not None and current_chunk_images.ndim == 4:
            H, W = int(current_chunk_images.shape[1]), int(current_chunk_images.shape[2])
        else:
            H = target_height if target_height > 0 else int(self.meta.get("height") or 1080)
            W = target_width if target_width > 0 else int(self.meta.get("width") or 1920)
        H = max(16, (H // 2) * 2)
        W = max(16, (W // 2) * 2)

        def _standardize(t: Any, out_h: int, out_w: int) -> torch.Tensor:
            if not isinstance(t, torch.Tensor):
                return torch.zeros((1, out_h, out_w, 3), dtype=torch.float32)
            t = t.detach().to(device="cpu", dtype=torch.float32)
            if t.ndim == 3:
                t = t.unsqueeze(0)
            elif t.ndim == 4 and t.shape[-1] != 3 and t.shape[1] == 3:
                t = t.permute(0, 2, 3, 1)
            if t.shape[1] != out_h or t.shape[2] != out_w:
                perm = t.permute(0, 3, 1, 2)
                resized = torch.nn.functional.interpolate(
                    perm, size=(out_h, out_w), mode="bilinear", align_corners=False
                )
                t = resized.permute(0, 2, 3, 1).contiguous()
            return t.clamp(0.0, 1.0)

        # Scenario 1: First chunk (start_frame <= 0)
        if start_frame <= 0:
            if optional_first_frame_ref is not None:
                ref_t = _standardize(optional_first_frame_ref, H, W)
                last_f = ref_t[-1:].clone()
                if ref_t.shape[0] >= N:
                    seq_f = ref_t[-N:].clone()
                else:
                    pad = ref_t[0:1].repeat(N - ref_t.shape[0], 1, 1, 1)
                    seq_f = torch.cat([pad, ref_t], dim=0)
                return last_f, seq_f, False

            if "Black" in str(first_chunk_mode) or "Zero" in str(first_chunk_mode):
                last_f = torch.zeros((1, H, W, 3), dtype=torch.float32)
                seq_f = torch.zeros((N, H, W, 3), dtype=torch.float32)
                return last_f, seq_f, False

            # Default: Current Chunk First Frame
            if current_chunk_images is not None and current_chunk_images.shape[0] > 0:
                curr = _standardize(current_chunk_images, H, W)
                last_f = curr[0:1].clone()
                if curr.shape[0] >= N:
                    seq_f = curr[:N].clone()
                else:
                    pad = curr[-1:].repeat(N - curr.shape[0], 1, 1, 1)
                    seq_f = torch.cat([curr, pad], dim=0)
                return last_f, seq_f, False
            else:
                last_f = torch.zeros((1, H, W, 3), dtype=torch.float32)
                seq_f = torch.zeros((N, H, W, 3), dtype=torch.float32)
                return last_f, seq_f, False

        # Scenario 2: Subsequent chunk (start_frame > 0)
        e_prev = int(start_frame)
        s_prev = max(0, e_prev - N)
        k_len = max(1, e_prev - s_prev)

        # Check if the chunk containing the previous frame was completed/edited
        is_edited = False
        for c in self.meta.get("chunks", []):
            if c.get("start_frame", -1) <= (e_prev - 1) < c.get("end_frame", -1):
                if c.get("status") == "completed":
                    is_edited = True
                break

        # Attempt to load master_frames if not in memory
        if self.master_frames is None and st_load is not None:
            st_path = _get_master_safetensor_path(self.project_name)
            if os.path.isfile(st_path):
                self._load_from_disk()

        raw: Optional[torch.Tensor] = None
        if self.master_frames is not None and self.master_frames.shape[0] >= e_prev:
            raw = self.master_frames[s_prev:e_prev].clone()
        else:
            # Fallback: extract directly from video file or connected images
            src_video = self.meta.get("source_video_path", "")
            if src_video and os.path.isfile(src_video):
                fps = float(self.meta.get("fps", force_fps) or force_fps)
                try:
                    raw = extract_video_chunk_ffmpeg(
                        video_path=src_video,
                        start_frame=s_prev,
                        chunk_length=k_len,
                        force_fps=fps,
                        target_width=W,
                        target_height=H,
                    )
                except Exception as e:
                    logger.warning("[Timeline] Failed to extract prev reference frames from video: %s", e)

            if raw is None and current_chunk_images is not None:
                raw = current_chunk_images[0:1].repeat(k_len, 1, 1, 1)

            if raw is None:
                raw = torch.zeros((k_len, H, W, 3), dtype=torch.float32)

        raw = _standardize(raw, H, W)
        if raw.shape[0] < N:
            pad = raw[0:1].repeat(N - raw.shape[0], 1, 1, 1)
            seq_f = torch.cat([pad, raw], dim=0)
        else:
            seq_f = raw[-N:].clone()

        last_f = seq_f[-1:].clone()
        return last_f, seq_f, is_edited


@project_locked
def get_or_create_timeline_session(project_name: str) -> TimelineSession:
    p_name = (project_name or "Video_Edit_Project").strip()
    with _SESSION_CACHE_LOCK:
        if p_name not in _SESSION_CACHE:
            _SESSION_CACHE[p_name] = TimelineSession(p_name)
        return _SESSION_CACHE[p_name]


def slice_video_and_audio(
    project_name: str,
    video_file: Optional[str] = None,
    images: Optional[torch.Tensor] = None,
    audio: Optional[Dict[str, Any]] = None,
    fps: float = 24.0,
    chunk_length: int = 124,
    chunk_index: int = 0,
    slice_mode: str = "Auto Chunk Grid (网格切分)",
    custom_start_frame: int = 0,
    custom_end_frame: int = 124,
    target_width: int = 0,
    target_height: int = 0,
    prev_ref_frames_count: int = 16,
    first_chunk_ref_mode: str = "Current Chunk First Frame (当前片段首帧)",
    optional_first_frame_ref: Optional[torch.Tensor] = None,
    return_ref_frames: bool = False,
) -> Any:
    """Smart slicing supporting both direct lazy video file decoding and pre-loaded image tensors."""
    session = get_or_create_timeline_session(project_name)
    resolved_video_path = resolve_video_path(video_file) if video_file else ""

    # Mode 1: Lazy direct video file loading (Zero RAM/VRAM explosion)
    if images is None and resolved_video_path:
        session.initialize_from_video_file(
            video_path=resolved_video_path,
            force_fps=fps,
            chunk_length=chunk_length,
            target_width=target_width,
            target_height=target_height,
        )
        total_frames = session.meta.get("total_frames", 124)
        effective_fps = float(session.meta.get("fps", 24.0))

        # Calculate frame window
        if "Custom Range" in slice_mode:
            start_frame = max(0, min(int(custom_start_frame), total_frames - 1))
            end_frame = max(start_frame + 1, min(int(custom_end_frame), total_frames))
            effective_chunk_idx = chunk_index
        elif "Next Unedited" in slice_mode:
            gaps = session.meta.get("gaps", [])
            if gaps:
                chosen = gaps[0]
                start_frame = chosen["start_frame"]
                end_frame = chosen["end_frame"]
                effective_chunk_idx = chosen["chunk_index"]
            else:
                start_frame = 0
                end_frame = min(total_frames, chunk_length)
                effective_chunk_idx = 0
        else:
            effective_chunk_idx = max(0, int(chunk_index))
            start_frame = effective_chunk_idx * chunk_length
            if start_frame >= total_frames:
                effective_chunk_idx = max(0, math.ceil(total_frames / chunk_length) - 1)
                start_frame = effective_chunk_idx * chunk_length
            end_frame = min(total_frames, start_frame + chunk_length)

        cur_len = max(1, end_frame - start_frame)
        chunk_images = extract_video_chunk_ffmpeg(
            video_path=resolved_video_path,
            start_frame=start_frame,
            chunk_length=cur_len,
            force_fps=effective_fps,
            target_width=session.meta.get("width", target_width),
            target_height=session.meta.get("height", target_height),
        )
        chunk_raw_audio = extract_audio_chunk_ffmpeg(
            video_path=resolved_video_path,
            start_frame=start_frame,
            chunk_length=cur_len,
            force_fps=effective_fps,
        )
        dur_samples = int(round(cur_len / effective_fps * 44100))
        chunk_audio = standardize_audio_dict(chunk_raw_audio, default_sr=44100, fallback_samples=dur_samples)

        slice_context = {
            "project_name": session.project_name,
            "source_video_path": resolved_video_path,
            "chunk_index": effective_chunk_idx,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "frame_count": chunk_images.shape[0],
            "chunk_length": chunk_length,
            "total_frames": total_frames,
            "fps": effective_fps,
            "width": chunk_images.shape[2],
            "height": chunk_images.shape[1],
            "timestamp": time.time(),
        }

    # Mode 2: Ingest from connected PyTorch Tensor
    elif images is not None:
        session.initialize_base(images, audio, fps, chunk_length)
        total_frames = images.shape[0]
        effective_fps = float(fps) if fps > 0 else 24.0
        chunk_length = max(16, int(chunk_length))

        if "Custom Range" in slice_mode:
            start_frame = max(0, min(int(custom_start_frame), total_frames - 1))
            end_frame = max(start_frame + 1, min(int(custom_end_frame), total_frames))
            effective_chunk_idx = chunk_index
        elif "Next Unedited" in slice_mode:
            gaps = session.meta.get("gaps", [])
            if gaps:
                chosen = gaps[0]
                start_frame = chosen["start_frame"]
                end_frame = chosen["end_frame"]
                effective_chunk_idx = chosen["chunk_index"]
            else:
                start_frame = 0
                end_frame = min(total_frames, chunk_length)
                effective_chunk_idx = 0
        else:
            effective_chunk_idx = max(0, int(chunk_index))
            start_frame = effective_chunk_idx * chunk_length
            if start_frame >= total_frames:
                effective_chunk_idx = max(0, math.ceil(total_frames / chunk_length) - 1)
                start_frame = effective_chunk_idx * chunk_length
            end_frame = min(total_frames, start_frame + chunk_length)

        chunk_images = images[start_frame:end_frame].clone()
        dur_samples = int(round((end_frame - start_frame) / effective_fps * 44100))
        if audio is not None and "waveform" in audio and "sample_rate" in audio:
            norm_in = standardize_audio_dict(audio)
            sr = int(norm_in["sample_rate"])
            waveform = norm_in["waveform"]
            sample_start = int(round(start_frame / effective_fps * sr))
            sample_end = int(round(end_frame / effective_fps * sr))
            sample_start = max(0, min(sample_start, waveform.shape[-1]))
            sample_end = max(sample_start, min(sample_end, waveform.shape[-1]))
            sliced_wave = waveform[..., sample_start:sample_end].clone()
            chunk_audio = standardize_audio_dict({"waveform": sliced_wave, "sample_rate": sr}, default_sr=sr, fallback_samples=dur_samples)
        else:
            chunk_audio = standardize_audio_dict(None, default_sr=44100, fallback_samples=dur_samples)

        slice_context = {
            "project_name": session.project_name,
            "source_video_path": resolved_video_path,
            "chunk_index": effective_chunk_idx,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "frame_count": end_frame - start_frame,
            "chunk_length": chunk_length,
            "total_frames": total_frames,
            "fps": effective_fps,
            "width": images.shape[2],
            "height": images.shape[1],
            "timestamp": time.time(),
        }
    else:
        raise ValueError("Either 'video_file' or 'images' input must be provided to MiniMaxVideoChunkSlicer")

    # Extract previous reference frames (single tail frame & multi-frame sequence)
    prev_last_frame, prev_ref_frames, is_prev_edited = session.get_previous_reference_frames(
        start_frame=start_frame,
        ref_frames_count=prev_ref_frames_count,
        first_chunk_mode=first_chunk_ref_mode,
        current_chunk_images=chunk_images,
        optional_first_frame_ref=optional_first_frame_ref,
        target_width=session.meta.get("width", target_width),
        target_height=session.meta.get("height", target_height),
        force_fps=effective_fps,
    )

    slice_context["prev_ref_info"] = {
        "has_prev_chunk": bool(start_frame > 0),
        "is_edited": bool(is_prev_edited),
        "prev_frame_index": max(0, start_frame - 1),
        "ref_frames_count": int(prev_ref_frames.shape[0]),
        "source": "session_master (edited)" if is_prev_edited else ("base_video" if start_frame > 0 else "first_chunk_fallback"),
    }

    total_chunks = session.meta.get("total_chunks", 1)
    duration_sec = (end_frame - start_frame) / effective_fps
    total_duration = total_frames / effective_fps
    cov = session.meta.get("coverage_ratio", 0.0) * 100
    info_text = (
        f"🎬 [{session.project_name}] Chunk #{effective_chunk_idx + 1}/{total_chunks} "
        f"(Frames: {start_frame}~{end_frame}, {duration_sec:.2f}s/{total_duration:.2f}s) | "
        f"進捗: {cov:.1f}%"
    )
    if start_frame > 0:
        if is_prev_edited:
            info_text += f" | 🔗 直前参照: フレーム #{start_frame - 1} (生成完了成果 ✅)"
        else:
            info_text += f" | 🔗 直前参照: フレーム #{start_frame - 1} (未生成元動画 ⚠️)"
    else:
        info_text += f" | 🔗 直前参照: 初回第0フレーム"

    if return_ref_frames:
        return chunk_images, chunk_audio, slice_context, info_text, prev_last_frame, prev_ref_frames

    return chunk_images, chunk_audio, slice_context, info_text


def render_timeline_indicator_image(
    session: TimelineSession,
    active_chunk_idx: int,
    active_start: int,
    active_end: int,
    width: int = 768,
    height: int = 128,
) -> torch.Tensor:
    """Renders a standalone visual timeline preview card for ComfyUI canvas inspection."""
    img = Image.new("RGB", (width, height), color=(20, 22, 29))
    draw = ImageDraw.Draw(img)

    total_frames = max(1, session.meta.get("total_frames", 1))
    fps = session.meta.get("fps", 24.0)
    chunks = session.meta.get("chunks", [])

    cov = session.meta.get("coverage_ratio", 0.0) * 100
    gaps_count = len(session.meta.get("gaps", []))
    src_title = os.path.basename(session.meta.get("source_video_path", "")) or session.project_name
    header_str = (
        f"[{src_title}] {total_frames} Frames ({total_frames/fps:.1f}s) | "
        f"Progress: {cov:.0f}%"
    )
    if gaps_count > 0:
        header_str += f" | ⚠️ {gaps_count} 箇所未生成"
    else:
        header_str += " | ✅ 全クリップ生成完了"
    draw.text((16, 12), header_str, fill=(226, 232, 240))

    track_x = 16
    track_y = 44
    track_w = width - 32
    track_h = 42

    draw.rectangle([track_x, track_y, track_x + track_w, track_y + track_h], fill=(30, 41, 59), outline=(51, 65, 85))

    for c in chunks:
        sf = c["start_frame"]
        ef = c["end_frame"]
        x1 = track_x + int((sf / total_frames) * track_w)
        x2 = track_x + max(x1 + 2, int((ef / total_frames) * track_w))
        status = c.get("status", "unprocessed")
        c_idx = c.get("chunk_index", -1)
        is_active = (c_idx == active_chunk_idx)

        if status == "completed":
            fill_color = (22, 163, 74) if not is_active else (34, 197, 94)
            outline_color = (134, 239, 172) if is_active else (21, 128, 61)
        else:
            fill_color = (71, 85, 105) if not is_active else (234, 179, 8)
            outline_color = (250, 204, 21) if is_active else (51, 65, 85)

        draw.rectangle([x1, track_y + 2, x2 - 1, track_y + track_h - 2], fill=fill_color, outline=outline_color)
        if (x2 - x1) > 28:
            draw.text((x1 + 4, track_y + 12), f"#{c_idx}", fill=(255, 255, 255))

    ax1 = track_x + int((active_start / total_frames) * track_w)
    ax2 = track_x + max(ax1 + 2, int((active_end / total_frames) * track_w))
    draw.rectangle([ax1, track_y - 2, ax2 - 1, track_y + track_h + 2], outline=(250, 204, 21), width=2)

    draw.rectangle([16, height - 26, 26, height - 16], fill=(22, 163, 74))
    draw.text((32, height - 28), "生成完了 (Completed)", fill=(148, 163, 184))

    draw.rectangle([170, height - 26, 180, height - 16], fill=(71, 85, 105))
    draw.text((186, height - 28), "未生成 (Unprocessed)", fill=(148, 163, 184))

    draw.rectangle([320, height - 26, 330, height - 16], fill=(234, 179, 8))
    draw.text((336, height - 28), "選択中 (Active)", fill=(148, 163, 184))

    return pil_to_tensor(img)


def export_master_to_video_file(
    project_name: str,
    output_dir: Optional[str] = None,
    filename: Optional[str] = None,
    crf: int = 18,
    preset: str = "fast",
) -> Dict[str, Any]:
    """Exports the full assembled master video and audio directly to an H.264/AAC MP4 file.

    Zero-loss pipeline: streams frames and audio directly into FFmpeg without extra ComfyUI nodes.
    """
    session = get_or_create_timeline_session(project_name)
    if session.master_frames is None:
        raise ValueError(f"Project '{project_name}' has no assembled frames to export. Please run at least one chunk first.")

    if output_dir is None:
        try:
            import folder_paths
            output_dir = folder_paths.get_output_directory()
        except Exception:
            output_dir = "output"

    os.makedirs(output_dir, exist_ok=True)

    fps = float(session.meta.get("fps", 24.0))
    total_frames = int(session.master_frames.shape[0])
    H = int(session.master_frames.shape[1])
    W = int(session.master_frames.shape[2])

    if not filename:
        safe_p = "".join(c for c in project_name if c.isalnum() or c in ("-", "_")).strip() or "Video"
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_p}_assembled_{ts}.mp4"

    out_path = os.path.join(output_dir, filename)

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg executable not found in PATH or environment")

    # Audio handling via standard library wave
    has_audio = session.master_audio is not None and "waveform" in session.master_audio
    temp_wav = None

    try:
        cmd = [
            ffmpeg, "-y",
            "-v", "error",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{W}x{H}",
            "-pix_fmt", "rgb24",
            "-r", f"{fps}",
            "-i", "pipe:0",
        ]

        if has_audio:
            try:
                import wave
                sr = int(session.master_audio.get("sample_rate", 44100))
                wave_t = session.master_audio["waveform"].cpu()
                if wave_t.ndim == 1:
                    wave_t = wave_t.unsqueeze(0)
                elif wave_t.ndim == 3:
                    wave_t = wave_t.squeeze(0)
                n_channels = wave_t.shape[0]

                temp_wav = os.path.join(output_dir, f"_temp_export_{int(time.time()*1000)}.wav")
                audio_np = wave_t.numpy()
                audio_np = np.clip(audio_np, -1.0, 1.0)
                audio_int16 = (audio_np * 32767.0).astype(np.int16)
                if n_channels == 2:
                    interleaved = np.empty((audio_int16.shape[1] * 2,), dtype=np.int16)
                    interleaved[0::2] = audio_int16[0]
                    interleaved[1::2] = audio_int16[1]
                    pcm_bytes = interleaved.tobytes()
                else:
                    pcm_bytes = audio_int16[0].tobytes()

                with wave.open(temp_wav, "wb") as wf:
                    wf.setnchannels(n_channels)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(pcm_bytes)

                cmd.extend(["-i", temp_wav, "-c:a", "aac", "-b:a", "192k"])
            except Exception as e:
                logger.warning("[Export Video] Audio processing failed, exporting video only: %s", e)
                has_audio = False

        cmd.extend([
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", str(crf),
            "-preset", preset,
            out_path
        ])

        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        frames = session.master_frames
        if frames.dtype != torch.uint8:
            frames = (torch.clamp(frames, 0.0, 1.0) * 255.0).round().to(torch.uint8)

        raw_bytes = frames.cpu().numpy().tobytes()
        stdout_data, stderr_data = proc.communicate(input=raw_bytes)

        if proc.returncode != 0:
            err = stderr_data.decode("utf-8", errors="replace")
            raise RuntimeError(f"FFmpeg export failed: {err}")

        dur = total_frames / max(fps, 1e-4)
        file_size = os.path.getsize(out_path) if os.path.isfile(out_path) else 0

        return {
            "success": True,
            "file_name": filename,
            "file_path": out_path,
            "duration": round(dur, 2),
            "total_frames": total_frames,
            "fps": fps,
            "width": W,
            "height": H,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "has_audio": has_audio,
        }
    finally:
        if temp_wav and os.path.isfile(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass

