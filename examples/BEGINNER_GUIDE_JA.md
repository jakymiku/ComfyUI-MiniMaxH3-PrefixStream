# MiniMax H3 Easy v5 完全導入・操作ガイド

本ガイドは、ComfyUIのインストールから必要なカスタムノードの導入、モデルの配置、そして長尺AI動画（音声付き・60fps・2倍超解像）の生成手順までをまとめたマニュアルです。

---

## 目次
1. [動作環境の目安](#1-動作環境の目安)
2. [ComfyUIの導入（環境構築）](#2-comfyuiの導入環境構築)
3. [必要なカスタムノードの導入](#3-必要なカスタムノードの導入)
4. [必要なAIモデルのダウンロードと配置](#4-必要なaiモデルのダウンロードと配置)
5. [ワークフローの読み込みと基本画面](#5-ワークフローの読み込みと基本画面)
6. [実践操作手順（I2Vから完成まで）](#6-実践操作手順i2vから完成まで)
   - [ステップ1：最初の区間を生成する（0〜3秒）](#ステップ1最初の区間を生成する03秒)
   - [ステップ2：続きを生成して動画を伸ばす（継続生成）](#ステップ2続きを生成して動画を伸ばす継続生成)
   - [ステップ3：採用した区間を結合する](#ステップ3採用した区間を結合する)
   - [ステップ4：2倍超解像・60fps化（最終仕上げ）](#ステップ42倍超解像60fps化最終仕上げ)
7. [よくあるトラブルと対処法（FAQ）](#7-よくあるトラブルと対処法faq)

---

## 1. 動作環境の目安

- **OS**: Windows 10 / 11、Linux
- **GPU**: **NVIDIA製GPU VRAM 12GB以上（RTX 4000番台以降、RTX 5000番台などを推奨）**
- **システムメモリ（RAM）**: 16GB以上（32GB推奨）
- **ストレージ空き容量**: SSD 50GB以上の空き容量（モデルファイルおよびキャッシュ用）

---

## 2. ComfyUIの導入（環境構築）

ComfyUIをまだインストールしていない場合は、以下のいずれかの方法で導入します。

### 方法A：StabilityMatrixを使用する場合（初心者推奨）
設定や依存関係の管理がグラフィカルに行えるため、最も容易な導入方法です。

1. [StabilityMatrix公式サイト](https://github.com/LykosAI/StabilityMatrix/releases) からインストーラーをダウンロードして起動します。
2. 画面の指示に従い「Packages」タブから「ComfyUI」を選択してインストールします。
3. インストール完了後、Launchボタンから起動できることを確認します。

### 方法B：公式ポータブル版を使用する場合
1. [ComfyUI公式リポジトリのReleases](https://github.com/comfyanonymous/ComfyUI/releases) から `ComfyUI_windows_portable_nvidia.7z` をダウンロードします。
2. 7-Zip等で解凍し、任意のフォルダ（例: `C:\ComfyUI_windows_portable`）に配置します。
3. `run_nvidia_gpu.bat` を実行してブラウザで操作画面が開くことを確認します。

---

## 3. 必要なカスタムノードの導入

本ワークフローを動作させるための拡張機能を導入します。

> **ComfyUI-Managerについて**  
> StabilityMatrix環境や最新のComfyUI環境では、初期状態から「ComfyUI-Manager」が標準で組み込まれていることがほとんどです。ComfyUI画面のメニュー（または右下）に「Manager」というボタンがあれば、すでに導入済みですので再インストールの必要はありません。

### 1. 本リポジトリ（ComfyUI-MiniMaxH3-PrefixStream）の導入
コマンドプロンプトまたはPowerShellを開き、ComfyUIの `custom_nodes` フォルダへ移動してクローンします。

```bash
cd /d "ComfyUIのインストール先\custom_nodes"
git clone https://github.com/jakymiku/ComfyUI-MiniMaxH3-PrefixStream.git
```

### 2. その他の必須カスタムノード
ComfyUI画面上の「Manager」ボタンをクリックし、「Install Custom Nodes」から以下の名称を検索してインストールします。

| カスタムノード名 | 主な用途 |
| :--- | :--- |
| **ComfyUI-VideoHelperSuite** | 動画・音声の読み込みおよび結合書き出し |
| **ComfyUI-Impact-Pack** | モード切り替えスイッチ（ImpactSwitch） |
| **ComfyUI-Frame-Interpolation** | RIFEによる動画のフレーム補間（60fps化） |
| **rgthree-comfy** | 操作盤の工程切り替え（Fast Groups Muter） |
| **ComfyUI-Custom-Scripts** | テキスト表示・確認用ノード（ShowText） |

> **注意**  
> 新規にカスタムノードを追加した後は、必ずComfyUIを一度再起動してください。

---

## 4. 必要なAIモデルのダウンロードと配置

以下のモデルファイルをダウンロードし、ComfyUI内の指定フォルダへ配置します。

| モデル種別 | 推奨ファイル名 | 配置先フォルダ | 入手先 |
| :--- | :--- | :--- | :--- |
| **H3 Diffusion Model** | `minimax_h3_fl2va_bf16.safetensors`<br>（またはint8版） | `models/diffusion_models/` | Hugging Face (Comfy-Org / MiniMax-H3) |
| **Text Encoder** | `qwen3vl_32b_minimax_h3_bf16.safetensors`<br>（またはawq版） | `models/text_encoders/` | Hugging Face (Comfy-Org / MiniMax-H3) |
| **動画用 VAE** | `minimax_h3_video_vae_fp16.safetensors` | `models/vae/` | Hugging Face (Comfy-Org / MiniMax-H3) |
| **音声用 VAE** | `minimax_h3_audio_vae_fp32.safetensors` | `models/vae/` | Hugging Face (Comfy-Org / MiniMax-H3) |
| **超解像モデル** | `RealESRGAN_x4plus_anime_6B.pth` | `models/upscale_models/` | Hugging Face / GitHub |
| **フレーム補間モデル** | `rife426.pth` | `models/frame_interpolation/` | ComfyUI-Frame-Interpolation等 |
| **LMS LoRA（任意）** | `minimax_h3_lms_v1.0_r64.safetensors` | `models/loras/` | Hugging Face (Alissonerdx/Minimax-H3-ComfyUI) |

---

## 5. ワークフローの読み込みと基本画面

1. ComfyUIを起動し、ブラウザで画面を開きます。
2. ダウンロードした **`01_H3_Easy_v5_Release.json`**（または `Master.json`）をComfyUI画面上へ直接ドラッグ＆ドロップします。
3. 画面左上にある **「🎛️ マスター操作盤」** を確認します。

### マスター操作盤（Fast Groups Muter）の役割
本ワークフローは、ノードの配線を繋ぎ替えることなく、チェックボックスのON/OFFだけで処理工程を切り替えられる設計になっています。

- **① 区間を生成**: 3秒単位の動画クリップを生成します（撮影フェーズ）。
- **② 採用した動画を結合**: 生成した複数クリップを1本の動画に結合します。
- **③ LMS 2ndパス**: 結合動画全体にディテール強調をかけます（任意）。
- **④ 2倍超解像・60fps化**: 2倍アップスケールと60fps補間を施します（仕上げフェーズ）。

---

## 6. 実践操作手順（I2Vから完成まで）

静止画を元に動画を生成し、長尺化して高画質60fps動画に仕上げる標準的な手順です。

※画面上の各ノードには、タイトルの先頭に `01`、`03`、`06` などの番号が大きく書かれています。

### ステップ1：最初の区間を生成する（0〜3秒）
1. 一番上のマスター操作盤で **「① 区間を生成」のみをON** にします（初期状態）。
2. **【共通の作品名】ノード**（上部中央）: 任意のプロジェクト名（例: `Test_Project_01`）を入力します。別の作品を作る際は名前を変えてください。
3. **【01 モード：1=T2V / 2=I2V / 3=FL2V / 4=R2V】ノード**（左上）: `2`（I2V）になっていることを確認します。
4. **【I2V / FL2V：開始画像】ノード**（左から2列目の中段）: 動かしたい画像をドラッグ＆ドロップして読み込みます。
5. **【03 解像度・追加秒数】ノード**（左上・モードノードの下）: 初期値の `Auto: Match Input Image (0.6 MP / Standard)` のままで問題ありません。セットした画像の縦横比を自動検知し、H3の仕様である32の倍数へ自動調整されます。
6. **【06 T2V / I2V / FL2V プロンプト】ノード**（左から2列目の上段）: カメラワークと人物の動作を入力します。
   - 例:
     ```text
     For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

     integrated_multimodal_description: Camera tracking shot. The girl walks forward cheerfully along a sunny path, her dress gently swaying as she smiles toward the camera.

     overall_soundscape: light rhythmic footsteps, gentle pleasant breeze.
     ```
7. 画面右下の **「Queue Prompt（実行）」** ボタンをクリックします。数分で最初の3秒クリップが生成されます。

### ステップ2：続きを生成して動画を伸ばす（継続生成）
1. 直前のクリップの最後の姿勢から「次の3秒で何を起こすか」を考え、**【06 T2V / I2V / FL2V プロンプト】ノードを書き換えます**。
   - 例:
     ```text
     integrated_multimodal_description: Camera slowly zooms in. She pauses her walk, turns her upper body toward the viewer, and waves her right hand with a bright smile.

     overall_soundscape: soft footsteps stopping, gentle breeze.
     ```
2. 再度 **「Queue Prompt（実行）」** をクリックします。
3. 直前の動画の末尾フレームを引き継いだ継続クリップが自動生成されます。必要な尺になるまでこの工程を繰り返します。

> **失敗した区間をやり直したい場合**  
> 右側の素材箱（Clip Bin）の画面で、1つ前の正常なクリップをクリックして選択状態にし、プロンプトやシード値を変更して再実行してください。選択した地点から再分岐して生成できます。

### ステップ3：採用した区間を結合する
1. マスター操作盤で：
   - **① 区間を生成 ➔ OFF**
   - **② 採用した動画を結合 ➔ ON**
2. 「② 採用した動画を結合」エリア内にある **【採用する最後のクリップを明示的にクリック】ノード** のギャラリーで、**採用する最後のクリップ（末尾）を1回クリック**します。
3. **「Queue Prompt（実行）」** をクリックします。
4. 初回クリップから末尾までが、音ズレなく1本に繋がった動画（`selected_video.mp4`）が出力されます。

### ステップ4：2倍超解像・60fps化（最終仕上げ）
1. マスター操作盤で：
   - **② 採用した動画を結合 ➔ OFF**
   - **④ 2倍超解像・60fps化 ➔ ON**
2. **「Queue Prompt（実行）」** をクリックします。
   - 直前の結合動画が自動的に検出・読み込まれます。手動でファイルパスを指定する必要はありません。
   - 本リポジトリ独自の `MiniMaxImageUpscaleBatched` ノードにより、長尺動画でもメインメモリを圧迫することなく安全に処理されます。
3. `output/H3_Easy_v5/Enhanced_60fps_*.mp4` に、高解像度かつ滑らかな60fps動画が出力されます。

---

## 7. よくあるトラブルと対処法（FAQ）

### Q. ワークフローを読み込むとノードが赤色になります（Missing Node）
必要なカスタムノードがインストールされていない状態です。[3. 必要なカスタムノードの導入](#3-必要なカスタムノードの導入) を参照し、不足しているノードをComfyUI Manager等からインストールしてComfyUIを再起動してください。

### Q. 画像のアスペクト比と生成動画の比率が合いません
【03 解像度・追加秒数】ノードのプリセットが `Auto: Match Input Image` になっているか確認してください。手動で固定サイズを選んでいる場合は、画像と比率が異なる場合にリサイズ処理が発生します。

### Q. 継続生成時にキャラクターの顔や服装が崩れます
I2Vモードでは、AIは画像を最も重視して人物の特徴を再現します。プロンプトに「金髪で青い目で白いワンピースを着て…」のように外見描写を重ねて書くと、画像との情報乖離によりチラつきや崩れの原因になります。外見描写は省略し、動作やカメラワークの指定に絞って記述してください。

### Q. 超解像処理中にパソコンの動作が極端に重くなります
動画のフレーム数が多い場合、ディスクへの一時書き出しが発生します。正常な安全動作の範囲内ですが、一時フォルダが置かれているドライブ（Cドライブ等）の空き容量が10GB以上あることを確認してください。
