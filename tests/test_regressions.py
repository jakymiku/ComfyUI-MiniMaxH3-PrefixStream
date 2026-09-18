"""Regression coverage for storage boundaries, AV data loss and disk streaming."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nodes
from engine import clip_bin_manager as bins
from engine.clip_bin_api import update_clip_rating_api
from engine.cache_manager import KVCacheConfig
from pipeline.disk_stream import append_disk_clip, selected_clip_chain, MiniMaxSelectedClipExportNode
from pipeline.seam_protector import stitch_audio_waveforms


class AVRegressions(unittest.TestCase):
    def test_easy_seconds_accounts_for_continuation(self):
        settings = nodes.MiniMaxEasyVideoSettingsNode()
        initial = settings.calculate("512x512 / Test square", 512, 512, 3)["result"]
        self.assertEqual(initial[:3], (512, 512, 73))
        context = nodes.pack_av_latent(torch.zeros(1, 24, 22, 24, 40), torch.zeros(1, 32, 2, 122))
        continued = settings.calculate("512x512 / Test square", 512, 512, 3, context)["result"]
        self.assertEqual(continued[:3], (640, 384, 107))
        self.assertIn("68f", continued[3])

    def test_easy_seconds_caps_context_to_available_frames(self):
        context = nodes.pack_av_latent(torch.zeros(1, 24, 22, 2, 2), torch.zeros(1, 32, 2, 122))
        settings = nodes.MiniMaxEasyVideoSettingsNode()
        result = settings.calculate("Manual", 512, 512, 3, context, KVCacheConfig(rolling_frames=90))["result"]
        self.assertEqual(result[2], 107)
        for seconds in [0.25, 1, 3, 5, 10]:
            result = settings.calculate("Manual", 517, 381, seconds)["result"]
            self.assertEqual(result[0:2], (512, 384))
            self.assertEqual(result[2] % 17, 5)
            self.assertLessEqual(abs(result[2] - seconds * 24), 8.5)

    def test_interpolation_keeps_twelve_second_timeline(self):
        # RIFE emits (N - 1) * multiplier + 1, omitting a trailing frame interval.
        frames = torch.arange(1436).reshape(1436, 1, 1, 1)
        images, fps = nodes.MiniMaxVideoFrameRateNode().resample(frames, 288, 24, 5, 60)
        self.assertEqual((len(images), fps), (720, 60))
        self.assertEqual(images[0].item(), 0)
        self.assertEqual(images[-1].item(), 1435)
        self.assertEqual(images[300].item(), 600)

    def test_plain_video_batch_is_not_split_into_audio(self):
        samples = torch.zeros(3, 24, 37, 2, 2)
        video, audio = nodes._unpack_latent({"samples": samples})
        self.assertIs(video, samples)
        self.assertIsNone(audio)

    def test_initial_latent_and_audio_are_preserved(self):
        video = torch.randn(1, 24, 37, 2, 2)
        audio = torch.randn(1, 32, 2, 207)
        latent = nodes.pack_av_latent(video, audio)
        _, _, result = nodes.MiniMaxTrimPrefixLatentNode().trim(latent=latent, cache_config=KVCacheConfig())
        out_v, out_a = nodes._unpack_latent(result)
        self.assertTrue(torch.equal(video, out_v))
        self.assertTrue(torch.equal(audio, out_a))

    def test_audio_only_keeps_remaining_second(self):
        audio = {"waveform": torch.randn(1, 2, 64000), "sample_rate": 32000}
        _, out, _ = nodes.MiniMaxTrimPrefixLatentNode().trim(audio=audio, trim_frames=24)
        self.assertTrue(torch.equal(out["waveform"], audio["waveform"][..., 32000:]))

    def test_trim_uses_effective_prefix_instead_of_requested_length(self):
        latent = nodes.pack_av_latent(torch.zeros(1, 24, 37, 2, 2), torch.zeros(1, 32, 2, 207))
        latent.update(noise_mask="mask", minimax_prefix_frames=39)
        images, _, result = nodes.MiniMaxTrimPrefixLatentNode().trim(
            images=torch.zeros(124, 2, 2, 3), latent=latent, cache_config=KVCacheConfig(rolling_frames=90))
        self.assertEqual(images.shape[0], 85)
        self.assertEqual(nodes._unpack_latent(result)[0].shape[2], 25)

    def test_trim_entire_latent_and_discard_obsolete_mask(self):
        latent = nodes.pack_av_latent(torch.zeros(1, 24, 7, 2, 2), torch.zeros(1, 32, 2, 10))
        latent["noise_mask"] = "old mask"
        _, _, result = nodes.MiniMaxTrimPrefixLatentNode().trim(latent=latent, trim_frames=39)
        video, audio = nodes._unpack_latent(result)
        self.assertEqual(video.shape[2], 0)
        self.assertEqual(audio.shape[-1], 0)
        self.assertNotIn("noise_mask", result)

    def test_audio_mismatch_is_explicit(self):
        prev = {"waveform": torch.zeros(1, 2, 32000), "sample_rate": 32000}
        curr = {"waveform": torch.zeros(1, 2, 48000), "sample_rate": 48000}
        with self.assertRaisesRegex(ValueError, "sample rates"):
            stitch_audio_waveforms(prev, curr, 24)
        curr = {"waveform": torch.zeros(1, 1, 32000), "sample_rate": 32000}
        with self.assertRaisesRegex(ValueError, "channel"):
            stitch_audio_waveforms(prev, curr, 24)

    def test_audio_dtype_and_short_overlap(self):
        prev = {"waveform": torch.ones(1, 2, 32000, dtype=torch.float64), "sample_rate": 32000}
        curr = {"waveform": torch.ones(1, 2, 4), "sample_rate": 32000}
        out = stitch_audio_waveforms(prev, curr, 48, trim_frames=24)
        self.assertEqual(out["waveform"].shape[-1], 64000)
        self.assertEqual(out["waveform"].dtype, torch.float64)

    def test_decode_errors_are_not_empty_successes(self):
        vae = types.SimpleNamespace(decode=lambda _: (_ for _ in ()).throw(ValueError("bad weights")))
        with self.assertRaisesRegex(RuntimeError, "Video VAE"):
            nodes.MiniMaxSafeVAEDecodeNode().decode(vae, {"samples": torch.zeros(1, 24, 7, 2, 2)})
        with self.assertRaisesRegex(RuntimeError, "Audio VAE"):
            nodes.MiniMaxSafeVAEDecodeAudioNode().decode(vae, {"samples": torch.zeros(1, 32, 2, 40)})


class StorageRegressions(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_encoder_preserves_all_frames_with_short_or_rounded_audio(self):
        images = torch.zeros(73, 16, 16, 3)
        for samples in (32000, round(73 / 24 * 32000), 128000):
            with self.subTest(samples=samples):
                output = os.path.join(self.temp.name, f"audio_{samples}.mkv")
                audio = {"waveform": torch.zeros(1, 2, samples), "sample_rate": 32000}
                self.assertTrue(bins.encode_images_to_mp4(images, output, 24, audio))
                probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-count_frames",
                                        "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
                                        "-of", "json", output], capture_output=True, text=True, check=True)
                self.assertEqual(int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"]), 73)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="minimax_regression_")
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_output_directory = lambda: self.temp.name
        self.modules = patch.dict(sys.modules, {"folder_paths": folder_paths})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()
        self.temp.cleanup()

    @unittest.skipUnless(shutil.which("ffprobe"), "FFprobe required")
    def test_export_includes_selected_branch_not_rejected_sibling(self):
        for clip_id, parent, color in [('clip_root', None, 0), ('clip_rejected', 'clip_root', 1), ('clip_selected', 'clip_root', 2)]:
            directory = Path(bins.get_clip_dir('Project', clip_id))
            directory.mkdir()
            bins.atomic_write_json(str(directory/'meta.json'), {'parent_clip_id':parent,'video_file':'video.mp4'})
            images = torch.zeros(5, 16, 16, 3)
            images[...,color] = 1
            self.assertTrue(bins.encode_images_to_mp4(images, str(directory/'video.mp4'), 24))
        self.assertEqual([c[0] for c in selected_clip_chain('Project','clip_selected')], ['clip_root','clip_selected'])
        path, ids = MiniMaxSelectedClipExportNode().export_selected('Project','clip_selected')
        self.assertNotIn('rejected', ids)
        probe = json.loads(subprocess.check_output([shutil.which('ffprobe'),'-v','error','-count_frames','-show_streams','-of','json',path]))
        video = next(s for s in probe['streams'] if s['codec_type']=='video')
        self.assertEqual(int(video['nb_read_frames']),10)
        directory = Path(bins.get_clip_dir('Project','clip_root'))
        bins.atomic_write_json(str(directory/'meta.json'), {'parent_clip_id':'clip_selected','video_file':'video.mp4'})
        with self.assertRaisesRegex(ValueError,'cycle'):
            selected_clip_chain('Project','clip_selected')

    def test_path_escape_rejected_without_deleting(self):
        outside = Path(self.temp.name, "outside")
        outside.mkdir()
        for clip_id in ("..", "../outside", "..\\outside", str(outside.resolve())):
            with self.subTest(clip_id=clip_id), patch.object(bins.shutil, "rmtree") as delete:
                with self.assertRaises(ValueError):
                    bins.delete_clip_asset("Project", clip_id)
                delete.assert_not_called()
        self.assertTrue(outside.is_dir())

    def test_linked_clip_rejected(self):
        project = bins.get_project_dir("Project")
        path = os.path.join(project, "clip_valid")
        realpath = os.path.realpath
        with patch.object(bins.os.path, "realpath", side_effect=lambda p: self.temp.name if p == path else realpath(p)):
            with self.assertRaisesRegex(ValueError, "Linked"):
                bins.get_clip_dir("Project", "clip_valid")

    def test_atomic_failure_preserves_old_index_and_raises(self):
        bins.save_project_index("Project", {"clips": [], "revision": 1})
        with patch.object(bins.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                bins.save_project_index("Project", {"clips": [], "revision": 2})
        self.assertEqual(bins.load_project_index("Project")["revision"], 1)
        self.assertFalse(list(Path(bins.get_project_dir("Project")).glob(".json_*")))

    def test_failed_delete_does_not_remove_index_entry(self):
        path = Path(bins.get_clip_dir("Project", "clip_valid"))
        path.mkdir()
        bins.save_project_index("Project", {"clips": [{"clip_id": "clip_valid"}]})
        with patch.object(bins.shutil, "rmtree", side_effect=PermissionError("locked")):
            self.assertFalse(bins.delete_clip_asset("Project", "clip_valid"))
        self.assertEqual(len(bins.load_project_index("Project")["clips"]), 1)

    def test_parallel_savers_and_rating_keep_all_assets(self):
        video = torch.zeros(1, 24, 2, 2, 2)
        image = torch.zeros(1, 8, 8, 3)
        def save(i):
            meta, _, _ = bins.save_clip_asset(video, None, image, "Project", shot_tag=str(i), save_video=False)
            self.assertTrue(update_clip_rating_api("Project", meta.clip_id, 5))
            return meta.clip_id
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(save, range(8)))
        clips = bins.load_project_index("Project")["clips"]
        self.assertEqual({c["clip_id"] for c in clips}, set(ids))
        self.assertTrue(all(c["rating"] == 5 for c in clips))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_disk_stream_appends_exports_and_checks_geometry(self):
        images = torch.zeros(12, 16, 16, 4)
        audio = {"waveform": torch.zeros(1, 2, 16000), "sample_rate": 32000}
        append_disk_clip("Project", "stream", images, audio)
        manifest, _, total = append_disk_clip("Project", "stream", images, audio, trim_frames=6)
        self.assertEqual(total, 18)
        _, output, _ = append_disk_clip("Project", "stream", None, export=True)
        probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-count_frames", "-show_streams",
                                "-of", "json", output], capture_output=True, text=True, check=True)
        streams = json.loads(probe.stdout)["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        self.assertEqual(int(video["nb_read_frames"]), 18)
        self.assertTrue(any(s["codec_type"] == "audio" for s in streams))
        with self.assertRaisesRegex(ValueError, "consistent"):
            append_disk_clip("Project", "stream", torch.zeros(12, 32, 16, 3), audio)
        self.assertEqual(json.loads(Path(manifest).read_text())["total_frames"], 18)


if __name__ == "__main__":
    unittest.main()
