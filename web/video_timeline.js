import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

/**
 * MiniMax H3 Video Timeline & Smart Chunk Slicer - High Performance & Clean UI Edition.
 *
 * Solves:
 * 1. Double yellow box confusion: Removed overlapping rangeOverlay layer; single clear chunk highlight.
 * 2. Canvas stutter/lag: Removed heavy setDirtyCanvas and global mousemove listeners, added seek-locks.
 * 3. Instant video preview: Smooth local playback with frame-stepping and loop-chunk capabilities.
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
    titleGroup.innerHTML = `
        <span>${isSlicer ? "🎬 视频智能切片工作台" : "🧩 回填总装时间轴"}</span>
        <span class="minimax-timeline-project-badge" id="project-badge">No Video</span>
        <span class="minimax-timeline-stats-badge" id="stats-badge">0%</span>
    `;

    const btnBar = document.createElement("div");
    btnBar.className = "minimax-timeline-btn-bar";

    if (isSlicer) {
        const nextBtn = document.createElement("button");
        nextBtn.className = "minimax-timeline-btn primary";
        nextBtn.innerHTML = "⏭️ 下一段";
        nextBtn.title = "自动切换并跳转到下一个切片段落";
        nextBtn.onclick = (e) => {
            e.stopPropagation();
            advanceChunk(1);
        };
        btnBar.appendChild(nextBtn);

        const nextUneditedBtn = document.createElement("button");
        nextUneditedBtn.className = "minimax-timeline-btn";
        nextUneditedBtn.innerHTML = "🎯 下一未编辑";
        nextUneditedBtn.title = "自动寻找并跳转到下一个尚未编辑或遗漏的分段";
        nextUneditedBtn.onclick = (e) => {
            e.stopPropagation();
            jumpToNextUnedited();
        };
        btnBar.appendChild(nextUneditedBtn);
    } else {
        const exportBtn = document.createElement("button");
        exportBtn.className = "minimax-timeline-btn primary";
        exportBtn.innerHTML = "🎬 导出全片 MP4";
        exportBtn.title = "直接将已回填缝合的长视频与音频压制导出为 H.264 MP4 文件至 output 目录";
        exportBtn.onclick = async (e) => {
            e.stopPropagation();
            await exportMasterVideo(exportBtn);
        };
        btnBar.appendChild(exportBtn);

        const resetChunkBtn = document.createElement("button");
        resetChunkBtn.className = "minimax-timeline-btn";
        resetChunkBtn.innerHTML = "↩️ 重置当前段";
        resetChunkBtn.title = "将当前段标记为未编辑重新生成";
        resetChunkBtn.onclick = async (e) => {
            e.stopPropagation();
            await resetActiveChunk();
        };
        btnBar.appendChild(resetChunkBtn);
    }

    const refreshBtn = document.createElement("button");
    refreshBtn.className = "minimax-timeline-btn";
    refreshBtn.innerHTML = "🔄";
    refreshBtn.title = "重新探测并同步视频与时间轴状态";
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
        presetBar.innerHTML = `
            <span style="color:#64748b;font-weight:600;font-size:10px;">⚡ 推荐预设:</span>
            <button class="preset-capsule primary" data-w="1344" data-h="768" data-len="124" title="MiniMax 官方默认推荐 (1344x768 / 124帧 / 16:9)">🌟 MiniMax标配 (1344x768)</button>
            <button class="preset-capsule" data-w="1280" data-h="720" data-len="124" title="标准 720p 宽屏 (1280x720)">720p</button>
            <button class="preset-capsule" data-w="1920" data-h="1080" data-len="124" title="全高清 1080p (1920x1080)">1080p</button>
            <button class="preset-capsule" data-w="0" data-h="0" title="保持原视频自身原始分辨率">原尺寸</button>
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
        placeholder.innerHTML = "<span>🎞️ 选择或上传视频后将在此实时预览</span>";

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
        playBtn.innerHTML = "▶ 播放";
        playBtn.onclick = (e) => {
            e.stopPropagation();
            if (videoEl.paused) {
                videoEl.play();
                playBtn.innerHTML = "⏸ 暂停";
            } else {
                videoEl.pause();
                playBtn.innerHTML = "▶ 播放";
            }
        };

        const prevFrameBtn = document.createElement("button");
        prevFrameBtn.className = "player-btn";
        prevFrameBtn.innerHTML = "◀ -1F";
        prevFrameBtn.title = "后退 1 帧";
        prevFrameBtn.onclick = (e) => {
            e.stopPropagation();
            stepFrame(-1);
        };

        const nextFrameBtn = document.createElement("button");
        nextFrameBtn.className = "player-btn";
        nextFrameBtn.innerHTML = "+1F ▶";
        nextFrameBtn.title = "前进 1 帧";
        nextFrameBtn.onclick = (e) => {
            e.stopPropagation();
            stepFrame(1);
        };

        const loopBtn = document.createElement("button");
        loopBtn.className = "player-btn active";
        loopBtn.innerHTML = "🔁 循环当前段: 开";
        loopBtn.title = "开启后播放到当前段末尾会自动回到起始帧循环播放";
        loopBtn.onclick = (e) => {
            e.stopPropagation();
            loopSelectionEnabled = !loopSelectionEnabled;
            loopBtn.className = `player-btn ${loopSelectionEnabled ? 'active' : ''}`;
            loopBtn.innerHTML = loopSelectionEnabled ? "🔁 循环当前段: 开" : "🔁 循环当前段: 关";
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
        timecode.innerText = "00:00.0 / 00:00.0 | 帧 0";

        rightGroup.appendChild(timecode);
        controls.appendChild(leftGroup);
        controls.appendChild(rightGroup);
        playerBox.appendChild(controls);
        container.appendChild(playerBox);

        // Preset Length Capsules (H3 Golden Grid Lengths)
        const capsuleBar = document.createElement("div");
        capsuleBar.className = "minimax-preset-capsules";
        capsuleBar.innerHTML = `
            <span>H3预设长度:</span>
            <span class="preset-capsule" data-len="39">⚡ 39帧 (1.6s)</span>
            <span class="preset-capsule" data-len="90">⚡ 90帧 (3.75s)</span>
            <span class="preset-capsule" data-len="124">⚡ 124帧 (5.16s)</span>
            <span class="preset-capsule" data-len="141">⚡ 141帧 (5.87s)</span>
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

    // 3. Main Track & Clean Chunk Grid (No overlapping rangeOverlay!)
    const trackWrap = document.createElement("div");
    trackWrap.className = "minimax-timeline-track-wrap";

    const ruler = document.createElement("div");
    ruler.className = "minimax-timeline-ruler";
    ruler.innerHTML = `<span>00:00 (Frame 0)</span><span id="ruler-end">00:00 (Frame 0)</span>`;

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
    footer.innerHTML = `
        <div class="minimax-timeline-legend">
            <div class="legend-item"><div class="legend-dot completed"></div>已编辑</div>
            <div class="legend-item"><div class="legend-dot active"></div>当前段</div>
            <div class="legend-item"><div class="legend-dot unprocessed"></div>未编辑</div>
            <div class="legend-item"><div class="legend-dot gap"></div>⚠️漏编</div>
        </div>
        <div class="minimax-timeline-details" id="timeline-details">正在加载状态...</div>
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
                timecodeEl.innerText = `${formatTime(curSec)} / ${formatTime(totalSec)} | 帧: ${curFrame}`;
            }

            // Update playhead position smoothly
            if (totalSec > 0) {
                const pct = Math.min(100, Math.max(0, (curSec / totalSec) * 100));
                playhead.style.left = `${pct}%`;
            }

            // Loop active chunk boundary guard (with isSeeking lock to prevent CPU freeze)
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
            if (playBtn) playBtn.innerHTML = "▶ 播放";
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
            // Reassembler node: Trace upstream Slicer connected via slice_context or graph
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
                            details.innerHTML = isConnected
                                ? `<span>⏳ 项目 [${projectName}] 已连接，运行后自动显示各段拼接进度</span>`
                                : `<span>🔗 请将切片器 (Slicer) 的 slice_context 连入此节点</span>`;
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
                placeholder.innerHTML = "<span>🎞️ 请在上方下拉框选择视频文件</span>";
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
            statsBadge.innerText = `${covPct}% 已组装`;
            statsBadge.className = `minimax-timeline-stats-badge ${cachedMeta.is_fully_assembled ? 'all-done' : (gaps.length > 0 && covPct > 0 ? 'has-gap' : '')}`;
        }
        const progressFill = container.querySelector("#progress-fill");
        if (progressFill) {
            progressFill.style.width = `${covPct}%`;
            progressFill.className = `minimax-progress-fill ${covPct >= 100 ? 'done' : (gaps.length > 0 && covPct > 0 ? 'gap' : '')}`;
        }
        if (rulerEnd) {
            rulerEnd.innerText = `${formatTime(totalSec)} (Frame ${totalFrames})`;
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

            block.title = `分段 #${cIdx} (${c.start_frame}~${c.end_frame}帧, ${stTime}~${edTime})\n点击选中并在上方播放此段`;

            block.onclick = (e) => {
                e.stopPropagation();
                selectedChunkIdx = cIdx;
                if (chunkIndexWidget) {
                    chunkIndexWidget.value = cIdx;
                    if (chunkIndexWidget.callback) chunkIndexWidget.callback(cIdx);
                }
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
                    details.innerText = `选中 #${activeIdx}: ${sf}~${ef}帧 (${formatTime(sf/fps)}~${formatTime(ef/fps)}) | 长度: ${ef - sf}帧 (${dur.toFixed(2)}s)`;
                }
            } else {
                const doneCount = completedIndices.size;
                const totalCount = chunks.length;
                if (covPct >= 100) {
                    details.innerHTML = `<span style="color:#10b981;font-weight:bold;">🎉 全长视频已 100% 拼接完成！所有分段均已缝合到位。可点击右上角【🎬 导出全片 MP4】。</span>`;
                } else {
                    const activeChunk = chunks.find(c => c.chunk_index === activeIdx);
                    const selInfo = activeChunk ? ` | 选中 #${activeIdx} (${activeChunk.status === 'completed' ? '已回填' : '未回填'})` : '';
                    details.innerText = `总装进度: ${covPct}% (已缝合 ${doneCount}/${totalCount} 段)${selInfo}`;
                }
            }
        }
    }

    async function exportMasterVideo(targetBtn) {
        const projectName = getResolvedProjectName();
        const origHtml = targetBtn.innerHTML;
        targetBtn.innerHTML = "⏳ 正在压制导出...";
        targetBtn.disabled = true;

        try {
            const res = await api.fetchApi("/minimax/timeline/export_video", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project: projectName, crf: 18, preset: "fast" })
            });
            const data = await res.json();
            if (data.success) {
                alert(`🎉 恭喜！长视频完整成品已导出成功！\n\n` +
                      `📁 文件名: ${data.file_name}\n` +
                      `⏱️ 时长: ${data.duration} 秒 (${data.total_frames} 帧 @ ${data.fps}fps)\n` +
                      `📐 分辨率: ${data.width}x${data.height}\n` +
                      `💾 文件大小: ${data.file_size_mb} MB\n` +
                      `🎵 包含音频: ${data.has_audio ? '是' : '否'}\n\n` +
                      `已保存至 ComfyUI 输出目录:\n${data.file_path}`);
            } else {
                alert(`⚠️ 导出未完成: ${data.error || "未知错误"}`);
            }
        } catch (err) {
            alert(`⚠️ 导出请求异常: ${err.message}`);
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
            alert("🎉 全片所有分段均已编辑完成！无未完成或遗漏段落。");
        }
    }

    async function resetActiveChunk() {
        const projectName = getResolvedProjectName();
        const chunkIndexWidget = node.widgets?.find(w => w.name === "chunk_index");
        const activeIdx = chunkIndexWidget ? parseInt(chunkIndexWidget.value || 0, 10) : selectedChunkIdx;

        if (!confirm(`确定重置分段 #${activeIdx} 的编辑状态吗？`)) return;

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

    // Attach callbacks to relevant ComfyUI widgets (lightweight, zero setDirtyCanvas!)
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
