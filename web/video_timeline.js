import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { t, getLocale } from "./i18n.js";

/**
 * MiniMax H3 Video Timeline & Smart Chunk Slicer - High Performance & Clean UI Edition.
 *
 * Solves:
 * 1. Double yellow box confusion: Removed overlapping rangeOverlay layer; single clear chunk highlight.
 * 2. Canvas stutter/lag: Removed heavy setDirtyCanvas and global mousemove listeners, added seek-locks.
 * 3. Instant video preview: Smooth local playback with frame-stepping and loop-chunk capabilities.
 * 4. Multi-language (i18n) support for Japanese, English, and Chinese.
 */

// Inject CSS stylesheet
const styleId = "minimax-video-timeline-styles";
if (!document.getElementById(styleId)) {
    const link = document.createElement("link");
    link.id = styleId;
    link.rel = "stylesheet";
    link.type = "text/css";
    link.href = new URL("./video_timeline.css", import.meta.url).href;
    document.head.appendChild(link);
}

function formatTime(seconds) {
    const s = Math.max(0, seconds || 0);
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    const ms = Math.floor((s % 1) * 10);
    return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}.${ms}`;
}

app.registerExtension({
    name: "MiniMaxH3.VideoTimeline",

    async beforeRegisterNodeDef(nodeType, nodeData, appInstance) {
        if (nodeData.name === "MiniMaxVideoChunkSlicer" || nodeData.name === "MiniMaxVideoPatchReassembler") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
                setupTimelineWidget(this, nodeData.name);
                return r;
            };

            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
                // Auto-heal legacy widget values (e.g. if 24 was saved for auto_advance)
                const autoAdv = this.widgets?.find(w => w.name === "auto_advance");
                if (autoAdv) {
                    if (typeof autoAdv.value === "number" || (autoAdv.options?.values && !autoAdv.options.values.includes(autoAdv.value))) {
                        autoAdv.value = "None (手动控制)";
                    }
                }
                if (this._probeVideoImmediate) {
                    this._probeVideoImmediate();
                }
                return r;
            };

            const onConnectionsChange = nodeType.prototype.onConnectionsChange;
            nodeType.prototype.onConnectionsChange = function () {
                const r = onConnectionsChange ? onConnectionsChange.apply(this, arguments) : undefined;
                if (this._refreshTimeline) {
                    this._refreshTimeline();
                }
                return r;
            };

            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                const r = onExecuted ? onExecuted.apply(this, arguments) : undefined;
                if (this._onExecutedHook) {
                    this._onExecutedHook(message);
                }
                if (this._refreshTimeline) {
                    this._refreshTimeline();
                }
                return r;
            };
        }
    }
});

function setupTimelineWidget(node, nodeTypeName) {
    // Singleton guard to prevent duplicate widgets and memory leaks
    if (node._timelineInitialized) return;
    node._timelineInitialized = true;

    // Auto-heal legacy widget values
    const autoAdvWidget = node.widgets?.find(w => w.name === "auto_advance");
    if (autoAdvWidget) {
        if (typeof autoAdvWidget.value === "number" || (autoAdvWidget.options?.values && !autoAdvWidget.options.values.includes(autoAdvWidget.value))) {
            autoAdvWidget.value = "None (手动控制)";
        }
    }

    const isSlicer = (nodeTypeName === "MiniMaxVideoChunkSlicer");

    // Container DOM
    const container = document.createElement("div");
    container.className = "minimax-timeline-container";

    // 1. Header
    const header = document.createElement("div");
    header.className = "minimax-timeline-header";

    const titleGroup = document.createElement("div");
    titleGroup.className = "minimax-timeline-title-group";
    const headerTitle = isSlicer ? t("timeline.workbench_title") : t("timeline.assembler_title");
    const noVideoText = getLocale() === "ja" ? "動画未読込" : (getLocale() === "zh" ? "无视频" : "No Video");
    titleGroup.innerHTML = `
        <span>${headerTitle}</span>
        <span class="minimax-timeline-project-badge" id="project-badge">${noVideoText}</span>
        <span class="minimax-timeline-stats-badge" id="stats-badge">0%</span>
    `;

    const btnBar = document.createElement("div");
    btnBar.className = "minimax-timeline-btn-bar";

    if (isSlicer) {
        const nextBtn = document.createElement("button");
        nextBtn.className = "minimax-timeline-btn primary";
        nextBtn.innerHTML = t("timeline.next_shot");
        nextBtn.title = getLocale() === "ja" ? "次の分割クリップへ自動切替" : (getLocale() === "zh" ? "自动切换并跳转到下一个切片段落" : "Switch to next clip");
        nextBtn.onclick = (e) => {
            e.stopPropagation();
            advanceChunk(1);
        };
        btnBar.appendChild(nextBtn);

        const nextUneditedBtn = document.createElement("button");
        nextUneditedBtn.className = "minimax-timeline-btn";
        nextUneditedBtn.innerHTML = t("timeline.next_unrendered");
        nextUneditedBtn.title = getLocale() === "ja" ? "次の未生成クリップを検索してジャンプ" : (getLocale() === "zh" ? "自动寻找并跳转到下一个尚未编辑或遗漏的分段" : "Jump to next unrendered clip");
        nextUneditedBtn.onclick = (e) => {
            e.stopPropagation();
            jumpToNextUnedited();
        };
        btnBar.appendChild(nextUneditedBtn);
    } else {
        const exportBtn = document.createElement("button");
        exportBtn.className = "minimax-timeline-btn primary";
        exportBtn.innerHTML = t("timeline.export_full_mp4");
        exportBtn.title = getLocale() === "ja" ? "全クリップを結合し、音声付き完成MP4動画をoutputディレクトリへ出力" : (getLocale() === "zh" ? "直接将已回填缝合的长视频与音频压制导出为 H.264 MP4 文件至 output 目录" : "Export fully assembled video as MP4");
        exportBtn.onclick = async (e) => {
            e.stopPropagation();
            await exportMasterVideo(exportBtn);
        };
        btnBar.appendChild(exportBtn);

        const resetChunkBtn = document.createElement("button");
        resetChunkBtn.className = "minimax-timeline-btn";
        resetChunkBtn.innerHTML = t("timeline.reset_current_shot");
        resetChunkBtn.title = getLocale() === "ja" ? "選択中のクリップの生成結果をリセットし未生成に戻す" : (getLocale() === "zh" ? "将当前段标记为未编辑重新生成" : "Reset current clip to unrendered");
        resetChunkBtn.onclick = async (e) => {
            e.stopPropagation();
            await resetActiveChunk();
        };
        btnBar.appendChild(resetChunkBtn);
    }

    const refreshBtn = document.createElement("button");
    refreshBtn.className = "minimax-timeline-btn";
    refreshBtn.innerHTML = "🔄";
    refreshBtn.title = getLocale() === "ja" ? "動画とタイムラインの状態を再同期" : (getLocale() === "zh" ? "重新探测并同步视频与时间轴状态" : "Resync timeline state");
    refreshBtn.onclick = (e) => {
        e.stopPropagation();
        probeVideo(true);
    };
    btnBar.appendChild(refreshBtn);

    header.appendChild(titleGroup);
    header.appendChild(btnBar);
    container.appendChild(header);

    // Progress Bar Strip
    const progressTrack = document.createElement("div");
    progressTrack.className = "minimax-progress-track";
    progressTrack.innerHTML = `<div class="minimax-progress-fill" id="progress-fill" style="width: 0%;"></div>`;
    container.appendChild(progressTrack);

    // Quick Resolution & Length Presets Bar (for Slicer)
    if (isSlicer) {
        const presetBar = document.createElement("div");
        presetBar.className = "minimax-preset-capsules";
        const presetLabel = getLocale() === "ja" ? "⚡ 推奨プリセット:" : (getLocale() === "zh" ? "⚡ 推荐预设:" : "⚡ Presets:");
        const stdPresetText = getLocale() === "ja" ? "🌟 MiniMax標準 (1344x768)" : (getLocale() === "zh" ? "🌟 MiniMax标配 (1344x768)" : "🌟 MiniMax Std (1344x768)");
        const origSizeText = getLocale() === "ja" ? "元サイズ" : (getLocale() === "zh" ? "原尺寸" : "Original");
        presetBar.innerHTML = `
            <span style="color:#64748b;font-weight:600;font-size:10px;">${presetLabel}</span>
            <button class="preset-capsule primary" data-w="1344" data-h="768" data-len="124" title="MiniMax 1344x768 / 124F / 16:9">${stdPresetText}</button>
            <button class="preset-capsule" data-w="1280" data-h="720" data-len="124" title="720p (1280x720)">720p</button>
            <button class="preset-capsule" data-w="1920" data-h="1080" data-len="124" title="1080p (1920x1080)">1080p</button>
            <button class="preset-capsule" data-w="0" data-h="0" title="${origSizeText}">${origSizeText}</button>
        `;
        presetBar.querySelectorAll(".preset-capsule").forEach(btn => {
            btn.onclick = (e) => {
                e.stopPropagation();
                const tw = parseInt(btn.dataset.w, 10);
                const th = parseInt(btn.dataset.h, 10);
                const tlen = btn.dataset.len ? parseInt(btn.dataset.len, 10) : null;
                const wW = node.widgets?.find(w => w.name === "target_width");
                const hW = node.widgets?.find(w => w.name === "target_height");
                const lW = node.widgets?.find(w => w.name === "chunk_length");
                if (wW) { wW.value = tw; if (wW.callback) wW.callback(tw); }
                if (hW) { hW.value = th; if (hW.callback) hW.callback(th); }
                if (tlen && lW) { lW.value = tlen; if (lW.callback) lW.callback(tlen); }
                probeVideo(true);
            };
        });
        container.appendChild(presetBar);
    }

    // 2. Embedded Video Player Box (For Slicer)
    let videoEl = null;
    let loopSelectionEnabled = true;
    let isSeeking = false;

    if (isSlicer) {
        const playerBox = document.createElement("div");
        playerBox.className = "minimax-player-box";

        const viewport = document.createElement("div");
        viewport.className = "minimax-video-viewport";

        videoEl = document.createElement("video");
        videoEl.className = "minimax-timeline-video";
        videoEl.preload = "auto";
        videoEl.playsInline = true;
        videoEl.muted = false;

        const placeholder = document.createElement("div");
        placeholder.className = "minimax-video-placeholder";
        const placeholderText = getLocale() === "ja" ? "🎞️ 動画を選択するとここでリアルタイムプレビューできます" : (getLocale() === "zh" ? "🎞️ 选择或上传视频后将在此实时预览" : "🎞️ Select a video to preview");
        placeholder.innerHTML = `<span>${placeholderText}</span>`;

        viewport.appendChild(videoEl);
        viewport.appendChild(placeholder);
        playerBox.appendChild(viewport);

        // Player Controls Bar
        const controls = document.createElement("div");
        controls.className = "minimax-player-controls";

        const leftGroup = document.createElement("div");
        leftGroup.className = "player-left-group";

        const playBtn = document.createElement("button");
        playBtn.className = "player-btn";
        const playText = getLocale() === "ja" ? "▶ 再生" : (getLocale() === "zh" ? "▶ 播放" : "▶ Play");
        const pauseText = getLocale() === "ja" ? "⏸ 一時停止" : (getLocale() === "zh" ? "⏸ 暂停" : "⏸ Pause");
        playBtn.innerHTML = playText;
        playBtn.onclick = (e) => {
            e.stopPropagation();
            if (videoEl.paused) {
                videoEl.play();
                playBtn.innerHTML = pauseText;
            } else {
                videoEl.pause();
                playBtn.innerHTML = playText;
            }
        };

        const prevFrameBtn = document.createElement("button");
        prevFrameBtn.className = "player-btn";
        prevFrameBtn.innerHTML = "◀ -1F";
        prevFrameBtn.title = getLocale() === "ja" ? "1フレーム戻る" : (getLocale() === "zh" ? "后退 1 帧" : "Step -1 Frame");
        prevFrameBtn.onclick = (e) => {
            e.stopPropagation();
            stepFrame(-1);
        };

        const nextFrameBtn = document.createElement("button");
        nextFrameBtn.className = "player-btn";
        nextFrameBtn.innerHTML = "+1F ▶";
        nextFrameBtn.title = getLocale() === "ja" ? "1フレーム進む" : (getLocale() === "zh" ? "前进 1 帧" : "Step +1 Frame");
        nextFrameBtn.onclick = (e) => {
            e.stopPropagation();
            stepFrame(1);
        };

        const loopBtn = document.createElement("button");
        loopBtn.className = "player-btn active";
        const loopLabel = (on) => getLocale() === "ja" ? `🔁 クリップループ: ${on ? 'ON' : 'OFF'}` : (getLocale() === "zh" ? `🔁 循环当前段: ${on ? '开' : '关'}` : `🔁 Loop Clip: ${on ? 'ON' : 'OFF'}`);
        loopBtn.innerHTML = loopLabel(true);
        loopBtn.title = getLocale() === "ja" ? "有効にするとクリップの末尾に達した際に先頭へ自動ループします" : (getLocale() === "zh" ? "开启后播放到当前段末尾会自动回到起始帧循环播放" : "Loop playback within active clip boundary");
        loopBtn.onclick = (e) => {
            e.stopPropagation();
            loopSelectionEnabled = !loopSelectionEnabled;
            loopBtn.className = `player-btn ${loopSelectionEnabled ? 'active' : ''}`;
            loopBtn.innerHTML = loopLabel(loopSelectionEnabled);
        };

        leftGroup.appendChild(playBtn);
        leftGroup.appendChild(prevFrameBtn);
        leftGroup.appendChild(nextFrameBtn);
        leftGroup.appendChild(loopBtn);

        const rightGroup = document.createElement("div");
        rightGroup.className = "player-right-group";

        const timecode = document.createElement("div");
        timecode.className = "minimax-timecode-badge";
        timecode.id = "player-timecode";
        const frameLabel = getLocale() === "ja" ? "フレーム" : (getLocale() === "zh" ? "帧" : "Frame");
        timecode.innerText = `00:00.0 / 00:00.0 | ${frameLabel} 0`;

        rightGroup.appendChild(timecode);
        controls.appendChild(leftGroup);
        controls.appendChild(rightGroup);
        playerBox.appendChild(controls);
        container.appendChild(playerBox);

        // Preset Length Capsules (H3 Golden Grid Lengths)
        const capsuleBar = document.createElement("div");
        capsuleBar.className = "minimax-preset-capsules";
        const gridLabel = getLocale() === "ja" ? "H3推奨フレーム長:" : (getLocale() === "zh" ? "H3预设长度:" : "H3 Grid Lengths:");
        capsuleBar.innerHTML = `
            <span>${gridLabel}</span>
            <span class="preset-capsule" data-len="39">⚡ 39F (1.6s)</span>
            <span class="preset-capsule" data-len="90">⚡ 90F (3.75s)</span>
            <span class="preset-capsule" data-len="124">⚡ 124F (5.16s)</span>
            <span class="preset-capsule" data-len="141">⚡ 141F (5.87s)</span>
        `;
        capsuleBar.querySelectorAll(".preset-capsule").forEach(cap => {
            cap.onclick = (e) => {
                e.stopPropagation();
                const targetLen = parseInt(cap.getAttribute("data-len"), 10);
                applyChunkLength(targetLen);
            };
        });
        container.appendChild(capsuleBar);
    }

    // 3. Main Track & Clean Chunk Grid
    const trackWrap = document.createElement("div");
    trackWrap.className = "minimax-timeline-track-wrap";

    const ruler = document.createElement("div");
    ruler.className = "minimax-timeline-ruler";
    const startRulerText = getLocale() === "ja" ? "00:00 (0フレーム)" : "00:00 (Frame 0)";
    ruler.innerHTML = `<span>${startRulerText}</span><span id="ruler-end">${startRulerText}</span>`;

    const blocksBar = document.createElement("div");
    blocksBar.className = "minimax-timeline-blocks-bar";

    // Smooth real-time playhead needle
    const playhead = document.createElement("div");
    playhead.className = "minimax-playhead";
    playhead.id = "timeline-playhead";
    blocksBar.appendChild(playhead);

    trackWrap.appendChild(ruler);
    trackWrap.appendChild(blocksBar);
    container.appendChild(trackWrap);

    // 4. Footer Legend & Current Selection Details
    const footer = document.createElement("div");
    footer.className = "minimax-timeline-footer";
    const legCompleted = getLocale() === "ja" ? "生成完了" : (getLocale() === "zh" ? "已编辑" : "Completed");
    const legActive = getLocale() === "ja" ? "選択中" : (getLocale() === "zh" ? "当前段" : "Active");
    const legUnprocessed = getLocale() === "ja" ? "未生成" : (getLocale() === "zh" ? "未编辑" : "Unrendered");
    const legGap = getLocale() === "ja" ? "⚠️未完了" : (getLocale() === "zh" ? "⚠️漏编" : "⚠️Gap");
    footer.innerHTML = `
        <div class="minimax-timeline-legend">
            <div class="legend-item"><div class="legend-dot completed"></div>${legCompleted}</div>
            <div class="legend-item"><div class="legend-dot active"></div>${legActive}</div>
            <div class="legend-item"><div class="legend-dot unprocessed"></div>${legUnprocessed}</div>
            <div class="legend-item"><div class="legend-dot gap"></div>${legGap}</div>
        </div>
        <div class="minimax-timeline-details" id="timeline-details">${t("common.loading")}</div>
    `;
    container.appendChild(footer);

    // Register widget with ComfyUI
    node.addDOMWidget("timeline_ui", "custom", container, {
        getValue() { return ""; },
        setValue(v) { }
    });

    node.setSize([Math.max(node.size[0] || 520, 560), Math.max(node.size[1] || 480, 520)]);

    let cachedMeta = null;
    let isProbing = false;

    // ================= Optimized Video Player & Seeking =================
    if (videoEl) {
        videoEl.addEventListener("seeking", () => { isSeeking = true; });
        videoEl.addEventListener("seeked", () => { isSeeking = false; });

        videoEl.ontimeupdate = () => {
            const timecodeEl = container.querySelector("#player-timecode");
            const fps = cachedMeta?.fps || 24.0;
            const curSec = videoEl.currentTime;
            const curFrame = Math.round(curSec * fps);
            const totalSec = videoEl.duration || (cachedMeta?.total_frames ? cachedMeta.total_frames / fps : 0);

            if (timecodeEl) {
                const fUnit = getLocale() === "ja" ? "フレーム" : (getLocale() === "zh" ? "帧" : "F");
                timecodeEl.innerText = `${formatTime(curSec)} / ${formatTime(totalSec)} | ${fUnit}: ${curFrame}`;
            }

            // Update playhead position smoothly
            if (totalSec > 0) {
                const pct = Math.min(100, Math.max(0, (curSec / totalSec) * 100));
                playhead.style.left = `${pct}%`;
            }

            // Loop active chunk boundary guard
            if (loopSelectionEnabled && cachedMeta && !isSeeking) {
                const { startFrame, endFrame } = getActiveWindow();
                const startSec = startFrame / fps;
                const endSec = endFrame / fps;
                if (curSec >= endSec - 0.04 || curSec < startSec - 0.5) {
                    isSeeking = true;
                    videoEl.currentTime = startSec;
                }
            }
        };

        videoEl.onended = () => {
            const playBtn = container.querySelector(".player-left-group .player-btn");
            if (playBtn) playBtn.innerHTML = getLocale() === "ja" ? "▶ 再生" : (getLocale() === "zh" ? "▶ 播放" : "▶ Play");
        };
    }

    function stepFrame(delta) {
        if (!videoEl || isSeeking) return;
        const fps = cachedMeta?.fps || 24.0;
        isSeeking = true;
        videoEl.currentTime = Math.max(0, videoEl.currentTime + (delta / fps));
    }

    function getActiveWindow() {
        const chunkIndexWidget = node.widgets?.find(w => w.name === "chunk_index");
        const chunkLenWidget = node.widgets?.find(w => w.name === "chunk_length");
        const modeWidget = node.widgets?.find(w => w.name === "slice_mode");
        const startWidget = node.widgets?.find(w => w.name === "custom_start_frame");
        const endWidget = node.widgets?.find(w => w.name === "custom_end_frame");

        const isCustom = modeWidget?.value?.includes("Custom Range");
        if (isCustom && startWidget && endWidget) {
            return {
                startFrame: parseInt(startWidget.value || 0, 10),
                endFrame: parseInt(endWidget.value || 124, 10),
            };
        }

        const cIdx = parseInt(chunkIndexWidget?.value || 0, 10);
        const cLen = parseInt(chunkLenWidget?.value || 124, 10);
        const sf = cIdx * cLen;
        const totalF = cachedMeta?.total_frames || 99999;
        const ef = Math.min(totalF, sf + cLen);
        return { startFrame: sf, endFrame: ef };
    }

    function applyChunkLength(targetLen) {
        const chunkLenWidget = node.widgets?.find(w => w.name === "chunk_length");
        if (chunkLenWidget) {
            chunkLenWidget.value = targetLen;
            if (chunkLenWidget.callback) chunkLenWidget.callback(targetLen);
        }
        probeVideo(true);
    }

    let selectedChunkIdx = 0;

    function getResolvedProjectName() {
        const projectWidget = node.widgets?.find(w => w.name === "project_name");
        if (projectWidget?.value) return projectWidget.value;
        if (!isSlicer) {
            const ctxInput = node.inputs?.find(inp => inp.name === "slice_context");
            if (ctxInput && ctxInput.link != null && app.graph?.links) {
                const link = app.graph.links[ctxInput.link];
                if (link) {
                    const originNode = app.graph.getNodeById(link.origin_id);
                    const p = originNode?.widgets?.find(w => w.name === "project_name")?.value;
                    if (p) return p;
                }
            }
            if (app.graph?._nodes) {
                const slicerNode = app.graph._nodes.find(n => n.type === "MiniMaxVideoChunkSlicer");
                const p = slicerNode?.widgets?.find(w => w.name === "project_name")?.value;
                if (p) return p;
            }
        }
        return "Video_Edit_Project";
    }

    // ================= Probe Video & Sync with Server =================
    async function probeVideo(force = false) {
        if (isProbing && !force) return;
        isProbing = true;

        if (!isSlicer) {
            // Reassembler node
            const projectName = getResolvedProjectName();
            const details = container.querySelector("#timeline-details");
            const projectBadge = container.querySelector("#project-badge");

            const ctxInput = node.inputs?.find(inp => inp.name === "slice_context");
            const isConnected = (ctxInput && ctxInput.link != null);

            try {
                const res = await api.fetchApi(`/minimax/timeline/state?project=${encodeURIComponent(projectName)}`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.success && data.meta && data.meta.total_frames > 0) {
                        cachedMeta = data.meta;
                        renderTimeline();
                    } else {
                        if (projectBadge) projectBadge.innerText = projectName;
                        if (details) {
                            const connectedMsg = getLocale() === "ja" ? `<span>⏳ プロジェクト [${projectName}] 接続中。実行後に各クリップの結合進捗が表示されます</span>` : (getLocale() === "zh" ? `<span>⏳ 项目 [${projectName}] 已连接，运行后自动显示各段拼接进度</span>` : `<span>⏳ Project [${projectName}] connected</span>`);
                            const unconnMsg = getLocale() === "ja" ? `<span>🔗 スライサー (Slicer) の slice_context をこのノードに接続してください</span>` : (getLocale() === "zh" ? `<span>🔗 请将切片器 (Slicer) 的 slice_context 连入此节点</span>` : `<span>🔗 Please connect slice_context from Slicer</span>`);
                            details.innerHTML = isConnected ? connectedMsg : unconnMsg;
                        }
                    }
                }
            } catch (err) {
                console.debug("[Reassembler Widget] Fetch state failed:", err);
            } finally {
                isProbing = false;
            }
            return;
        }

        const videoWidget = node.widgets?.find(w => w.name === "video_file");
        const projectWidget = node.widgets?.find(w => w.name === "project_name");
        const chunkLenWidget = node.widgets?.find(w => w.name === "chunk_length");
        const fpsWidget = node.widgets?.find(w => w.name === "force_fps");
        const widthWidget = node.widgets?.find(w => w.name === "target_width");
        const heightWidget = node.widgets?.find(w => w.name === "target_height");

        const videoName = videoWidget?.value;
        const projectName = projectWidget?.value || "Video_Edit_Project";

        if (!videoName || videoName === "none") {
            const placeholder = container.querySelector(".minimax-video-placeholder");
            if (placeholder) {
                placeholder.style.display = "flex";
                const selectHint = getLocale() === "ja" ? "<span>🎞️ 上のドロップダウンから動画ファイルを選択してください</span>" : (getLocale() === "zh" ? "<span>🎞️ 请在上方下拉框选择视频文件</span>" : "<span>🎞️ Please select a video file</span>");
                placeholder.innerHTML = selectHint;
            }
            isProbing = false;
            return;
        }

        const params = new URLSearchParams({
            video: videoName,
            project: projectName,
            chunk_length: chunkLenWidget?.value || 124,
            force_fps: fpsWidget?.value || 24.0,
            target_width: widthWidget?.value || 0,
            target_height: heightWidget?.value || 0,
        });

        try {
            const res = await api.fetchApi(`/minimax/timeline/probe_video?${params.toString()}`);
            if (res.ok) {
                const data = await res.json();
                if (data.success) {
                    cachedMeta = data.meta;
                    if (videoEl && data.video_url) {
                        const placeholder = container.querySelector(".minimax-video-placeholder");
                        if (placeholder) placeholder.style.display = "none";
                        const fullTarget = data.video_url.startsWith("http") ? data.video_url : (window.location.origin + data.video_url);
                        if (videoEl.src !== fullTarget) {
                            videoEl.src = data.video_url;
                            videoEl.load();
                        }
                    }
                    renderTimeline();
                }
            }
        } catch (err) {
            console.debug("[Timeline Widget] Probe video failed:", err);
        } finally {
            isProbing = false;
        }
    }

    function renderTimeline() {
        if (!cachedMeta) return;

        const projectBadge = container.querySelector("#project-badge");
        const statsBadge = container.querySelector("#stats-badge");
        const rulerEnd = container.querySelector("#ruler-end");
        const details = container.querySelector("#timeline-details");

        const totalFrames = cachedMeta.total_frames || 0;
        const fps = cachedMeta.fps || 24.0;
        const totalSec = totalFrames > 0 ? (totalFrames / fps) : 0;
        const covRatio = cachedMeta.coverage_ratio || 0.0;
        const covPct = Math.round(covRatio * 100);
        const gaps = cachedMeta.gaps || [];
        const chunks = cachedMeta.chunks || [];

        const srcFile = cachedMeta.source_video_path ? cachedMeta.source_video_path.split(/[\\/]/).pop() : (cachedMeta.project_name || "Video");
        if (projectBadge) projectBadge.innerText = srcFile;
        if (statsBadge) {
            const asmSuffix = getLocale() === "ja" ? "統合済" : (getLocale() === "zh" ? "已组装" : "Assembled");
            statsBadge.innerText = `${covPct}% ${asmSuffix}`;
            statsBadge.className = `minimax-timeline-stats-badge ${cachedMeta.is_fully_assembled ? 'all-done' : (gaps.length > 0 && covPct > 0 ? 'has-gap' : '')}`;
        }
        const progressFill = container.querySelector("#progress-fill");
        if (progressFill) {
            progressFill.style.width = `${covPct}%`;
            progressFill.className = `minimax-progress-fill ${covPct >= 100 ? 'done' : (gaps.length > 0 && covPct > 0 ? 'gap' : '')}`;
        }
        if (rulerEnd) {
            const frameUnit = getLocale() === "ja" ? "フレーム" : (getLocale() === "zh" ? "帧" : "Frame");
            rulerEnd.innerText = `${formatTime(totalSec)} (${totalFrames} ${frameUnit})`;
        }

        // Active chunk index
        const chunkIndexWidget = node.widgets?.find(w => w.name === "chunk_index");
        const activeIdx = chunkIndexWidget ? parseInt(chunkIndexWidget.value || 0, 10) : selectedChunkIdx;

        // Clean & render chunk blocks
        const oldBlocks = blocksBar.querySelectorAll(".minimax-chunk-block");
        oldBlocks.forEach(b => b.remove());

        if (chunks.length === 0) return;

        const completedIndices = new Set(chunks.filter(c => c.status === "completed").map(c => c.chunk_index));
        const maxCompleted = completedIndices.size > 0 ? Math.max(...completedIndices) : -1;

        chunks.forEach((c) => {
            const block = document.createElement("div");
            const cIdx = c.chunk_index;
            const isCompleted = (c.status === "completed");
            const isActive = (cIdx === activeIdx);
            const isGap = (!isCompleted && cIdx < maxCompleted);

            let statusClass = "status-unprocessed";
            if (isCompleted) statusClass = "status-completed";
            else if (isGap) statusClass = "status-gap";
            if (isActive) statusClass += " status-active";

            block.className = `minimax-chunk-block ${statusClass}`;
            const stTime = formatTime(c.start_frame / fps);
            const edTime = formatTime(c.end_frame / fps);

            block.innerHTML = `
                <div class="chunk-label">#${cIdx}</div>
                <div class="chunk-time">${stTime}</div>
            `;

            const blockTooltip = getLocale() === "ja" ?
                `クリップ #${cIdx} (${c.start_frame}~${c.end_frame}フレーム, ${stTime}~${edTime})\nクリックで選択しプレイヤーでプレビュー` :
                (getLocale() === "zh" ?
                    `分段 #${cIdx} (${c.start_frame}~${c.end_frame}帧, ${stTime}~${edTime})\n点击选中并在上方播放此段` :
                    `Clip #${cIdx} (${c.start_frame}-${c.end_frame}F, ${stTime}-${edTime})\nClick to preview`);
            block.title = blockTooltip;

            block.onclick = (e) => {
                e.stopPropagation();
                selectedChunkIdx = cIdx;
                if (chunkIndexWidget) {
                    chunkIndexWidget.value = cIdx;
                    if (chunkIndexWidget.callback) chunkIndexWidget.callback(cIdx);
                }
                // Keep exact internal enum string
                const modeWidget = node.widgets?.find(w => w.name === "slice_mode");
                if (modeWidget) {
                    modeWidget.value = "Auto Chunk Grid (网格切分)";
                }
                if (videoEl) {
                    isSeeking = true;
                    videoEl.currentTime = c.start_frame / fps;
                    videoEl.play();
                }
                renderTimeline();
            };

            blocksBar.appendChild(block);
        });

        // Update footer details info
        if (details) {
            if (isSlicer) {
                const activeChunk = chunks.find(c => c.chunk_index === activeIdx);
                if (activeChunk) {
                    const sf = activeChunk.start_frame;
                    const ef = activeChunk.end_frame;
                    const dur = (ef - sf) / fps;
                    const fUnit = getLocale() === "ja" ? "フレーム" : (getLocale() === "zh" ? "帧" : "F");
                    const lenLabel = getLocale() === "ja" ? "長さ:" : (getLocale() === "zh" ? "长度:" : "Length:");
                    details.innerText = `${getLocale() === "ja" ? "選択中" : "选中"} #${activeIdx}: ${sf}~${ef}${fUnit} (${formatTime(sf/fps)}~${formatTime(ef/fps)}) | ${lenLabel} ${ef - sf}${fUnit} (${dur.toFixed(2)}s)`;
                }
            } else {
                const doneCount = completedIndices.size;
                const totalCount = chunks.length;
                if (covPct >= 100) {
                    const completeMsg = getLocale() === "ja" ?
                        `🎉 全長動画が100%結合完了しました！すべてのクリップが正常に統合されています。右上の【${t("timeline.export_full_mp4")}】をクリックしてください。` :
                        (getLocale() === "zh" ?
                            `🎉 全长视频已 100% 拼接完成！所有分段均已缝合到位。可点击右上角【🎬 导出全片 MP4】。` :
                            `🎉 100% Assembled! Click [Export Full MP4] above.`);
                    details.innerHTML = `<span style="color:#10b981;font-weight:bold;">${completeMsg}</span>`;
                } else {
                    const activeChunk = chunks.find(c => c.chunk_index === activeIdx);
                    const filledLabel = activeChunk?.status === 'completed' ? (getLocale() === "ja" ? "統合済" : "已回填") : (getLocale() === "ja" ? "未統合" : "未回填");
                    const selInfo = activeChunk ? ` | ${getLocale() === "ja" ? "選択中" : "选中"} #${activeIdx} (${filledLabel})` : '';
                    const progressPrefix = getLocale() === "ja" ? "統合進捗:" : (getLocale() === "zh" ? "总装进度:" : "Progress:");
                    const assembledCountText = getLocale() === "ja" ? `(統合済み ${doneCount}/${totalCount} 本)` : (getLocale() === "zh" ? `(已缝合 ${doneCount}/${totalCount} 段)` : `(${doneCount}/${totalCount} clips)`);
                    details.innerText = `${progressPrefix} ${covPct}% ${assembledCountText}${selInfo}`;
                }
            }
        }
    }

    async function exportMasterVideo(targetBtn) {
        const projectName = getResolvedProjectName();
        const origHtml = targetBtn.innerHTML;
        targetBtn.innerHTML = getLocale() === "ja" ? "⏳ 動画を出力中..." : (getLocale() === "zh" ? "⏳ 正在压制导出..." : "⏳ Exporting...");
        targetBtn.disabled = true;

        try {
            const res = await api.fetchApi("/minimax/timeline/export_video", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project: projectName, crf: 18, preset: "fast" })
            });
            const data = await res.json();
            if (data.success) {
                const audioLabel = data.has_audio ? (getLocale() === "ja" ? "あり" : "是") : (getLocale() === "ja" ? "なし" : "否");
                const successAlert = getLocale() === "ja" ?
                    `🎉 完成動画（MP4）の出力が完了しました！\n\n` +
                    `📁 ファイル名: ${data.file_name}\n` +
                    `⏱️ 再生時間: ${data.duration} 秒 (${data.total_frames} フレーム @ ${data.fps}fps)\n` +
                    `📐 解像度: ${data.width}x${data.height}\n` +
                    `💾 ファイルサイズ: ${data.file_size_mb} MB\n` +
                    `🎵 音声トラック: ${audioLabel}\n\n` +
                    `保存先ディレクトリ (ComfyUI output):\n${data.file_path}` :
                    (getLocale() === "zh" ?
                        `🎉 恭喜！长视频完整成品已导出成功！\n\n` +
                        `📁 文件名: ${data.file_name}\n` +
                        `⏱️ 时长: ${data.duration} 秒 (${data.total_frames} 帧 @ ${data.fps}fps)\n` +
                        `📐 分辨率: ${data.width}x${data.height}\n` +
                        `💾 文件大小: ${data.file_size_mb} MB\n` +
                        `🎵 包含音频: ${data.has_audio ? '是' : '否'}\n\n` +
                        `已保存至 ComfyUI 输出目录:\n${data.file_path}` :
                        `🎉 Video exported successfully!\n\nFile: ${data.file_name}\nPath: ${data.file_path}`);
                alert(successAlert);
            } else {
                alert(`⚠️ ${t("timeline.export_failed", { error: data.error || "Unknown" })}`);
            }
        } catch (err) {
            alert(`⚠️ ${err.message}`);
        } finally {
            targetBtn.innerHTML = origHtml;
            targetBtn.disabled = false;
        }
    }

    function advanceChunk(delta = 1) {
        const chunkIndexWidget = node.widgets?.find(w => w.name === "chunk_index");
        if (!chunkIndexWidget) return;
        const totalChunks = cachedMeta?.total_chunks || 999;
        let nextVal = parseInt(chunkIndexWidget.value || 0, 10) + delta;
        if (nextVal >= totalChunks) nextVal = 0;
        chunkIndexWidget.value = Math.max(0, nextVal);
        if (chunkIndexWidget.callback) chunkIndexWidget.callback(chunkIndexWidget.value);

        const modeWidget = node.widgets?.find(w => w.name === "slice_mode");
        if (modeWidget) modeWidget.value = "Auto Chunk Grid (网格切分)";

        const { startFrame } = getActiveWindow();
        if (videoEl && cachedMeta) {
            isSeeking = true;
            videoEl.currentTime = startFrame / (cachedMeta.fps || 24.0);
            videoEl.play();
        }
        renderTimeline();
    }

    function jumpToNextUnedited() {
        const chunkIndexWidget = node.widgets?.find(w => w.name === "chunk_index");
        if (!chunkIndexWidget || !cachedMeta) return;

        const chunks = cachedMeta.chunks || [];
        const activeIdx = parseInt(chunkIndexWidget.value || 0, 10);

        let target = chunks.find(c => c.chunk_index > activeIdx && c.status !== "completed");
        if (!target) {
            target = chunks.find(c => c.status !== "completed");
        }

        if (target) {
            chunkIndexWidget.value = target.chunk_index;
            if (chunkIndexWidget.callback) chunkIndexWidget.callback(target.chunk_index);
            const modeWidget = node.widgets?.find(w => w.name === "slice_mode");
            if (modeWidget) modeWidget.value = "Auto Chunk Grid (网格切分)";
            if (videoEl) {
                isSeeking = true;
                videoEl.currentTime = target.start_frame / (cachedMeta.fps || 24.0);
                videoEl.play();
            }
            renderTimeline();
        } else {
            const allDoneMsg = getLocale() === "ja" ?
                "🎉 すべてのクリップの生成が完了しています！未完了またはスキップされたクリップはありません。" :
                (getLocale() === "zh" ?
                    "🎉 全片所有分段均已编辑完成！无未完成或遗漏段落。" :
                    "🎉 All clips have been rendered!");
            alert(allDoneMsg);
        }
    }

    async function resetActiveChunk() {
        const projectName = getResolvedProjectName();
        const chunkIndexWidget = node.widgets?.find(w => w.name === "chunk_index");
        const activeIdx = chunkIndexWidget ? parseInt(chunkIndexWidget.value || 0, 10) : selectedChunkIdx;

        const confirmReset = getLocale() === "ja" ?
            `クリップ #${activeIdx} の生成結果をリセットし、未生成状態に戻しますか？` :
            (getLocale() === "zh" ?
                `确定重置分段 #${activeIdx} 的编辑状态吗？` :
                `Reset clip #${activeIdx} to unrendered?`);
        if (!confirm(confirmReset)) return;

        try {
            const res = await api.fetchApi("/minimax/timeline/reset_chunk", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project: projectName, chunk_index: activeIdx })
            });
            if (res.ok) {
                probeVideo(true);
            }
        } catch (err) {
            console.error("[Timeline Widget] Reset failed:", err);
        }
    }

    node._onExecutedHook = (message) => {
        if (isSlicer) {
            const autoAdv = node.widgets?.find(w => w.name === "auto_advance")?.value || "";
            if (autoAdv.includes("Next Chunk") || autoAdv.includes("顺序下一段")) {
                console.log("[MiniMax Timeline] Auto-advancing chunk index (+1)...");
                advanceChunk(1);
            } else if (autoAdv.includes("Next Unedited") || autoAdv.includes("下一未编辑")) {
                console.log("[MiniMax Timeline] Auto-jumping to next unedited chunk...");
                jumpToNextUnedited();
            }
        }
    };

    // Attach callbacks to relevant ComfyUI widgets (preserving internal enum values)
    ["video_file", "chunk_length", "chunk_index", "slice_mode", "force_fps", "target_width", "target_height", "auto_advance"].forEach(wName => {
        const w = node.widgets?.find(w => w.name === wName);
        if (w) {
            const origCb = w.callback;
            w.callback = function () {
                if (origCb) origCb.apply(this, arguments);
                if (wName === "video_file" || wName === "chunk_length" || wName === "force_fps") {
                    probeVideo(true);
                } else {
                    renderTimeline();
                }
            };
        }
    });

    node._refreshTimeline = () => probeVideo(true);
    node._probeVideoImmediate = () => probeVideo(false);

    // Initial probe on node initialization
    setTimeout(() => probeVideo(false), 150);
}
