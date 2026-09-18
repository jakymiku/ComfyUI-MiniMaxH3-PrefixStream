"""ComfyUI custom nodes for MiniMax H3 masked AV continuation and chaining.

Exposes:
- MiniMaxPrefixCacheConfig: Select continuation mode and a visible-video context length.
- MiniMaxPrefixCacheApplier: Builds native AV masks or Safe Native fallback conditioning.
- MiniMaxTrimPrefixLatent: Automatically trims leading overlap frames from generated clips (AV unified).
- MiniMaxLongVideoStitcher: Smoothly stitches video and audio latents between clips in unified LATENT space.
- MiniMaxCacheMonitor: Real-time diagnostics for VRAM, memory footprint, and session progress.
"""

import os
import json
import logging
from typing import Dict, Any, Tuple, Optional
import torch

try:
    from safetensors.torch import load_file as st_load, save_file as st_save
except ImportError:
    try:
        import safetensors.torch
        st_load = safetensors.torch.load_file
        st_save = safetensors.torch.save_file
    except ImportError:
        st_load = None
        st_save = None

logger = logging.getLogger("minimax_prefix_stream")

try:
    from .engine.cache_manager import (
        KVCacheConfig,
        PrefixKVCacheManager,
        pixel_frames_to_latent_steps,
        latent_steps_to_pixel_frames,
    )
    from .pipeline.long_video_director import LongVideoSession
    from .pipeline.disk_stream import MiniMaxDiskVideoStreamNode, MiniMaxSelectedClipExportNode
    from .pipeline.native_masked_av import apply_native_masked_av
    from .pipeline.seam_protector import (
        audio_equal_power_crossfade,
        latent_soft_blend,
        stitch_video_latents,
        stitch_audio_latents,
        trim_prefix_frames,
        trim_audio_latents,
        trim_audio_waveform,
        stitch_video_images,
        stitch_audio_waveforms,
        trim_images_and_audio,
        estimate_luminance_gain,
        apply_luminance_gain_fade,
        _standardize_image_tensor,
        _standardize_audio_dict,
    )
    from .engine.clip_bin_manager import (
        save_clip_asset,
        load_clip_asset,
        get_project_dir,
        list_projects,
        load_project_index,
        get_clips_for_selection,
        format_clip_label,
        pil_to_tensor,
        create_placeholder_card,
    )
    from .engine.timeline_session_manager import (
        get_or_create_timeline_session,
        slice_video_and_audio,
        render_timeline_indicator_image,
        get_input_video_files,
        resolve_video_path,
        standardize_audio_dict,
    )
except (ImportError, ValueError):
    from engine.cache_manager import (
        KVCacheConfig,
        PrefixKVCacheManager,
        pixel_frames_to_latent_steps,
        latent_steps_to_pixel_frames,
    )
    from pipeline.long_video_director import LongVideoSession
    from pipeline.disk_stream import MiniMaxDiskVideoStreamNode, MiniMaxSelectedClipExportNode
    from pipeline.native_masked_av import apply_native_masked_av
    from pipeline.seam_protector import (
        audio_equal_power_crossfade,
        latent_soft_blend,
        stitch_video_latents,
        stitch_audio_latents,
        trim_prefix_frames,
        trim_audio_latents,
        trim_audio_waveform,
        stitch_video_images,
        stitch_audio_waveforms,
        trim_images_and_audio,
        estimate_luminance_gain,
        apply_luminance_gain_fade,
        _standardize_image_tensor,
        _standardize_audio_dict,
    )
    from engine.clip_bin_manager import (
        save_clip_asset,
        load_clip_asset,
        get_project_dir,
        list_projects,
        load_project_index,
        get_clips_for_selection,
        format_clip_label,
        pil_to_tensor,
        create_placeholder_card,
    )
    from engine.timeline_session_manager import (
        get_or_create_timeline_session,
        slice_video_and_audio,
        render_timeline_indicator_image,
        get_input_video_files,
        resolve_video_path,
        standardize_audio_dict,
    )


class AnyType(str):
    """Wildcard type for ComfyUI input slots to accept multiple types (STRING, VHS_FILENAMES, etc.)."""
    def __ne__(self, __value: object) -> bool:
        return False

    def __eq__(self, __value: object) -> bool:
        return True


any_type = AnyType("*")


class MiniMaxPrefixCacheConfigNode:
    """Configures the user-facing continuation mode and video context length."""

    @classmethod
    def INPUT_TYPES(cls):

        return {
            "required": {
                "cache_mode": ([
                    "Native Masked AV (Recommended)",
                    "Safe Native (Fallback)"
                ], {
                    "default": "Native Masked AV (Recommended)",
                    "tooltip": "【継続生成方式】Native Masked AV (推奨): 前クリップの音画潜在(AV Latent)を先頭に配置し、ComfyUI標準のデノイズマスクで厳密に保護します（画質劣化・ブロックノイズ防止）。Safe Native: 旧仕様ワークフロー向けのフォールバック方式です。"
                }),
                "continuation_frames": (["39", "90", "141", "192"], {
                    "default": "39",
                    "tooltip": "【参照フレーム数】直前クリップから引き継ぐ末尾参照フレーム数です（39フレーム ≒ 約1.6秒。17k+5のモデル境界に最適化されます）。"
                }),
            },
            "optional": {
                "audio_tail_carryover": ([
                    "Match Video Handover",
                    "Full Previous Tail"
                ], {
                    "default": "Match Video Handover",
                    "tooltip": "【音声引き継ぎ方式】Match Video Handover: 音声保護区間を映像と100%厳密同期させ、音ズレや二重再生を防止します。Full Previous Tail: 直前クリップの末尾音声をそのまま保持します。"
                }),
                "audio_feather_ticks": ("INT", {
                    "default": 2, "min": 0, "max": 16, "step": 1,
                    "tooltip": "【音声マスク境界フェザー】音声接合部のコサイン補間ステップ数（40Hz Latent周期、推奨2〜4ステップ。音飛びやノイズを防止します）。"
                }),
            }
        }

    RETURN_TYPES = ("MINIMAX_CACHE_CONFIG",)
    RETURN_NAMES = ("cache_config",)
    FUNCTION = "create_config"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def create_config(
        self,
        cache_mode: str = "Native Masked AV (Recommended)",
        continuation_frames: Any = "39",
        audio_tail_carryover: str = "Match Video Handover",
        audio_feather_ticks: int = 2,
        **kwargs
    ) -> Tuple[KVCacheConfig]:
        # Accept a saved legacy rolling_frames value if an old workflow sends it.
        actual_rolling = kwargs.get("rolling_frames", continuation_frames)
        try:
            r_frames = int(actual_rolling)
        except (ValueError, TypeError):
            r_frames = 39

        config = KVCacheConfig(
            cache_mode=cache_mode,
            rolling_frames=r_frames,
            audio_tail_carryover=audio_tail_carryover,
            audio_feather_ticks=int(audio_feather_ticks)
        )
        return (config,)



def _unpack_latent(latent_dict: Optional[Dict[str, Any]]) -> Tuple[Optional[torch.Tensor], Optional[torch.Tensor]]:
    """Unpacks video and audio tensors from an H3 latent dict, handling NestedTensor."""
    if latent_dict is None:
        return None, None
    samples = latent_dict.get("samples")
    if samples is None:
        return None, None
    if isinstance(samples, torch.Tensor) and not samples.is_nested:
        return samples, None
    if hasattr(samples, "unbind"):
        parts = list(samples.unbind())
        v = parts[0]
        a = parts[1] if len(parts) > 1 else None
    elif hasattr(samples, "tensors"):
        parts = samples.tensors
        v = parts[0]
        a = parts[1] if len(parts) > 1 else None
    elif isinstance(samples, (tuple, list)):
        v = samples[0]
        a = samples[1] if len(samples) > 1 else None
    elif isinstance(samples, torch.Tensor):
        v = samples
        a = None
    else:
        return None, None
    if v is not None and v.ndim == 4:
        v = v.unsqueeze(0)
    if a is not None and a.ndim == 3:
        a = a.unsqueeze(0)
    return v, a


def pack_av_latent(
    video: torch.Tensor,
    audio: Optional[torch.Tensor] = None,
    original_dict: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Packs video and audio tensors back into an H3 latent dict matching ComfyUI conventions."""
    out = dict(original_dict) if original_dict is not None else {}
    if audio is None:
        out["samples"] = video
        return out

    try:
        import comfy.nested_tensor
        out["samples"] = comfy.nested_tensor.NestedTensor([video, audio])
    except (ImportError, AttributeError):
        out["samples"] = (video, audio)
    return out


def _pack_nested_streams(video: torch.Tensor, audio: torch.Tensor):
    """Pack two H3 streams without importing ComfyUI during standalone tests."""
    try:
        import comfy.nested_tensor
        return comfy.nested_tensor.NestedTensor((video, audio))
    except (ImportError, AttributeError):
        return (video, audio)


def _drop_head_keyframes(conditioning: Any, protected_frames: int):
    """Remove native keyframes that collide with the hard-protected masked head."""
    if not conditioning:
        return conditioning
    out = []
    for item in conditioning:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            out.append(item)
            continue
        embedding, extra = item
        updated = dict(extra)
        prior = updated.get("minimax_keyframes") or []
        updated["minimax_keyframes"] = [
            dict(kf) for kf in prior
            if float(kf.get("resolved_frame_index", 0)) >= float(protected_frames)
        ]
        out.append([embedding, updated])
    return out


def _require_native_masked_av_support() -> None:
    """Probe for ComfyUI's native MiniMax H3 per-stream mask implementation."""
    import inspect
    try:
        import comfy.model_base as model_base
        import comfy.ldm.minimax.model as h3_model
    except Exception as exc:
        raise RuntimeError(
            "Native Masked AV requires a current ComfyUI build with MiniMax H3 AV-mask support (PR #15375)."
        ) from exc

    base_cls = getattr(model_base, "MiniMaxH3", None)
    model_cls = getattr(h3_model, "MiniMaxH3Model", None)
    forward = getattr(model_cls, "forward", None) if model_cls is not None else None
    inner = getattr(model_cls, "_forward", None) if model_cls is not None else None
    scale = base_cls.__dict__.get("scale_latent_inpaint") if base_cls is not None else None
    extra_conds = getattr(base_cls, "extra_conds", None) if base_cls is not None else None

    def has_params(fn, *names):
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False
        return all(name in params for name in names)

    def code_names(fn):
        import types
        found = set()
        def walk(code):
            if not isinstance(code, types.CodeType):
                return
            found.update(code.co_names)
            found.update(value for value in code.co_consts if isinstance(value, str))
            for value in code.co_consts:
                if isinstance(value, types.CodeType):
                    walk(value)
        walk(getattr(fn, "__code__", None))
        return found

    extra_names = code_names(extra_conds) if callable(extra_conds) else set()

    available = all((
        base_cls is not None,
        callable(getattr(h3_model, "mask_row_values", None)),
        callable(forward) and has_params(forward, "denoise_mask", "audio_denoise_mask"),
        callable(inner) and has_params(inner, "denoise_mask", "audio_denoise_mask"),
        callable(base_cls.__dict__.get("_token_grid_masks")) if base_cls is not None else False,
        callable(base_cls.__dict__.get("_denoise_mask_conds")) if base_cls is not None else False,
        callable(scale) and has_params(scale, "x", "denoise_mask"),
        callable(extra_conds) and "denoise_mask" in extra_names and "_denoise_mask_conds" in extra_names,
    ))
    if not available:
        raise RuntimeError(
            "Native Masked AV requires a current ComfyUI build with MiniMax H3 AV-mask support from PR #15375. "
            "Update ComfyUI, restart it completely, and reload the workflow."
        )


class MiniMaxPrefixCacheApplierNode:
    """Builds Native Masked AV sampling input or Safe Native fallback conditioning."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "conditioning": ("CONDITIONING",),
            },
            "optional": {
                "cache_config": ("MINIMAX_CACHE_CONFIG", {"tooltip": "MiniMax H3 継続生成設定ノードから出力された設定オブジェクト。"}),
                "context_latent": ("LATENT", {"tooltip": "【直前クリップの潜在データ】直前クリップの音画潜在(AV Latent)を入力します。初回生成時は空のままで構いません。"}),
                "target_latent": ("LATENT", {"tooltip": "【生成対象の潜在データ】MiniMaxH3ReferenceToVideo の潜在出力を接続します。Native Masked AV 方式では保護マスク付きの潜在データを出力します。"}),
            }
        }

    RETURN_TYPES = ("MODEL", "CONDITIONING", "MINIMAX_SESSION", "LATENT")
    RETURN_NAMES = ("model", "conditioning", "session", "masked_latent")
    FUNCTION = "apply_cache"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def apply_cache(
        self,
        model: Any,
        conditioning: Any,
        cache_config: Optional[KVCacheConfig] = None,
        context_latent: Optional[Dict[str, Any]] = None,
        target_latent: Optional[Dict[str, Any]] = None,
        **legacy: Any,
    ) -> Tuple[Any, Any, LongVideoSession, Optional[Dict[str, Any]]]:
        cfg = cache_config or KVCacheConfig()
        session = legacy.get("session")
        sess = session or LongVideoSession(cfg)
        if session is not None and cache_config is not None:
            sess.config = cfg

        # Legacy aliases are accepted only for previously saved workflows; they
        # are intentionally absent from the user-facing node interface.
        ctx_target = context_latent if context_latent is not None else legacy.get("context_video_latent")

        # Unpack video and audio from latents (supports ComfyUI NestedTensor from H3ContinuousLoadLatent)
        v_ctx, a_ctx_from_latent = _unpack_latent(ctx_target)
        a_ctx = a_ctx_from_latent

        def safe_native_result(reason: Optional[Exception] = None):
            """Run the compatibility path when a native AV mask cannot be built."""
            if reason is not None:
                logger.warning(
                    "[Native Masked AV] %s Falling back to Safe Native for this clip; "
                    "use a source and target of at least 39 frames to enable native masks.",
                    reason,
                )
            patched_model, out_cond = sess.prepare_next_clip(
                model_patcher=model,
                conditioning=conditioning,
                previous_video_latent=v_ctx,
                previous_audio_latent=a_ctx,
                anchor_video_latent=None,
            )
            return (patched_model, out_cond, sess, target_latent)

        if cfg.is_native_masked_av_mode():
            if ctx_target is None:
                logger.info("[Native Masked AV] Initial clip: target latent passes through without a protected prefix.")
                return (model, conditioning, sess, target_latent)
            if target_latent is None:
                raise ValueError(
                    "Native Masked AV mode requires target_latent from MiniMaxH3ReferenceToVideo. "
                    "Connect the applier's masked_latent output to the sampler latent input."
                )
            if v_ctx is None or a_ctx_from_latent is None:
                raise ValueError("Native Masked AV requires a previous latent containing both video and audio streams")
            target_video, target_audio = _unpack_latent(target_latent)
            if target_video is None or target_audio is None:
                raise ValueError("Native Masked AV requires a target latent containing both video and audio streams")
            logger.info(
                "[Native Masked AV] Source %d video steps/%d frames, target %d video steps/%d frames.",
                v_ctx.shape[2], latent_steps_to_pixel_frames(v_ctx.shape[2]),
                target_video.shape[2], latent_steps_to_pixel_frames(target_video.shape[2]),
            )
            _require_native_masked_av_support()
            carryover_mode = legacy.get("audio_tail_carryover", getattr(cfg, "audio_tail_carryover", "Match Video Handover"))
            feather_ticks = int(legacy.get("audio_feather_ticks", getattr(cfg, "audio_feather_ticks", 2)))
            sess.is_current_clip_trimmed = False
            try:
                out_v, out_a, video_mask, audio_mask, plan = apply_native_masked_av(
                    target_video=target_video,
                    target_audio=target_audio,
                    source_video=v_ctx,
                    source_audio=a_ctx_from_latent,
                    context_frames=cfg.rolling_frames,
                    audio_tail_carryover=carryover_mode,
                    audio_feather_ticks=feather_ticks,
                )
            except ValueError as exc:
                geometry_errors = (
                    "at least 39 source frames",
                    "consume the whole target",
                    "has no phase-aligned",
                )
                if any(text in str(exc) for text in geometry_errors):
                    return safe_native_result(exc)
                raise
            masked_latent = pack_av_latent(out_v, out_a, target_latent)
            masked_latent["noise_mask"] = _pack_nested_streams(video_mask, audio_mask)
            masked_latent["minimax_prefix_frames"] = int(plan["actual_context_frames"])
            sess.last_rolling_steps = int(plan["context_steps"])
            sess.last_rolling_frames = int(plan["actual_context_frames"])
            out_cond = _drop_head_keyframes(conditioning, sess.last_rolling_frames)
            logger.info(
                "[Native Masked AV] Protected %d video frames/%d audio ticks; source latent %d:%d.",
                sess.last_rolling_frames,
                plan["audio_steps"],
                plan["start_t"],
                plan["end_t"],
            )
            return (model, out_cond, sess, masked_latent)

        # Safe Native fallback: inject grid-aligned keyframe conditioning only.
        return safe_native_result()


class MiniMaxTrimPrefixLatentNode:
    """Automatically trims redundant prefix overlap frames from video and audio.
    
    SUPPORTED MODES:
    1. Pixel & Waveform Space (RECOMMENDED): Connect decoded 'images' and 'audio'.
       Trims leading frames directly in pixel space, guaranteeing ZERO VAE flicker and ZERO color distortion!
    2. Latent Space: Connect 'latent'. Trims raw latent steps.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "trim_frames": ("INT", {
                    "default": 0, "min": 0, "max": 124, "step": 1,
                    "tooltip": "【先頭削除フレーム数】直前クリップから引き継いだ重複フレームの削除数です（0を指定しsession/configを接続すると自動計算されます）。"
                }),
            },
            "optional": {
                "images": ("IMAGE", {"tooltip": "【推奨】デコード後の映像フレーム。ピクセル空間で直接トリミングを行い、VAEのフリッカーや色変化を完全に防止します。"}),
                "audio": ("AUDIO", {"tooltip": "【推奨】デコード後の音声データ。ミリ秒単位で厳密に同期トリミングを行います。"}),
                "latent": ("LATENT", {"tooltip": "サンプラーから出力された潜在データ（画像/音声を直接トリミングする場合は省略可能）。"}),
                "session": ("MINIMAX_SESSION",),
                "cache_config": ("MINIMAX_CACHE_CONFIG",),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0}),
                "match_tail": ("BOOLEAN", {"default": True, "tooltip": "【末尾整合】40Hz音声と24fps映像の境界丸め誤差を補正し、厳密に尺を一致させます。"}),
                "video_latent": ("LATENT",),  # Backward compatibility alias
            }
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "LATENT")
    RETURN_NAMES = ("trimmed_images", "trimmed_audio", "trimmed_latent")
    FUNCTION = "trim"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def trim(
        self,
        trim_frames: int = 0,
        images: Optional[torch.Tensor] = None,
        audio: Optional[Dict[str, Any]] = None,
        latent: Optional[Dict[str, Any]] = None,
        video_latent: Optional[Dict[str, Any]] = None,
        session: Optional[LongVideoSession] = None,
        cache_config: Optional[KVCacheConfig] = None,
        fps: float = 24.0,
        match_tail: bool = True,
        **kwargs
    ) -> Tuple[Optional[torch.Tensor], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        # 1. Determine trim frame count
        actual_trim_frames = trim_frames
        if actual_trim_frames <= 0:
            if session is not None:
                # Strictly respect session: 0 for initial clip, >0 for continuation clips
                actual_trim_frames = session.last_rolling_frames
            elif cache_config is not None:
                # Protect initial clip from accidental trimming when session is not connected:
                # Only continuation clips contain 'noise_mask' in target_latent
                target_latent = latent if latent is not None else video_latent
                has_noise_mask = bool(target_latent is not None and isinstance(target_latent, dict) and "noise_mask" in target_latent)
                if has_noise_mask:
                    actual_trim_frames = int(target_latent.get("minimax_prefix_frames", cache_config.rolling_frames))
                else:
                    logger.info("[Trim AV] No session connected and no noise_mask in latent; preserving full initial clip (0 trim frames).")
                    actual_trim_frames = 0


        # 2. Pixel & audio waveform trimming (Golden Standard)
        out_images = None
        out_audio = None
        if images is not None:
            out_images, out_audio = trim_images_and_audio(
                images=images,
                audio=audio,
                trim_frames=actual_trim_frames,
                fps=fps,
                match_tail=match_tail
            )
            logger.info(
                "[Trim AV] Cleanly trimmed %d leading frames in pixel space. Output: %d frames (~%.2fs). Zero VAE flicker.",
                actual_trim_frames, out_images.shape[0], out_images.shape[0] / float(fps)
            )
        elif audio is not None:
            normalized = _standardize_audio_dict(audio)
            if normalized is not None:
                sr = int(normalized.get("sample_rate", 32000))
                head = max(0, round(actual_trim_frames / fps * sr))
                out_audio = dict(normalized, waveform=normalized["waveform"][..., head:])

        if actual_trim_frames > 0 and session is not None:
            session.is_current_clip_trimmed = True

        # 3. Latent trimming (fallback / passthrough)
        out_latent = None
        target_latent = latent if latent is not None else video_latent
        if target_latent is not None:
            trim_steps = 0
            if actual_trim_frames > 0:
                trim_steps = pixel_frames_to_latent_steps(actual_trim_frames)

            v, a_from_latent = _unpack_latent(target_latent)
            if v is None:
                v = target_latent.get("samples")

            if v is not None:
                if trim_steps > 0:
                    trimmed_v = v[:, :, trim_steps:]
                else:
                    trimmed_v = v
                trimmed_a = None
                if a_from_latent is not None:
                    if trim_steps > 0:
                        eff_frames = actual_trim_frames if actual_trim_frames > 0 else latent_steps_to_pixel_frames(trim_steps)
                        audio_trim_steps = min(int(round(eff_frames * 40.0 / 24.0)), a_from_latent.shape[-1])
                        trimmed_a = trim_audio_latents(a_from_latent, audio_trim_steps)
                    else:
                        trimmed_a = a_from_latent
                out_latent = pack_av_latent(trimmed_v, trimmed_a, original_dict=target_latent)
                if trim_steps > 0:
                    out_latent.pop("noise_mask", None)
                    out_latent["minimax_prefix_frames"] = 0
            else:
                out_latent = target_latent

        if out_images is None:
            out_images = images if images is not None else torch.empty((0, 768, 1344, 3), dtype=torch.float32)
        if out_audio is None:
            out_audio = audio

        return (out_images, out_audio, out_latent)



class MiniMaxLongVideoStitcherNode:
    """Seamlessly stitches previous long video and current newly generated clip in pixel & waveform space.

    Applies:
    1. Zero-VAE-distortion pixel-space joining with luminance gain matching and smooth cosine S-curve crossfade.
    2. Waveform-space sample-accurate audio concatenation with linear overlap crossfade (zero click/pop).
    3. Handles initial clip mode gracefully when prev_images or prev_audio is empty or None.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "trim_frames": ("INT", {
                    "default": 0, "min": 0, "max": 192, "step": 1,
                    "tooltip": "【重複削除フレーム数】結合前に削除する重複先頭フレーム数（0を指定しsession/config接続時は自動計算）。"
                }),
                "crossfade_frames": ("INT", {
                    "default": 4, "min": 0, "max": 30, "step": 1,
                    "tooltip": "【クロスフェードフレーム数】映像接合部を滑らかにS字ブレンドするフレーム数（推奨: 2〜6フレーム）。"
                }),
                "luminance_match": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "【輝度自動補正】前後クリップの全体的な明暗差・露出差を自動検出し補正します。"
                }),
                "luminance_fade_frames": ("INT", {
                    "default": 16, "min": 1, "max": 60, "step": 1,
                    "tooltip": "【輝度補正フェードフレーム数】補正ゲインを元の明るさへ徐々に戻す移行フレーム数。"
                }),
                "crossfade_ms": ("FLOAT", {
                    "default": 15.0, "min": 0.0, "max": 500.0, "step": 1.0,
                    "tooltip": "【音声クロスフェード時間(ms)】音声接合部のフェード時間。クリックノイズやプチ音を完全に防止します。"
                }),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0}),
            },
            "optional": {
                "prev_images": ("IMAGE", {"tooltip": "これまで結合された先行映像。初回生成時は空で構いません。"}),
                "curr_images": ("IMAGE", {"tooltip": "今回生成された映像フレーム（VAEDecode または TrimPrefix から）。"}),
                "prev_audio": ("AUDIO", {"tooltip": "これまで結合された先行音声。初回生成時は空で構いません。"}),
                "curr_audio": ("AUDIO", {"tooltip": "今回生成された音声データ（VAEDecodeAudio または TrimPrefix から）。"}),
                "session": ("MINIMAX_SESSION",),
                "cache_config": ("MINIMAX_CACHE_CONFIG",),
            }
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "INT")
    RETURN_NAMES = ("stitched_images", "stitched_audio", "total_frames")
    FUNCTION = "stitch"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def stitch(
        self,
        trim_frames: int = 0,
        crossfade_frames: int = 4,
        luminance_match: bool = True,
        luminance_fade_frames: int = 16,
        crossfade_ms: float = 15.0,
        fps: float = 24.0,
        prev_images: Optional[torch.Tensor] = None,
        curr_images: Optional[torch.Tensor] = None,
        prev_audio: Optional[Dict[str, Any]] = None,
        curr_audio: Optional[Dict[str, Any]] = None,
        session: Optional[LongVideoSession] = None,
        cache_config: Optional[KVCacheConfig] = None,
        **kwargs
    ) -> Tuple[Optional[torch.Tensor], Optional[Dict[str, Any]], int]:
        # Determine trim frames for curr_images/audio
        actual_trim_frames = trim_frames
        if actual_trim_frames <= 0:
            if session is not None and getattr(session, "is_current_clip_trimmed", False):
                logger.info(
                    "[Long Video Stitcher] Input clip was already trimmed upstream by MiniMaxTrimPrefixLatent. "
                    "Skipping duplicate trimming (0 trim frames) to prevent audio/video loss."
                )
                actual_trim_frames = 0
            elif session is not None:
                actual_trim_frames = session.last_rolling_frames
            elif cache_config is not None and prev_images is not None and prev_images.shape[0] > 0:
                actual_trim_frames = cache_config.rolling_frames

        # Standardize inputs
        prev_img_std = _standardize_image_tensor(prev_images)
        curr_img_std = _standardize_image_tensor(curr_images)
        prev_aud_std = _standardize_audio_dict(prev_audio)
        curr_aud_std = _standardize_audio_dict(curr_audio)

        out_images = None
        if curr_img_std is not None and curr_img_std.shape[0] > 0:
            out_images = stitch_video_images(
                prev_images=prev_img_std,
                curr_images=curr_img_std,
                trim_frames=actual_trim_frames,
                crossfade_frames=crossfade_frames,
                luminance_match=luminance_match,
                luminance_fade_frames=luminance_fade_frames,
            )
        elif prev_img_std is not None:
            out_images = prev_img_std
        else:
            out_images = torch.empty((0, 768, 1344, 3), dtype=torch.float32)

        curr_total_f = curr_img_std.shape[0] if curr_img_std is not None else 0
        out_audio = None
        if curr_aud_std is not None:
            out_audio = stitch_audio_waveforms(
                prev_audio=prev_aud_std,
                curr_audio=curr_aud_std,
                curr_total_frames=curr_total_f,
                trim_frames=actual_trim_frames,
                crossfade_ms=crossfade_ms,
                fps=fps,
            )
        else:
            out_audio = prev_aud_std

        total_f = int(out_images.shape[0]) if out_images is not None else 0
        logger.info(
            "[Long Video Stitcher] Seamlessly stitched. Total video: %d frames (~%.2fs).",
            total_f, total_f / float(fps)
        )
        return (out_images, out_audio, total_f)


class MiniMaxCacheMonitorNode:
    """Provides operational telemetry for the current continuation session."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "session": ("MINIMAX_SESSION",),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("telemetry_report",)
    OUTPUT_NODE = True
    FUNCTION = "report"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def report(self, session: LongVideoSession) -> Tuple[str]:
        native_masked = session.config.is_native_masked_av_mode()
        status_str = "Native Masked AV" if native_masked else "Safe Native fallback"
        report_str = (
            f"=== MiniMax H3 Continuation Telemetry ===\n"
            f"Mode: {status_str}\n"
            f"Current Clip: #{session.current_clip_index}\n"
            f"Configured Protected Context: {session.config.rolling_frames} frames\n"
            f"Last Protected Context: {session.last_rolling_frames} frames ({session.last_rolling_steps} latent steps)\n"
            f"Accumulated Clips: {len(session.accumulated_video_latents)}"
        )
        return (report_str,)


class MiniMaxSaveLatentNode:
    """Saves the complete joint MiniMax H3 AV latent to safetensors for seamless continuation.
    
    Guarantees 100% independent, standalone operation without requiring external continuation suites.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {"tooltip": "Sampler output joint AV latent to save."}),
                "filename_prefix": ("STRING", {
                    "default": "minimax_h3/clip",
                    "tooltip": "Subfolder and filename prefix in ComfyUI output directory."
                }),
                "clip_index": ("INT", {
                    "default": 1, "min": 0, "max": 99999, "step": 1,
                    "tooltip": "Fixed chain slot (1, 2, 3...). 0 = auto-incrementing."
                }),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("saved_path", "latent_info")
    OUTPUT_NODE = True
    FUNCTION = "save"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def save(
        self,
        latent: Dict[str, Any],
        filename_prefix: str = "minimax_h3/clip",
        clip_index: int = 1,
        **kwargs
    ) -> Tuple[str, str]:
        if latent is None:
            raise ValueError("MiniMaxSaveLatent: 'latent' input is required.")

        video, audio = _unpack_latent(latent)
        if video is None:
            raise ValueError("MiniMaxSaveLatent: latent contains no video samples.")

        video_cpu = video.detach().cpu().contiguous()
        audio_cpu = audio.detach().cpu().contiguous() if audio is not None else None

        try:
            import folder_paths
            base_dir = folder_paths.get_output_directory()
        except Exception:
            base_dir = "output"

        target_dir = os.path.join(base_dir, os.path.dirname(filename_prefix))
        os.makedirs(target_dir, exist_ok=True)

        base_name = os.path.basename(filename_prefix)
        if clip_index > 0:
            filename = f"{base_name}_{clip_index:05d}.safetensors"
        else:
            filename = f"{base_name}_temp.safetensors"
        full_path = os.path.join(target_dir, filename)

        tensors = {"video": video_cpu}
        if audio_cpu is not None:
            tensors["audio"] = audio_cpu

        frame_count = latent_steps_to_pixel_frames(video_cpu.shape[2])

        if st_save is not None:
            st_save(
                tensors,
                full_path,
                metadata={
                    "format": "minimax_h3_av_latent",
                    "frame_count": str(frame_count),
                    "clip_index": str(clip_index),
                    "video_shape": json.dumps(list(video_cpu.shape)),
                    "audio_shape": json.dumps(list(audio_cpu.shape)) if audio_cpu is not None else "none",
                }
            )
        else:
            torch.save(tensors, full_path)

        info_str = f"{frame_count} frames | Video {tuple(video_cpu.shape)}"
        if audio_cpu is not None:
            info_str += f" | Audio {tuple(audio_cpu.shape)}"
        logger.info("[Save Latent] Successfully saved %s -> %s", info_str, full_path)
        return (full_path, info_str)


class MiniMaxLoadLatentNode:
    """Loads a saved MiniMax H3 joint AV latent for continuation and long-video stitching.
    
    Guarantees 100% independent, standalone operation: compatible with any saved H3 safetensors latent.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent_path": ("STRING", {
                    "default": "minimax_h3/clip",
                    "tooltip": "Folder or filepath relative to ComfyUI output, or absolute path."
                }),
                "clip_index": ("INT", {
                    "default": 1, "min": 0, "max": 99999, "step": 1,
                    "tooltip": "Clip index to load (e.g. 1 to continue Clip 2). 0 = latest file."
                }),
            }
        }

    RETURN_TYPES = ("LATENT", "STRING", "STRING")
    RETURN_NAMES = ("latent", "loaded_path", "latent_info")
    FUNCTION = "load"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def load(
        self,
        latent_path: str = "minimax_h3/clip",
        clip_index: int = 1
    ) -> Tuple[Dict[str, Any], str, str]:
        try:
            import folder_paths
            base_dir = folder_paths.get_output_directory()
        except Exception:
            base_dir = "output"

        p = (latent_path or "").strip().strip('"').strip("'")
        if not os.path.isabs(p):
            full_target = os.path.join(base_dir, p)
        else:
            full_target = p

        if os.path.isfile(full_target):
            target_file = full_target
        else:
            parent_dir = os.path.dirname(full_target)
            base_name = os.path.basename(full_target)
            if os.path.isdir(parent_dir):
                if clip_index > 0:
                    candidates = [f for f in os.listdir(parent_dir) if f.startswith(base_name) and f.endswith(".safetensors")]
                    matched = [f for f in candidates if f"_{clip_index:05d}" in f or f"_{clip_index}." in f or f"_{clip_index}_" in f]
                    if matched:
                        target_file = os.path.join(parent_dir, matched[0])
                    else:
                        target_file = os.path.join(parent_dir, f"{base_name}_{clip_index:05d}.safetensors")
                else:
                    candidates = [os.path.join(parent_dir, f) for f in os.listdir(parent_dir) if f.endswith(".safetensors")]
                    if candidates:
                        target_file = max(candidates, key=os.path.getmtime)
                    else:
                        target_file = full_target
            else:
                target_file = full_target

        if not os.path.exists(target_file):
            raise FileNotFoundError(f"MiniMaxLoadLatent: file not found at '{target_file}'")

        if st_load is not None:
            tensors = st_load(target_file, device="cpu")
        else:
            try:
                tensors = torch.load(target_file, map_location="cpu")
            except RuntimeError as exc:
                if "safetensors is not installed" in str(exc):
                    with open(target_file, "rb") as f:
                        tensors = torch.load(f, map_location="cpu")
                else:
                    raise

        if "video" not in tensors:
            raise ValueError(f"MiniMaxLoadLatent: '{target_file}' does not contain 'video' tensor.")

        video = tensors["video"]
        audio = tensors.get("audio", None)

        out_latent = pack_av_latent(video, audio)
        frame_count = latent_steps_to_pixel_frames(video.shape[2])
        info_str = f"{frame_count} frames | Video {tuple(video.shape)}"
        if audio is not None:
            info_str += f" | Audio {tuple(audio.shape)}"

        logger.info("[Load Latent] Successfully loaded %s (%s)", target_file, info_str)
        return (out_latent, target_file, info_str)


class MiniMaxClipBinSaverNode:
    """Saves a unified MiniMax H3 AV Latent into the Clip Bin media pool with keyframes, preview card, and rich metadata."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {"tooltip": "【音画潜在データ】サンプラーから出力された統合AV Latent (MiniMax H3公式NestedTensor対応)。"}),
                "project_name": ("STRING", {
                    "default": "Default_Project",
                    "tooltip": "【プロジェクト/プール名】クリップを分類・保存するプロジェクトフォルダ名です（例: Default_Project）。"
                }),
                "shot_tag": ("STRING", {
                    "default": "Auto (自动编号)",
                    "tooltip": "【クリップ名/ラベル】クリップの識別ラベル（'Auto (自动编号)' でプール内クリップ数に基づき自動連番 Shot 1, Shot 2...）。"
                }),
                "rating": ("INT", {
                    "default": 4, "min": 1, "max": 5, "step": 1,
                    "tooltip": "【品質評価】1〜5つ星のレーティング。後でギャラリーから高評価クリップのみを抽出して継続できます。"
                }),
            },
            "optional": {
                "images": ("IMAGE", {"tooltip": "【レンダリング映像】デコード後の映像フレーム。登録時に先頭と末尾のサムネイルカードを自動生成します。"}),
                "audio": ("AUDIO", {"tooltip": "【音声データ】クリップに付随する音声データ。MP4保存時に自動で多重化されます。"}),
                "prompt": ("STRING", {"default": "", "tooltip": "【プロンプト】生成時のプロンプト文字列。メタデータに保存されギャラリーで確認できます。"}),
                "parent_clip_id": ("STRING", {"default": "", "tooltip": "【親クリップID】直前のクリップ選択ノードから出力された clip_id を接続し、派生ツリーを記録します。"}),
                "video_file_name": (any_type, {"default": "", "tooltip": "【生成動画ファイル】VHS_VideoCombine 等で保存した MP4 ファイル名を関連付けます。"}),
                "save_video": ("BOOLEAN", {"default": True, "tooltip": "【動画アーカイブ】プール内に動画ファイルを保持し、ギャラリーでのホバー再生・プレビューを有効にします。"}),
            }
        }

    RETURN_TYPES = ("STRING", "IMAGE", "STRING")
    RETURN_NAMES = ("clip_id", "preview_image", "bin_path")
    OUTPUT_NODE = True
    FUNCTION = "save_clip"
    CATEGORY = "MiniMaxH3/ClipBin"

    def save_clip(
        self,
        latent: Dict[str, Any],
        project_name: str = "Default_Project",
        shot_tag: str = "Auto (自动编号)",
        rating: int = 4,
        images: Optional[torch.Tensor] = None,
        audio: Optional[Dict[str, Any]] = None,
        prompt: str = "",
        parent_clip_id: str = "",
        video_file_name: Any = "",
        save_video: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        if latent is None:
            raise ValueError("MiniMaxClipBinSaver: 'latent' input is required.")

        video, audio_lat = _unpack_latent(latent)
        if video is None:
            raise ValueError("MiniMaxClipBinSaver: latent contains no video samples.")

        images = _standardize_image_tensor(images)
        audio = _standardize_audio_dict(audio)

        actual_shot = (shot_tag or "").strip()
        if actual_shot.startswith("Auto") or not actual_shot:
            idx = load_project_index(project_name)
            actual_shot = f"Shot {len(idx.get('clips', [])) + 1}"

        # Handle video_file_name if passed as list/tuple from VHS_VideoCombine Filenames
        resolved_video_name = ""
        def _extract_filename(val: Any) -> str:
            if isinstance(val, (list, tuple)):
                if not val:
                    return ""
                # Prioritize video extensions in list/tuple
                # First pass: Look for audio-enabled video (-audio)
                for item in val:
                    if isinstance(item, (list, tuple)):
                        extracted = _extract_filename(item)
                        if extracted and "-audio" in extracted.lower() and any(extracted.lower().endswith(e) for e in (".mp4", ".webm", ".mov", ".mkv")):
                            return extracted
                    elif isinstance(item, str) and "-audio" in item.lower() and any(item.lower().endswith(e) for e in (".mp4", ".webm", ".mov", ".mkv")):
                        return item.strip()
                # Second pass: Standard videos
                for item in val:
                    if isinstance(item, (list, tuple)):
                        extracted = _extract_filename(item)
                        if extracted and any(extracted.lower().endswith(e) for e in (".mp4", ".webm", ".mov", ".mkv")):
                            return extracted
                    elif isinstance(item, str) and any(item.lower().endswith(e) for e in (".mp4", ".webm", ".mov", ".mkv")):
                        return item.strip()
                return _extract_filename(val[-1])
            s = str(val).strip()
            return s if s.lower() not in ("true", "false", "none") else ""

        if video_file_name is not None:
            resolved_video_name = _extract_filename(video_file_name)
            # If it's a full path, keep basename for friendly display
            if resolved_video_name:
                resolved_video_name = os.path.basename(resolved_video_name)

        meta_obj, clip_dir, preview_pil = save_clip_asset(
            video_tensor=video,
            audio_tensor=audio_lat,
            images=images,
            project_name=project_name,
            shot_tag=actual_shot,
            prompt=prompt if isinstance(prompt, str) else str(prompt),
            rating=rating,
            parent_clip_id=parent_clip_id if isinstance(parent_clip_id, str) else str(parent_clip_id),
            associated_video_path=resolved_video_name,
            raw_video_source=video_file_name,
            audio_dict=audio,
            save_video=save_video,
        )

        preview_tensor = pil_to_tensor(preview_pil)

        try:
            import folder_paths
            base_dir = folder_paths.get_output_directory()
            subfolder = os.path.relpath(clip_dir, os.path.realpath(base_dir))
        except Exception:
            subfolder = ""

        ui_images = [{
            "filename": "preview.png",
            "subfolder": subfolder,
            "type": "output"
        }]

        logger.info("[Clip Bin Saver] Stored clip '%s' in '%s' (%s frames | ⭐%s | tag: %s | video: '%s')",
                    meta_obj.clip_id, project_name, meta_obj.frames, meta_obj.rating, actual_shot, resolved_video_name)

        return {
            "ui": {"images": ui_images},
            "result": (meta_obj.clip_id, preview_tensor, clip_dir)
        }


class MiniMaxClipBinPickerNode:
    """Visually browses, filters, and loads clips from the Clip Bin with instant tail-frame output."""

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        projects = list_projects()
        default_proj = projects[0] if projects else "Default_Project"
        return {
            "required": {
                "project_name": ("STRING", {
                    "default": default_proj,
                    "tooltip": "【プロジェクト選択】読み込むクリッププール名（例: Default_Project）。"
                }),
                "mode": ([
                    "Auto (首段全新 / 后续自动接力)",
                    "Force Initial (强制新建首段，无上下文)",
                    "Strict Chaining (必须接力指定或最新镜头)"
                ], {
                    "default": "Auto (首段全新 / 后续自动接力)",
                    "tooltip": "【動作モード】\n• Auto (推奨): 初回は新規生成、2本目以降は直前の最新クリップから自動継続（配線の差し替え不要）\n• Force Initial: 常に完全新規生成（単発クリップ、過去履歴を無視）\n• Strict Chaining: 厳格な継続モード（プールが空の場合はエラー）"
                }),
                "filter_rating": ([
                    "All (1-5 ⭐)",
                    "⭐⭐⭐+ (3+ ⭐)",
                    "⭐⭐⭐⭐+ (4+ ⭐)",
                    "⭐⭐⭐⭐⭐ (5 ⭐)"
                ], {
                    "default": "All (1-5 ⭐)",
                    "tooltip": "【評価フィルター】指定した星の数以上のクリップのみを一覧に表示します（例: 4星以上のみを抽出）。"
                }),
                "clip_selection": ("STRING", {
                    "default": "latest",
                    "tooltip": "【継続元クリップの指定】\n• 'latest' (推奨): 直前に生成された最新クリップを自動選択\n• clip_id: 特定のクリップIDを指定して別分岐を開始\n• ラベル名: クリップのラベル名で指定"
                }),
            },
            "optional": {
                "custom_clip_path": ("STRING", {
                    "default": "",
                    "tooltip": "【カスタムパス】任意のフォルダからクリップを読み込む場合の絶対パス（通常は空）。"
                }),
                "view_mode": ([
                    "Deck (卡片流)",
                    "Tree (关系树)"
                ], {
                    "default": "Deck (卡片流)",
                    "tooltip": "【表示形式】🎴 カード一覧 または 🌳 系統ツリー（ノード上のボタンでも切替可能）。"
                }),
            }
        }

    RETURN_TYPES = ("LATENT", "IMAGE", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("latent", "tail_frame", "first_frame", "prompt", "clip_id")
    FUNCTION = "pick_clip"
    CATEGORY = "MiniMaxH3/ClipBin"


    def pick_clip(
        self,
        project_name: str = "Default_Project",
        mode: str = "Auto (首段全新 / 后续自动接力)",
        filter_rating: str = "All (1-5 ⭐)",
        clip_selection: str = "latest",
        custom_clip_path: str = "",
        view_mode: str = "Deck (卡片流)",
        **kwargs
    ) -> Dict[str, Any]:
        p_name = (project_name or "Default_Project").strip()
        custom_p = (custom_clip_path or "").strip().strip('"').strip("'")

        if custom_p and os.path.isdir(custom_p):
            target_clip_dir = custom_p
            p_name = os.path.basename(os.path.dirname(custom_p)) or p_name
            target_clip_id = os.path.basename(custom_p)
        else:
            idx = load_project_index(p_name)
            clips = idx.get("clips", [])

            # Check if Initial Mode applies (Auto with empty bin, or Force Initial)
            is_initial_mode = mode.startswith("Force Initial") or (mode.startswith("Auto") and len(clips) == 0)

            if is_initial_mode:
                logger.info("[Clip Bin Picker] Operating in Initial Generation mode for project '%s' (Zero prior context).", p_name)
                card = create_placeholder_card("✨ Initial Clip Mode", f"Project: {p_name} | Ready for First Clip (No Context)")
                placeholder_tensor = pil_to_tensor(card)
                return {
                    "ui": {"images": []},
                    "result": (None, placeholder_tensor, placeholder_tensor, "", "[INITIAL_GENERATION]")
                }

            if not clips:
                raise ValueError(f"MiniMaxClipBinPicker: No clips found in project '{p_name}'. "
                                 f"Switch mode to 'Auto' to generate the first clip.")

            # Parse star rating filter
            min_stars = 1
            if filter_rating.startswith("⭐⭐⭐⭐⭐"):
                min_stars = 5
            elif filter_rating.startswith("⭐⭐⭐⭐"):
                min_stars = 4
            elif filter_rating.startswith("⭐⭐⭐"):
                min_stars = 3

            filtered = [c for c in clips if c.get("rating", 3) >= min_stars]
            if not filtered:
                logger.warning("[Clip Bin Picker] No clips match rating >= %s in '%s', falling back to all clips.",
                               min_stars, p_name)
                filtered = clips

            sel = (clip_selection or "latest").strip()
            if sel.lower() in ("latest", "", "0", "auto", "default"):
                target_clip = filtered[0]
                target_clip_id = target_clip["clip_id"]
            else:
                # Substring / exact match
                matched = [c for c in clips if sel in c.get("clip_id", "") or sel in c.get("shot_tag", "")]
                if matched:
                    target_clip_id = matched[0]["clip_id"]
                else:
                    target_clip_id = sel

        video, audio, tail_tensor, first_tensor, meta_dict = load_clip_asset(p_name, target_clip_id)
        out_latent = pack_av_latent(video, audio)

        prompt_str = meta_dict.get("prompt", "")
        frames = meta_dict.get("frames", latent_steps_to_pixel_frames(video.shape[2]))
        logger.info("[Clip Bin Picker] Loaded clip '%s' (%s frames | ⭐%s | tag: '%s')",
                    target_clip_id, frames, meta_dict.get("rating", 3), meta_dict.get("shot_tag", ""))

        return {
            "ui": {"images": []},
            "result": (out_latent, tail_tensor, first_tensor, prompt_str, target_clip_id)
        }




class MiniMaxClipBinTreePickerNode(MiniMaxClipBinPickerNode):
    """Visual Tree & DAG Lineage Graph Picker for MiniMax Clip Bin.
    
    Identical pipeline compatibility with MiniMaxClipBinPickerNode, but defaults to
    an interactive branching DAG tree view for exploring non-linear story branches.
    """

    @classmethod
    def INPUT_TYPES(cls):
        types = super().INPUT_TYPES()
        import copy
        types = copy.deepcopy(types)
        types["optional"]["view_mode"] = ([
            "Tree (关系树)",
            "Deck (卡片流)"
        ], {
            "default": "Tree (关系树)",
            "tooltip": "【表示形式】🌳 系統ツリー または 🎴 カード一覧（ノード上のボタンでも切替可能）。"
        })
        return types

    CATEGORY = "MiniMaxH3/ClipBin"


class MiniMaxSafeVAEDecodeNode:
    """Safe VAE Video Decoder that gracefully handles None in Initial Clip Mode.
    
    When samples is None (e.g. initial generation with zero prior context),
    it safely returns an empty IMAGE tensor instead of crashing with TypeError.
    When samples is present, it delegates to vae.decode() with full fidelity.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "vae": ("VAE",),
            },
            "optional": {
                "samples": ("LATENT",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "decode"
    CATEGORY = "MiniMaxH3/ClipBin"

    def decode(self, vae: Any, samples: Optional[Dict[str, Any]] = None) -> Tuple[torch.Tensor]:
        if samples is None:
            logger.info("[Safe VAE Decode] No previous video latent provided (Initial Clip Mode). Passing through empty.")
            return (torch.empty((0, 768, 1344, 3), dtype=torch.float32),)

        v, _ = _unpack_latent(samples)
        if v is None:
            raw_s = samples.get("samples")
            if raw_s is None:
                return (torch.empty((0, 768, 1344, 3), dtype=torch.float32),)
            v = raw_s

        try:
            images = vae.decode(v)
            images = _standardize_image_tensor(images)
            return (images,)
        except Exception as e:
            raise RuntimeError(f"Video VAE decode failed for shape {getattr(v, 'shape', None)}") from e


class MiniMaxSafeVAEDecodeAudioNode:
    """Safe VAE Audio Decoder that gracefully handles None in Initial Clip Mode.
    
    When samples is None (e.g. initial generation with zero prior context),
    it safely returns an empty AUDIO dict instead of crashing with TypeError.
    When samples is present, it delegates to vae.decode() with full audio fidelity.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "vae": ("VAE",),
            },
            "optional": {
                "samples": ("LATENT",),
            }
        }

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "decode"
    CATEGORY = "MiniMaxH3/ClipBin"

    def decode(self, vae: Any, samples: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Dict[str, Any]]]:
        if samples is None:
            logger.info("[Safe VAE Decode Audio] No previous audio latent provided (Initial Clip Mode). Passing through None.")
            return (None,)

        _, a = _unpack_latent(samples)
        if a is None:
            # Check if samples itself is an audio latent or dictionary
            raw_s = samples.get("samples")
            if raw_s is not None and hasattr(raw_s, "ndim") and raw_s.ndim <= 4:
                a = raw_s
            else:
                logger.info("[Safe VAE Decode Audio] No audio stream found in latent. Passing through None.")
                return (None,)

        try:
            audio = vae.decode(a)
            audio = _standardize_audio_dict(audio)
            return (audio,)
        except Exception as e:
            raise RuntimeError(f"Audio VAE decode failed for shape {getattr(a, 'shape', None)}") from e


class MiniMaxVideoChunkSlicerNode:
    """Intelligent frame & audio slicer for long video editing (>15s).

    Supports:
    1. Direct on-demand video file streaming (Lazy Chunk Decoding) - Zero RAM/VRAM explosion!
    2. Optional connected images Tensor (100% backward compatible).
    3. Built-in target resolution scaling and force_fps locking.
    4. Emits slice_context so downstream reassembler seamlessly replaces original frames.
    """
    @classmethod
    def INPUT_TYPES(cls):
        video_files = get_input_video_files()
        return {
            "required": {
                "video_file": (video_files,),
                "project_name": ("STRING", {"default": "Video_Edit_Project"}),
                "chunk_length": ("INT", {"default": 124, "min": 16, "max": 2048, "step": 1}),
                "chunk_index": ("INT", {"default": 0, "min": 0, "max": 9999, "step": 1}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 7680, "step": 8}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 4320, "step": 8}),
                "force_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0, "step": 0.01}),
                "slice_mode": ([
                    "Auto Chunk Grid (网格切分)",
                    "Custom Range (自由区间)",
                    "Next Unedited (自动下一未编辑段)",
                ],),
                "custom_start_frame": ("INT", {"default": 0, "min": 0, "max": 999999, "step": 1}),
                "custom_end_frame": ("INT", {"default": 124, "min": 1, "max": 999999, "step": 1}),
                "auto_advance": ([
                    "None (手动控制)",
                    "Next Chunk (顺序下一段)",
                    "Next Unedited (跳至下一未编辑)",
                ], {"default": "None (手动控制)"}),
                "prev_ref_frames_count": ("INT", {"default": 16, "min": 1, "max": 128, "step": 1}),
                "first_chunk_ref_mode": ([
                    "Current Chunk First Frame (当前片段首帧)",
                    "Black / Zero Frame (全黑空帧)",
                ], {"default": "Current Chunk First Frame (当前片段首帧)"}),
            },
            "optional": {
                "images": ("IMAGE",),
                "audio": ("AUDIO",),
                "fps": ("FLOAT", {"forceInput": True}),
                "optional_first_frame_ref": ("IMAGE",),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        # Allow backward-compatibility with saved workflows that had shifted widget indices
        return True

    RETURN_TYPES = ("IMAGE", "AUDIO", "SLICE_CONTEXT", "VHS_VIDEOINFO", "IMAGE", "FLOAT", "INT", "STRING", "IMAGE", "IMAGE")
    RETURN_NAMES = (
        "chunk_images",
        "chunk_audio",
        "slice_context",
        "video_info",
        "timeline_preview",
        "fps",
        "frame_count",
        "slice_info",
        "prev_last_frame",
        "prev_ref_frames",
    )
    FUNCTION = "slice_chunk"
    CATEGORY = "MiniMaxH3/VideoEdit"

    def slice_chunk(
        self,
        video_file: str,
        project_name: str,
        chunk_length: int = 124,
        chunk_index: int = 0,
        target_width: int = 0,
        target_height: int = 0,
        force_fps: float = 24.0,
        slice_mode: str = "Auto Chunk Grid (网格切分)",
        custom_start_frame: int = 0,
        custom_end_frame: int = 124,
        auto_advance: Any = "None (手动控制)",
        prev_ref_frames_count: int = 16,
        first_chunk_ref_mode: str = "Current Chunk First Frame (当前片段首帧)",
        images: Optional[torch.Tensor] = None,
        audio: Optional[Dict[str, Any]] = None,
        fps: Optional[Any] = None,
        optional_first_frame_ref: Optional[torch.Tensor] = None,
        **kwargs,
    ):
        # Sanitize auto_advance (gracefully handle legacy workflows where 24 was stored)
        valid_modes = [
            "None (手动控制)",
            "Next Chunk (顺序下一段)",
            "Next Unedited (跳至下一未编辑)",
        ]
        if not isinstance(auto_advance, str) or auto_advance not in valid_modes:
            auto_advance = "None (手动控制)"

        try:
            prev_ref_frames_count = max(1, int(prev_ref_frames_count))
        except Exception:
            prev_ref_frames_count = 16

        valid_first_modes = [
            "Current Chunk First Frame (当前片段首帧)",
            "Black / Zero Frame (全黑空帧)",
        ]
        if not isinstance(first_chunk_ref_mode, str) or first_chunk_ref_mode not in valid_first_modes:
            first_chunk_ref_mode = "Current Chunk First Frame (当前片段首帧)"

        # Robust FPS resolution
        effective_fps = 24.0
        try:
            if fps is not None and str(fps).strip() != "" and float(fps) > 0:
                effective_fps = float(fps)
            elif force_fps is not None and float(force_fps) > 0:
                effective_fps = float(force_fps)
        except (ValueError, TypeError):
            try:
                effective_fps = float(force_fps) if force_fps else 24.0
            except Exception:
                effective_fps = 24.0
        chunk_imgs, chunk_aud, ctx, info, prev_last_frame, prev_ref_frames = slice_video_and_audio(
            project_name=project_name,
            video_file=video_file,
            images=images,
            audio=audio,
            fps=effective_fps,
            chunk_length=chunk_length,
            chunk_index=chunk_index,
            slice_mode=slice_mode,
            custom_start_frame=custom_start_frame,
            custom_end_frame=custom_end_frame,
            target_width=target_width,
            target_height=target_height,
            prev_ref_frames_count=prev_ref_frames_count,
            first_chunk_ref_mode=first_chunk_ref_mode,
            optional_first_frame_ref=optional_first_frame_ref,
            return_ref_frames=True,
        )
        ctx["auto_advance"] = auto_advance
        session = get_or_create_timeline_session(project_name)
        preview_card = render_timeline_indicator_image(
            session=session,
            active_chunk_idx=ctx["chunk_index"],
            active_start=ctx["start_frame"],
            active_end=ctx["end_frame"],
        )

        # Construct standard VHS_VIDEOINFO compatible dictionary for downstream nodes (e.g. VHS_VideoInfo)
        total_f = session.meta.get("total_frames", chunk_imgs.shape[0])
        src_w = session.meta.get("width", chunk_imgs.shape[2])
        src_h = session.meta.get("height", chunk_imgs.shape[1])
        src_fps = float(session.meta.get("fps", effective_fps))
        cur_f = int(chunk_imgs.shape[0])
        cur_w = int(chunk_imgs.shape[2])
        cur_h = int(chunk_imgs.shape[1])

        video_info = {
            "source_fps": src_fps,
            "source_frame_count": total_f,
            "source_duration": round(total_f / src_fps, 4) if src_fps > 0 else 0.0,
            "source_width": src_w,
            "source_height": src_h,
            "loaded_fps": float(effective_fps),
            "loaded_frame_count": cur_f,
            "loaded_duration": round(cur_f / effective_fps, 4) if effective_fps > 0 else 0.0,
            "loaded_width": cur_w,
            "loaded_height": cur_h,
        }

        return (
            chunk_imgs,
            chunk_aud,
            ctx,
            video_info,
            preview_card,
            float(effective_fps),
            cur_f,
            info,
            prev_last_frame,
            prev_ref_frames,
        )


class MiniMaxVideoPatchReassemblerNode:
    """Seamless in-place video patch reassembler & timeline progress tracker.

    Takes generated/edited clip and automatically replaces original frames in master video.
    Detects unedited gaps, supports seam micro-blending, and exports assembled long video.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "edited_images": ("IMAGE",),
                "slice_context": ("SLICE_CONTEXT",),
                "seam_blend_frames": ("INT", {"default": 2, "min": 0, "max": 16, "step": 1}),
                "output_mode": ([
                    "Full Assembled Video (完整拼装长视频)",
                    "Current Patch Only (仅当前片段)",
                ],),
                "save_to_session": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "edited_audio": ("AUDIO",),
            }
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "BOOLEAN", "STRING")
    RETURN_NAMES = ("output_images", "output_audio", "is_all_completed", "timeline_summary")
    FUNCTION = "reassemble_patch"
    CATEGORY = "MiniMaxH3/VideoEdit"

    def reassemble_patch(
        self,
        edited_images: torch.Tensor,
        slice_context: Dict[str, Any],
        seam_blend_frames: int = 2,
        output_mode: str = "Full Assembled Video (完整拼装长视频)",
        save_to_session: bool = True,
        edited_audio: Optional[Dict[str, Any]] = None,
    ):
        project_name = slice_context.get("project_name", "Video_Edit_Project")
        chunk_index = slice_context.get("chunk_index", 0)
        start_frame = slice_context.get("start_frame", 0)
        end_frame = slice_context.get("end_frame", edited_images.shape[0])

        session = get_or_create_timeline_session(project_name)

        if save_to_session:
            session.patch_chunk(
                chunk_index=chunk_index,
                start_frame=start_frame,
                end_frame=end_frame,
                edited_images=edited_images,
                edited_audio=edited_audio,
                seam_blend_frames=seam_blend_frames,
            )

        # Decide output
        if "Current Patch Only" in output_mode:
            out_imgs = edited_images
            raw_aud = edited_audio or (session.master_audio if session.master_audio else None)
        else:
            # Output master assembled frames
            if session.master_frames is not None:
                out_imgs = session.master_frames
            else:
                out_imgs = edited_images

            raw_aud = session.master_audio

        fps = float(session.meta.get("fps", 24.0))
        dur_samples = int(round(out_imgs.shape[0] / fps * 44100))
        out_aud = standardize_audio_dict(raw_aud, default_sr=44100, fallback_samples=dur_samples)

        is_all_done = bool(session.meta.get("is_fully_assembled", False))
        cov_pct = session.meta.get("coverage_ratio", 0.0) * 100
        total_frames = session.meta.get("total_frames", out_imgs.shape[0])
        fps = session.meta.get("fps", 24.0)
        gaps = session.meta.get("gaps", [])

        if is_all_done:
            status_text = (
                f"✅ [全クリップ統合完了 100%] プロジェクト '{project_name}' の全 {total_frames} フレーム "
                f"({total_frames/fps:.2f}秒) が差し替え・統合されました！"
            )
        else:
            gap_desc = ", ".join(f"#{g['chunk_index']}({g['start_frame']}~{g['end_frame']}F)" for g in gaps[:3])
            if len(gaps) > 3:
                gap_desc += f"...計{len(gaps)}箇所"
            status_text = (
                f"⚡ [統合進捗: {cov_pct:.1f}%] プロジェクト '{project_name}' の クリップ #{chunk_index} を正常に統合しました！"
                f"\n⚠️ 未生成またはスキップされたクリップ: {gap_desc}"
            )

        return (out_imgs, out_aud, is_all_done, status_text)


class MiniMaxEasyVideoSettingsNode:
    PRESETS = {
        "Auto: Match Input Image (0.25 MP / Test)": (0, 0),
        "Auto: Match Input Image (0.6 MP / Standard)": (0, 0),
        "Auto: Match Input Image (1.0 MP / High)": (0, 0),
        "512x512 / Test square": (512, 512),
        "640x384 / Test landscape": (640, 384),
        "384x640 / Test portrait": (384, 640),
        "960x544 / Turbo example": (960, 544),
        "544x960 / Portrait": (544, 960),
        "768x768 / H3 square": (768, 768),
        "1344x768 / H3 native landscape": (1344, 768),
        "768x1344 / H3 native portrait": (768, 1344),
    }

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "resolution": (list(cls.PRESETS) + ["Manual"], {"default": "Auto: Match Input Image (0.6 MP / Standard)"}),
            "manual_width": ("INT", {"default": 512, "min": 32, "max": 8192, "step": 32}),
            "manual_height": ("INT", {"default": 512, "min": 32, "max": 8192, "step": 32}),
            "seconds_to_add": ("FLOAT", {"default": 3.0, "min": 0.25, "max": 120.0, "step": 0.25,
                "tooltip": "今回追加する秒数。継続用の重複は自動加算。実際の尺は17k+5に丸めた近似値。"}),
        }, "optional": {
            "image": ("IMAGE",),
            "context_latent": ("LATENT",),
            "cache_config": ("MINIMAX_CACHE_CONFIG",)
        }}

    RETURN_TYPES = ("INT", "INT", "INT", "STRING")
    RETURN_NAMES = ("width", "height", "length", "summary")
    FUNCTION = "calculate"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def calculate(self, resolution, manual_width, manual_height, seconds_to_add,
                  image=None, context_latent=None, cache_config=None):
        import math
        auto_info = ""
        if resolution.startswith("Auto"):
            if image is not None and hasattr(image, "shape") and len(image.shape) >= 3:
                orig_h = int(image.shape[1])
                orig_w = int(image.shape[2])
                aspect_ratio = orig_w / max(1, orig_h)

                if "0.25 MP" in resolution:
                    target_pixels = 512 * 512
                elif "1.0 MP" in resolution:
                    target_pixels = 1024 * 1024
                else:
                    target_pixels = 768 * 768

                calc_w = math.sqrt(target_pixels * aspect_ratio)
                calc_h = math.sqrt(target_pixels / aspect_ratio)

                width = max(32, int(round(calc_w / 32)) * 32)
                height = max(32, int(round(calc_h / 32)) * 32)
                auto_info = f"【画像連動 {orig_w}×{orig_h} (比率 {orig_w/orig_h:.2f}:1 ➔ 32倍数へ調整)】"
            else:
                width, height = (512, 512) if "0.25 MP" in resolution else (1024, 1024) if "1.0 MP" in resolution else (768, 768)
                auto_info = "【画像未接続：標準フォールバック】"
        elif resolution == "Manual":
            width, height = manual_width, manual_height
        else:
            width, height = self.PRESETS[resolution]

        width = max(32, int(round(width / 32)) * 32)
        height = max(32, int(round(height / 32)) * 32)
        video, audio = _unpack_latent(context_latent)
        overlap = 0
        cfg = cache_config or KVCacheConfig()
        if video is not None and audio is not None and cfg.is_native_masked_av_mode():
            available = latent_steps_to_pixel_frames(video.shape[2])
            cap = min(cfg.rolling_frames, available)
            if cap >= 39:
                overlap = 39 + ((cap - 39) // 51) * 51
                # A continuation must use the saved latent's spatial grid.
                width, height = int(video.shape[4]) * 16, int(video.shape[3]) * 16
        requested = max(5, float(seconds_to_add) * 24 + overlap)
        length = 5 + max(0, int((requested - 5) / 17 + 0.5)) * 17
        while length <= overlap:
            length += 17
        added = length - overlap
        summary = (f"{width} × {height} / 24 fps | "
                   f"{auto_info + ' ' if auto_info else ''}"
                   f"{'継続（前クリップの解像度）' if overlap else '新規'}\n"
                   f"生成 {length}f − 重複 {overlap}f = 追加 {added}f / {added / 24:.3f}秒")
        return {"ui": {"text": [summary]}, "result": (width, height, length, summary)}


class MiniMaxVideoFrameRateNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "source_frame_count": ("INT", {"default": 73, "min": 1}),
            "source_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0}),
            "interpolation_multiplier": ("INT", {"default": 5, "min": 1, "max": 16}),
            "target_fps": ("FLOAT", {"default": 60.0, "min": 1.0, "max": 120.0}),
        }}

    RETURN_TYPES = ("IMAGE", "FLOAT")
    RETURN_NAMES = ("images", "fps")
    FUNCTION = "resample"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def resample(self, images, source_frame_count, source_fps, interpolation_multiplier, target_fps):
        count = max(1, round(source_frame_count / source_fps * target_fps))
        indices = [min(len(images) - 1, round(i * source_fps * interpolation_multiplier / target_fps))
                   for i in range(count)]
        return (images[indices], float(target_fps))


class MiniMaxVideoPathResolverNode:
    """Intelligently resolves target video path for Stage 4 enhancement.
    Prioritizes:
    1. If use_lms_if_available is True and lms_video was executed and exists -> use lms_video
    2. If joined_video was executed and exists -> use joined_video
    3. If neither was executed (e.g. running Stage 4 alone) -> auto-resolve latest exported video in project bin
    4. Auto-resolve latest combined video in output/H3_Easy_v5/
    5. Manual fallback path
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_name": ("STRING", {"default": "H3_SilverCap_1MP_Test"}),
                "use_lms_if_available": ("BOOLEAN", {"default": False}),
                "fallback_path": ("STRING", {"default": "", "multiline": False}),
            },
            "optional": {
                "joined_video": ("STRING", {"forceInput": True}),
                "lms_video": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("video_path",)
    FUNCTION = "resolve"
    CATEGORY = "MiniMaxH3/PrefixStream"

    def resolve(self, project_name: str, use_lms_if_available: bool = False, fallback_path: str = "", joined_video: str = "", lms_video: str = "") -> Tuple[str]:
        # 1. LMS video if selected and valid
        if use_lms_if_available and lms_video and isinstance(lms_video, str):
            p = lms_video.strip()
            if p and os.path.exists(p):
                logger.info(f"[VideoPathResolver] Using upstream LMS video: {p}")
                return (p,)

        # 2. Joined video if valid
        if joined_video and isinstance(joined_video, str):
            p = joined_video.strip()
            if p and os.path.exists(p):
                logger.info(f"[VideoPathResolver] Using upstream joined video: {p}")
                return (p,)

        # 3. Check latest export from project_name bin
        import folder_paths
        output_dir = folder_paths.get_output_directory()
        bins_project_dir = os.path.join(output_dir, "minimax_h3_bins", project_name.strip())
        if os.path.exists(bins_project_dir):
            exports = []
            for item in os.listdir(bins_project_dir):
                if item.startswith("export_"):
                    v = os.path.join(bins_project_dir, item, "selected_video.mp4")
                    if os.path.exists(v):
                        exports.append((os.path.getmtime(v), v))
            if exports:
                exports.sort(key=lambda x: x[0], reverse=True)
                resolved = exports[0][1]
                logger.info(f"[VideoPathResolver] Auto-resolved latest project export for '{project_name}': {resolved}")
                return (resolved,)

        # 4. Check latest combined in output/H3_Easy_v5/
        easy_dir = os.path.join(output_dir, "H3_Easy_v5")
        if os.path.exists(easy_dir):
            combined_candidates = []
            for f in os.listdir(easy_dir):
                if f.endswith(".mp4") and ("Combined" in f or "selected" in f or "01_" in f):
                    fp = os.path.join(easy_dir, f)
                    combined_candidates.append((os.path.getmtime(fp), fp))
            if combined_candidates:
                combined_candidates.sort(key=lambda x: x[0], reverse=True)
                resolved = combined_candidates[0][1]
                logger.info(f"[VideoPathResolver] Auto-resolved latest combined video in H3_Easy_v5: {resolved}")
                return (resolved,)

        # 5. Fallback path
        if fallback_path and os.path.exists(fallback_path.strip()):
            return (fallback_path.strip(),)

        raise ValueError(f"[MiniMaxVideoPathResolver] No valid video found for project '{project_name}'! Please run Stage 2 first or provide a valid fallback_path.")


class MiniMaxImageUpscaleBatchedNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "upscale_model": ("UPSCALE_MODEL",),
                "images": ("IMAGE",),
                "per_batch": ("INT", {"default": 16, "min": 1, "max": 4096, "step": 1}),
            },
            "optional": {
                "downscale_ratio": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.01}),
                "downscale_method": (["nearest-exact", "bilinear", "area", "bicubic", "lanczos"], {"default": "lanczos"}),
                "precision": (["float32", "float16", "bfloat16"], {"default": "float16"}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "upscale"
    CATEGORY = "MiniMaxH3/Enhancement"
    DESCRIPTION = "Memory-safe batch model upscaling with native float16 and automatic disk memmap for long videos (>4GB) to prevent RAM OOM."

    def upscale(self, upscale_model, images, per_batch, downscale_ratio=1.0, downscale_method="lanczos", precision="float16"):
        import numpy as np
        import folder_paths
        import uuid
        from comfy import model_management
        from comfy.utils import ProgressBar, common_upscale

        dtype = torch.float16 if precision == "float16" else torch.bfloat16 if precision == "bfloat16" else torch.float32
        device = model_management.get_torch_device()
        upscale_model.to(device, dtype=dtype)

        steps = images.shape[0]
        if steps == 0:
            return (images,)

        pbar = ProgressBar(steps)
        out_tensor = None

        for start_idx in range(0, steps, per_batch):
            batch_slice = images[start_idx:start_idx+per_batch].movedim(-1, -3).to(device=device, dtype=dtype)
            sub_images = upscale_model(batch_slice)
            if downscale_ratio < 1.0:
                new_height = int(sub_images.shape[2] * downscale_ratio)
                new_width = int(sub_images.shape[3] * downscale_ratio)
                if sub_images.dtype == torch.bfloat16:
                    sub_images = sub_images.to(torch.float32)
                sub_images = common_upscale(sub_images, new_width, new_height, downscale_method, "disabled")

            sub_images = sub_images.movedim(1, -1).to(device="cpu", dtype=torch.float16)

            if out_tensor is None:
                total_bytes = steps * sub_images.shape[1] * sub_images.shape[2] * sub_images.shape[3] * 2
                if total_bytes > 4 * 1024 * 1024 * 1024:  # > 4 GB: use disk memory-mapped tensor to prevent RAM OOM
                    temp_dir = folder_paths.get_temp_directory()
                    os.makedirs(temp_dir, exist_ok=True)
                    mmap_file = os.path.join(temp_dir, f"upscale_mmap_{uuid.uuid4().hex}.bin")
                    mmap_arr = np.memmap(mmap_file, dtype='float16', mode='w+', shape=(steps, sub_images.shape[1], sub_images.shape[2], sub_images.shape[3]))
                    out_tensor = torch.from_numpy(mmap_arr)
                    out_tensor._mmap_file = mmap_file
                else:
                    out_tensor = torch.empty((steps, sub_images.shape[1], sub_images.shape[2], sub_images.shape[3]), dtype=torch.float16, device="cpu")

            batch_count = sub_images.shape[0]
            out_tensor[start_idx:start_idx+batch_count] = sub_images
            pbar.update(batch_count)

        upscale_model.cpu()
        return (out_tensor,)


NODE_CLASS_MAPPINGS = {
    "MiniMaxImageUpscaleBatched": MiniMaxImageUpscaleBatchedNode,
    "MiniMaxVideoPathResolver": MiniMaxVideoPathResolverNode,
    "MiniMaxSelectedClipExport": MiniMaxSelectedClipExportNode,
    "MiniMaxEasyVideoSettings": MiniMaxEasyVideoSettingsNode,
    "MiniMaxVideoFrameRate": MiniMaxVideoFrameRateNode,
    "MiniMaxDiskVideoStream": MiniMaxDiskVideoStreamNode,
    "MiniMaxPrefixCacheConfig": MiniMaxPrefixCacheConfigNode,
    "MiniMaxPrefixCacheApplier": MiniMaxPrefixCacheApplierNode,
    "MiniMaxTrimPrefix": MiniMaxTrimPrefixLatentNode,
    "MiniMaxTrimPrefixLatent": MiniMaxTrimPrefixLatentNode,
    "MiniMaxLongVideoStitcher": MiniMaxLongVideoStitcherNode,
    "MiniMaxCacheMonitor": MiniMaxCacheMonitorNode,
    "MiniMaxSaveLatent": MiniMaxSaveLatentNode,
    "MiniMaxLoadLatent": MiniMaxLoadLatentNode,
    "MiniMaxClipBinSaver": MiniMaxClipBinSaverNode,
    "MiniMaxClipBinPicker": MiniMaxClipBinPickerNode,
    "MiniMaxClipBinTreePicker": MiniMaxClipBinTreePickerNode,
    "MiniMaxSafeVAEDecode": MiniMaxSafeVAEDecodeNode,
    "MiniMaxSafeVAEDecodeAudio": MiniMaxSafeVAEDecodeAudioNode,
    "MiniMaxVideoChunkSlicer": MiniMaxVideoChunkSlicerNode,
    "MiniMaxVideoPatchReassembler": MiniMaxVideoPatchReassemblerNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxImageUpscaleBatched": "H3 2倍超解像（高速・メモリ安全版）",
    "MiniMaxVideoPathResolver": "H3 動画パス自動解決 (Smart Path)",
    "MiniMaxSelectedClipExport": "H3 選択した分岐だけ結合",
    "MiniMaxEasyVideoSettings": "H3 解像度・追加秒数（自動計算）",
    "MiniMaxVideoFrameRate": "H3 補間後のFPS・尺調整",
    "MiniMaxDiskVideoStream": "MiniMax H3 ディスク動画ストリーム",
    "MiniMaxPrefixCacheConfig": "MiniMax H3 継続生成設定",
    "MiniMaxPrefixCacheApplier": "MiniMax H3 継続生成適用",
    "MiniMaxTrimPrefix": "MiniMax H3 先頭重複フレーム削除 (AV Master)",
    "MiniMaxTrimPrefixLatent": "MiniMax H3 先頭重複潜在削除 (AV Master)",
    "MiniMaxLongVideoStitcher": "MiniMax H3 長尺動画シームレス結合",
    "MiniMaxCacheMonitor": "MiniMax H3 キャッシュ監視モニター",
    "MiniMaxSaveLatent": "MiniMax H3 潜在データ保存 (AV Latent)",
    "MiniMaxLoadLatent": "MiniMax H3 潜在データ読込 (AV Latent)",
    "MiniMaxClipBinSaver": "MiniMax H3 クリップ保存・プール",
    "MiniMaxClipBinPicker": "MiniMax H3 クリップ選択・ギャラリー",
    "MiniMaxClipBinTreePicker": "MiniMax H3 クリップ選択・系統ツリー",
    "MiniMaxSafeVAEDecode": "MiniMax H3 安全VAEデコード (Video)",
    "MiniMaxSafeVAEDecodeAudio": "MiniMax H3 安全VAEデコード (Audio)",
    "MiniMaxVideoChunkSlicer": "🎬 MiniMax 動画スマート分割スライサー",
    "MiniMaxVideoPatchReassembler": "🧩 MiniMax 動画パーツ差し替え・統合",
}


