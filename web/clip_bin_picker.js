import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { t, getLocale } from "./i18n.js";

/**
 * MiniMax H3 Clip Bin - Visual Non-Linear Timeline & Interactive Card Deck Extension.
 *
 * Implements:
 * - Horizontal carousel of clip cards directly inside MiniMaxClipBinPicker node.
 * - Thumbnail previews (First Frame & Tail Handover Frame).
 * - Interactive 5-star ratings with real-time API persistence.
 * - Click-to-select active continuation source (highlights card, syncs clip_selection widget).
 * - Special Auto / Initial mode card.
 * - Auto-refresh on generation completion.
 * - Multi-language (i18n) support for Japanese, English, and Chinese.
 */

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
}

// Inject CSS stylesheet into page header
const styleId = "minimax-clip-bin-styles";
if (!document.getElementById(styleId)) {
    const link = document.createElement("link");
    link.id = styleId;
    link.rel = "stylesheet";
    link.type = "text/css";
    link.href = new URL("./clip_bin_picker.css", import.meta.url).href;
    document.head.appendChild(link);
}

app.registerExtension({
    name: "MiniMaxH3.ClipBinPicker",

    async beforeRegisterNodeDef(nodeType, nodeData, appInstance) {
        if (nodeData.name !== "MiniMaxClipBinPicker" && nodeData.name !== "MiniMaxClipBinTreePicker") {
            return;
        }

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            this.imgs = null;
            setupClipBinPickerWidget(this);
            return r;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
            this.imgs = null;
            return r;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const r = onExecuted ? onExecuted.apply(this, arguments) : undefined;
            // Prevent ComfyUI from displaying default preview image in this picker node
            this.imgs = null;
            return r;
        };

        nodeType.prototype.setSizeForImage = function () {
            // Prevent auto-resizing picker node for image previews
        };
    }
});

function setupClipBinPickerWidget(node) {
    let closeCurrentModal = null;
    // Find relevant widgets
    const projectWidget = node.widgets?.find(w => w.name === "project_name");
    const selectionWidget = node.widgets?.find(w => w.name === "clip_selection");
    const ratingFilterWidget = node.widgets?.find(w => w.name === "filter_rating");
    const viewModeWidget = node.widgets?.find(w => w.name === "view_mode");

    function projectName() {
        const index = node.inputs?.findIndex(input => input.name === "project_name") ?? -1;
        const source = index >= 0 ? node.getInputNode?.(index) : null;
        const value = source?.widgets?.find(w => w.name === "value")?.value;
        return value ?? projectWidget?.value ?? "Default_Project";
    }

    const isTreePickerNode = node.type === "MiniMaxClipBinTreePicker" || node.comfyClass === "MiniMaxClipBinTreePicker";
    let currentView = node.properties?.clip_bin_view || (isTreePickerNode ? "tree" : (viewModeWidget?.value?.toLowerCase?.().includes("tree") ? "tree" : "deck"));

    // Container DOM
    const container = document.createElement("div");
    container.className = "minimax-clip-bin-container";

    // Header
    const header = document.createElement("div");
    header.className = "minimax-clip-bin-header";

    const titleWrap = document.createElement("div");
    titleWrap.className = "minimax-clip-bin-title";
    const headerTitleText = getLocale() === "ja" ? "🎞️ MiniMax クリッププール:" : (getLocale() === "zh" ? "🎞️ MiniMax 项目素材库:" : "🎞️ MiniMax Project Clip Bin:");
    titleWrap.innerHTML = `${headerTitleText} <span class="minimax-clip-bin-project-tag">${escapeHTML(projectName())}</span>`;

    const actionsWrap = document.createElement("div");
    actionsWrap.className = "minimax-clip-bin-actions";

    const refreshBtn = document.createElement("button");
    refreshBtn.className = "minimax-clip-bin-refresh-btn";
    refreshBtn.innerText = "🔄 " + t("common.refresh");
    refreshBtn.title = t("picker.refresh_tooltip");
    actionsWrap.appendChild(refreshBtn);

    const rescanBtn = document.createElement("button");
    rescanBtn.className = "minimax-clip-bin-rescan-btn";
    const rescanText = getLocale() === "ja" ? "🔍 インデックス再構築" : (getLocale() === "zh" ? "🔍 重建索引" : "🔍 Rebuild Index");
    const rescanningText = getLocale() === "ja" ? "再構築中..." : (getLocale() === "zh" ? "扫描中..." : "Rebuilding...");
    const rescanTooltip = getLocale() === "ja" ? "ディスクを再走査してクリッププールのインデックスを修復します" : (getLocale() === "zh" ? "重新扫描磁盘目录并修复素材库索引" : "Rescan directory and rebuild clip pool index");
    rescanBtn.innerText = rescanText;
    rescanBtn.title = rescanTooltip;
    rescanBtn.onclick = async (e) => {
        e.stopPropagation();
        rescanBtn.disabled = true;
        rescanBtn.innerText = rescanningText;
        try {
            await api.fetchApi("/minimax/clip_bin/rescan", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project: projectName() })
            });
            await loadClips();
        } catch (err) {
            console.error("[Clip Bin] Failed to rescan:", err);
        } finally {
            rescanBtn.disabled = false;
            rescanBtn.innerText = rescanText;
        }
    };
    actionsWrap.appendChild(rescanBtn);

    // View mode toggle [🎴 カード一覧 | 🌳 系統ツリー]
    const viewToggle = document.createElement("div");
    viewToggle.className = "minimax-view-toggle";

    const deckBtn = document.createElement("button");
    deckBtn.className = "minimax-view-btn" + (currentView === "deck" ? " active" : "");
    deckBtn.innerText = t("picker.view_deck");
    deckBtn.title = t("picker.view_deck_tooltip");

    const treeBtn = document.createElement("button");
    treeBtn.className = "minimax-view-btn" + (currentView === "tree" ? " active" : "");
    treeBtn.innerText = t("picker.view_tree");
    treeBtn.title = t("picker.view_tree_tooltip");

    viewToggle.appendChild(deckBtn);
    viewToggle.appendChild(treeBtn);
    actionsWrap.appendChild(viewToggle);

    header.appendChild(titleWrap);
    header.appendChild(actionsWrap);
    container.appendChild(header);

    // Deck carousel
    const deck = document.createElement("div");
    deck.className = "minimax-clip-bin-deck";
    deck.style.display = currentView === "deck" ? "flex" : "none";
    container.appendChild(deck);

    // Tree Lineage View Container
    const treeContainer = document.createElement("div");
    treeContainer.className = "minimax-clip-bin-tree-container";
    treeContainer.style.display = currentView === "tree" ? "block" : "none";
    container.appendChild(treeContainer);

    // Drag-to-pan support for tree canvas
    let isDraggingTree = false;
    let dragStartX = 0, dragStartY = 0, dragScrollLeft = 0, dragScrollTop = 0;
    treeContainer.addEventListener("mousedown", (e) => {
        if (e.target?.closest && (e.target.closest(".minimax-tree-node") || e.target.closest("button"))) return;
        isDraggingTree = true;
        if (treeContainer.classList?.add) treeContainer.classList.add("dragging");
        dragStartX = e.pageX - (treeContainer.offsetLeft || 0);
        dragStartY = e.pageY - (treeContainer.offsetTop || 0);
        dragScrollLeft = treeContainer.scrollLeft || 0;
        dragScrollTop = treeContainer.scrollTop || 0;
    });
    treeContainer.addEventListener("mouseleave", () => {
        isDraggingTree = false;
        if (treeContainer.classList?.remove) treeContainer.classList.remove("dragging");
    });
    treeContainer.addEventListener("mouseup", () => {
        isDraggingTree = false;
        if (treeContainer.classList?.remove) treeContainer.classList.remove("dragging");
    });
    treeContainer.addEventListener("mousemove", (e) => {
        if (!isDraggingTree) return;
        if (e.preventDefault) e.preventDefault();
        const x = e.pageX - (treeContainer.offsetLeft || 0);
        const y = e.pageY - (treeContainer.offsetTop || 0);
        treeContainer.scrollLeft = dragScrollLeft - (x - dragStartX) * 1.5;
        treeContainer.scrollTop = dragScrollTop - (y - dragStartY) * 1.5;
    });

    function switchView(mode) {
        currentView = mode;
        if (!node.properties) node.properties = {};
        node.properties.clip_bin_view = mode;
        // Keep exact internal enum strings for Python node compatibility
        if (viewModeWidget) {
            viewModeWidget.value = mode === "tree" ? "Tree (关系树)" : "Deck (卡片流)";
            viewModeWidget.callback?.(viewModeWidget.value);
        }
        deckBtn.className = "minimax-view-btn" + (mode === "deck" ? " active" : "");
        treeBtn.className = "minimax-view-btn" + (mode === "tree" ? " active" : "");
        deck.style.display = mode === "deck" ? "flex" : "none";
        treeContainer.style.display = mode === "tree" ? "block" : "none";

        loadClips();
    }
    deckBtn.onclick = (e) => { e.stopPropagation(); switchView("deck"); };
    treeBtn.onclick = (e) => { e.stopPropagation(); switchView("tree"); };

    // Footer bar
    const footer = document.createElement("div");
    footer.className = "minimax-clip-bin-footer";
    const selectionInfo = document.createElement("div");
    selectionInfo.className = "minimax-clip-bin-selection-info";
    const selectLabel = getLocale() === "ja" ? "選択中クリップ:" : (getLocale() === "zh" ? "选中镜头:" : "Selected Clip:");
    selectionInfo.innerHTML = `${selectLabel} <span class="minimax-clip-bin-selected-target">${escapeHTML(selectionWidget?.value || "latest")}</span>`;

    const hintText = document.createElement("div");
    hintText.innerText = "👉 " + t("picker.select_as_relay");
    footer.appendChild(selectionInfo);
    footer.appendChild(hintText);
    container.appendChild(footer);

    // Add DOM widget to node
    const widget = node.addDOMWidget("clip_bin_gallery", "gallery", container, {
        serialize: false,
        hideOnZoom: false,
        getMinWidth: () => 480,
    });

    node.imgs = null;

    // Ensure sufficient dimensions
    if (isTreePickerNode) {
        if (node.size[0] < 640) {
            node.setSize([680, 440]);
        }
    } else if (node.size[1] > 600 && node.size[0] <= 560) {
        node.setSize([node.size[0] < 500 ? 520 : node.size[0], 380]);
    } else if (node.size[0] < 500) {
        node.setSize([520, Math.max(node.size[1], 360)]);
    }

    // Function to render stars
    function renderStars(rating, clipId, projectName, onRated) {
        const starWrap = document.createElement("div");
        starWrap.className = "minimax-clip-rating";
        const starTooltip = (i) => getLocale() === "ja" ? `評価: ${i} ★ (クリックで変更)` : (getLocale() === "zh" ? `评级: ${i} 星 (点击修改)` : `Rating: ${i} Stars (click to change)`);
        for (let i = 1; i <= 5; i++) {
            const star = document.createElement("span");
            star.className = `minimax-clip-star ${i <= rating ? "filled" : "empty"}`;
            star.innerText = "★";
            star.title = starTooltip(i);
            star.onclick = async (e) => {
                e.stopPropagation();
                try {
                    const resp = await api.fetchApi("/minimax/clip_bin/rate", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ project: projectName, clip_id: clipId, rating: i })
                    });
                    if (resp.ok && (await resp.json()).success) {
                        const stars = starWrap.querySelectorAll(".minimax-clip-star");
                        stars.forEach((s, idx) => {
                            if (idx + 1 <= i) {
                                s.className = "minimax-clip-star filled";
                            } else {
                                s.className = "minimax-clip-star empty";
                            }
                        });
                        onRated?.(i);
                    }
                } catch (err) {
                    console.error("[Clip Bin] Failed to update rating:", err);
                }
            };
            starWrap.appendChild(star);
        }
        return starWrap;
    }

    // Function to open full-featured audio/video modal preview
    function openVideoModal(clip, projectName) {
        const existing = document.getElementById("minimax-video-modal-overlay");
        if (existing) {
            existing.closePreview?.();
            existing.remove();
        }

        const overlay = document.createElement("div");
        overlay.id = "minimax-video-modal-overlay";
        overlay.className = "minimax-video-modal-overlay";

        const modal = document.createElement("div");
        modal.className = "minimax-video-modal";

        // Modal Header
        const mHeader = document.createElement("div");
        mHeader.className = "minimax-modal-header";
        mHeader.innerHTML = `
            <div class="minimax-modal-title">
                <span class="minimax-modal-shot-title">🎬 ${escapeHTML(clip.shot_tag || "Shot")}</span>
                <span class="minimax-modal-clip-id">${escapeHTML(clip.clip_id)}</span>
            </div>
            <button class="minimax-modal-close-btn" title="${t("common.close")} (Esc)">✕</button>
        `;

        // Modal Body
        const mBody = document.createElement("div");
        mBody.className = "minimax-modal-body";

        const videoWrap = document.createElement("div");
        videoWrap.className = "minimax-modal-video-wrap";
        const video = document.createElement("video");
        video.className = "minimax-modal-video";
        video.src = clip.video_url;
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        videoWrap.appendChild(video);

        const metaLabels = {
            title: t("picker.clip_details"),
            project: getLocale() === "ja" ? "プロジェクト:" : (getLocale() === "zh" ? "归属项目:" : "Project:"),
            specs: getLocale() === "ja" ? "スペック:" : (getLocale() === "zh" ? "规格参数:" : "Specs:"),
            frames: t("common.frames"),
            seconds: t("common.seconds"),
            created: getLocale() === "ja" ? "生成日時:" : (getLocale() === "zh" ? "生成时间:" : "Created:"),
            unknown: getLocale() === "ja" ? "不明" : (getLocale() === "zh" ? "未知" : "Unknown"),
            rating: getLocale() === "ja" ? "品質評価:" : (getLocale() === "zh" ? "品质评级:" : "Rating:"),
            parent: getLocale() === "ja" ? "親クリップ:" : (getLocale() === "zh" ? "父镜头血缘:" : "Parent:"),
            prompt: getLocale() === "ja" ? "プロンプト (Prompt):" : (getLocale() === "zh" ? "正向描述词 (Prompt):" : "Prompt:"),
        };

        const metaPanel = document.createElement("div");
        metaPanel.className = "minimax-modal-meta";
        metaPanel.innerHTML = `
            <div class="minimax-modal-meta-title">${metaLabels.title}</div>
            <div class="minimax-modal-row">
                <span class="label">${metaLabels.project}</span>
                <span class="value">${escapeHTML(projectName)}</span>
            </div>
            <div class="minimax-modal-row">
                <span class="label">${metaLabels.specs}</span>
                <span class="value">${escapeHTML(clip.frames || 124)} ${metaLabels.frames} | ${escapeHTML(clip.duration_seconds || 5.2)} ${metaLabels.seconds} (${escapeHTML(clip.fps || 24)} fps)</span>
            </div>
            <div class="minimax-modal-row">
                <span class="label">${metaLabels.created}</span>
                <span class="value">${escapeHTML(clip.created_at || metaLabels.unknown)}</span>
            </div>
            <div class="minimax-modal-row">
                <span class="label">${metaLabels.rating}</span>
                <span class="value" id="minimax-modal-stars-container"></span>
            </div>
            ${clip.parent_clip_id ? `
            <div class="minimax-modal-row">
                <span class="label">${metaLabels.parent}</span>
                <span class="value parent-link" title="${escapeHTML(clip.parent_clip_id)}">${escapeHTML(clip.parent_clip_id)}</span>
            </div>` : ""}
            ${clip.prompt ? `
            <div class="minimax-modal-prompt-wrap">
                <div class="label">${metaLabels.prompt}</div>
                <div class="prompt-content">${escapeHTML(clip.prompt)}</div>
            </div>` : ""}
        `;

        // Interactive rating inside modal
        const starsContainer = metaPanel.querySelector("#minimax-modal-stars-container");
        if (starsContainer) {
            starsContainer.appendChild(renderStars(clip.rating || 3, clip.clip_id, projectName));
        }

        mBody.appendChild(videoWrap);
        mBody.appendChild(metaPanel);

        // Modal Footer
        const mFooter = document.createElement("div");
        mFooter.className = "minimax-modal-footer";

        const selectBtn = document.createElement("button");
        selectBtn.className = "minimax-modal-select-btn";
        selectBtn.innerHTML = t("picker.select_as_relay");
        selectBtn.onclick = () => {
            if (selectionWidget) {
                selectionWidget.value = clip.clip_id;
                selectionWidget.callback?.(selectionWidget.value);
            }
            updateSelectionDisplay(clip.shot_tag ? `${clip.shot_tag} (${clip.clip_id.slice(-8)})` : clip.clip_id);
            closeModal();
            loadClips();
        };

        const closeBtn = document.createElement("button");
        closeBtn.className = "minimax-modal-cancel-btn";
        closeBtn.innerText = t("common.close");
        closeBtn.onclick = closeModal;

        const modalDelBtn = document.createElement("button");
        modalDelBtn.className = "minimax-modal-delete-btn";
        modalDelBtn.innerHTML = "🗑️ " + (getLocale() === "ja" ? "クリップを削除" : (getLocale() === "zh" ? "删除镜头" : "Delete Clip"));
        modalDelBtn.onclick = async () => {
            const confirmMsg = getLocale() === "ja" ?
                `クリップ「${clip.shot_tag || clip.clip_id}」および関連ファイルを完全に削除してもよろしいですか？\nこの操作は元に戻せません。` :
                (getLocale() === "zh" ?
                    `确定彻底删除镜头 "${clip.shot_tag || clip.clip_id}" 及其所有媒体文件吗？\n此操作无法撤销。` :
                    `Are you sure you want to completely delete clip "${clip.shot_tag || clip.clip_id}" and its media files?\nThis cannot be undone.`);
            const ok = confirm(confirmMsg);
            if (!ok) return;
            try {
                const resp = await api.fetchApi("/minimax/clip_bin/delete", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ project: projectName, clip_id: clip.clip_id })
                });
                if (resp.ok && (await resp.json()).success) {
                    closeModal();
                    loadClips();
                }
            } catch (err) {
                console.error("[Clip Bin] Modal failed to delete clip:", err);
            }
        };

        mFooter.appendChild(modalDelBtn);
        mFooter.appendChild(selectBtn);
        mFooter.appendChild(closeBtn);

        modal.appendChild(mHeader);
        modal.appendChild(mBody);
        modal.appendChild(mFooter);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        function closeModal() {
            video.pause();
            video.src = "";
            overlay.classList.add("closing");
            setTimeout(() => overlay.remove(), 180);
            window.removeEventListener("keydown", onKeyDown);
            closeCurrentModal = null;
        }
        closeCurrentModal = closeModal;
        overlay.closePreview = closeModal;

        function onKeyDown(e) {
            if (e.key === "Escape") {
                closeModal();
            }
        }
        window.addEventListener("keydown", onKeyDown);

        mHeader.querySelector(".minimax-modal-close-btn").onclick = closeModal;
        overlay.onclick = (e) => {
            if (e.target === overlay) closeModal();
        };
    }

    // Function to load and render clips
    let disposed = false;
    let loadVersion = 0;
    async function loadClips() {
        if (disposed) return;
        const version = ++loadVersion;
        const currentProject = projectName();
        const currentSelection = (selectionWidget?.value || "latest").trim();
        titleWrap.innerHTML = `${headerTitleText} <span class="minimax-clip-bin-project-tag">${escapeHTML(currentProject)}</span>`;

        try {
            const res = await api.fetchApi(`/minimax/clip_bin/list?project=${encodeURIComponent(currentProject)}`);
            if (disposed || version !== loadVersion) return;
            if (!res.ok) {
                const connMsg = getLocale() === "ja" ? "バックエンドサービスに接続できないか、プールが空です" : (getLocale() === "zh" ? "未连接到后台服务或素材库为空" : "Backend service unavailable or clip pool empty");
                deck.innerHTML = `<div style="padding: 10px; color: #94a3b8; font-size: 11px;">${connMsg}</div>`;
                return;
            }
            const data = await res.json();
            if (disposed || version !== loadVersion) return;
            const clips = data.clips || [];

            // Filter clips by rating if applicable
            let minRating = 1;
            const rfVal = ratingFilterWidget?.value || "";
            if (rfVal.includes("⭐⭐⭐⭐⭐")) minRating = 5;
            else if (rfVal.includes("⭐⭐⭐⭐")) minRating = 4;
            else if (rfVal.includes("⭐⭐⭐")) minRating = 3;

            const filteredClips = clips.filter(c => (c.rating || 3) >= minRating);

            if (currentView === "tree") {
                deck.style.display = "none";
                treeContainer.style.display = "block";
                renderTree(filteredClips, currentSelection, currentProject);
            } else {
                deck.style.display = "flex";
                treeContainer.style.display = "none";
                renderDeck(filteredClips, currentSelection, currentProject);
            }
        } catch (e) {
            console.warn("[Clip Bin] Error loading clips:", e);
        }
    }

    function renderDeck(filteredClips, currentSelection, currentProject) {
        deck.innerHTML = "";

        // 1. Always append "Auto / Initial" Special Card
        const autoCard = document.createElement("div");
        const isAutoActive = currentSelection.toLowerCase() === "latest" || currentSelection.toLowerCase() === "auto" || currentSelection === "";
        autoCard.className = `minimax-clip-card auto-card ${isAutoActive ? "active" : ""}`;
        const autoTitle = getLocale() === "ja" ? "✨ Auto (自動継続 / 最新)" : (getLocale() === "zh" ? "✨ Auto / 自动最新" : "✨ Auto (Latest)");
        const autoSub = getLocale() === "ja" ? "初回は新規 / 以降は自動継続" : (getLocale() === "zh" ? "首段开辟 / 持续自动" : "Initial fresh / Auto relay");
        const autoDesc = getLocale() === "ja" ? "スマート連携 | 手動設定不要" : (getLocale() === "zh" ? "智能递推 | 零手动配置" : "Smart relay | Zero config");
        autoCard.innerHTML = `
            <div class="minimax-clip-thumb-wrap">
                <div class="minimax-clip-thumb-placeholder">⚡</div>
                ${isAutoActive ? `<div class="minimax-clip-active-badge">${t("picker.current_relay_source")}</div>` : ""}
            </div>
            <div class="minimax-clip-body">
                <div class="minimax-clip-shot-name">${autoTitle}</div>
                <div class="minimax-clip-metrics">
                    <span>${autoSub}</span>
                </div>
                <div class="minimax-clip-lineage">${autoDesc}</div>
            </div>
        `;
        autoCard.onclick = () => {
            if (selectionWidget) {
                selectionWidget.value = "latest";
                selectionWidget.callback?.(selectionWidget.value);
            }
            updateSelectionDisplay("latest");
            loadClips();
        };
        deck.appendChild(autoCard);

        if (filteredClips.length === 0) {
            const emptyMsg = document.createElement("div");
            emptyMsg.style.cssText = "padding: 20px 10px; color: #64748b; font-size: 11px; white-space: nowrap;";
            emptyMsg.innerText = t("picker.empty_pool");
            deck.appendChild(emptyMsg);
            return;
        }

        filteredClips.forEach(clip => {
            const card = document.createElement("div");
            const isActive = currentSelection === clip.clip_id;
            card.className = `minimax-clip-card ${isActive ? "active" : ""}`;

            // Thumbnail
            const thumbWrap = document.createElement("div");
            thumbWrap.className = "minimax-clip-thumb-wrap";

            if (clip.thumbnail_url) {
                const img = document.createElement("img");
                img.className = "minimax-clip-thumb";
                img.src = clip.thumbnail_url;
                img.loading = "lazy";
                img.onerror = () => {
                    thumbWrap.innerHTML = `<div class="minimax-clip-thumb-placeholder">🎬</div>`;
                };
                thumbWrap.appendChild(img);
            } else {
                thumbWrap.innerHTML = `<div class="minimax-clip-thumb-placeholder">🎬</div>`;
            }

            // Video badge & play trigger
            if (clip.has_video && clip.video_url) {
                const vidBadge = document.createElement("div");
                vidBadge.className = "minimax-clip-video-badge";
                vidBadge.innerHTML = "▶ MP4";
                vidBadge.title = getLocale() === "ja" ? "クリックで動画を再生" : (getLocale() === "zh" ? "点击全屏视听播放" : "Click to play video");
                vidBadge.onclick = (e) => {
                    e.stopPropagation();
                    openVideoModal(clip, currentProject);
                };
                thumbWrap.appendChild(vidBadge);

                const playOverlay = document.createElement("button");
                playOverlay.className = "minimax-clip-play-overlay";
                playOverlay.innerHTML = "▶";
                playOverlay.title = getLocale() === "ja" ? "動画を再生" : (getLocale() === "zh" ? "视听播放" : "Play video");
                playOverlay.onclick = (e) => {
                    e.stopPropagation();
                    openVideoModal(clip, currentProject);
                };
                thumbWrap.appendChild(playOverlay);

                // Hover-to-Play dynamic preview
                let hoverVideo = null;
                let hoverTimer = null;

                card.addEventListener("mouseenter", () => {
                    hoverTimer = setTimeout(() => {
                        if (!hoverVideo) {
                            hoverVideo = document.createElement("video");
                            hoverVideo.className = "minimax-clip-hover-video";
                            hoverVideo.src = clip.video_url;
                            hoverVideo.muted = true;
                            hoverVideo.loop = true;
                            hoverVideo.playsInline = true;
                            hoverVideo.autoplay = true;
                            thumbWrap.appendChild(hoverVideo);
                        }
                        hoverVideo.play().catch(() => {});
                        hoverVideo.style.opacity = "1";
                    }, 180);
                });

                card.addEventListener("mouseleave", () => {
                    if (hoverTimer) {
                        clearTimeout(hoverTimer);
                        hoverTimer = null;
                    }
                    if (hoverVideo) {
                        hoverVideo.pause();
                        hoverVideo.style.opacity = "0";
                        const vRef = hoverVideo;
                        hoverVideo = null;
                        setTimeout(() => {
                            if (vRef && vRef.parentNode) {
                                vRef.remove();
                            }
                        }, 200);
                    }
                });

                // Double click to open full video modal
                card.ondblclick = (e) => {
                    e.stopPropagation();
                    openVideoModal(clip, currentProject);
                };
            }

            if (isActive) {
                const badge = document.createElement("div");
                badge.className = "minimax-clip-active-badge";
                badge.innerText = t("picker.current_relay_source");
                thumbWrap.appendChild(badge);
            }
            card.appendChild(thumbWrap);

            // Delete button on card
            const delBtn = document.createElement("button");
            delBtn.className = "minimax-clip-delete-btn";
            delBtn.innerHTML = "✕";
            delBtn.title = t("picker.delete_shot");
            delBtn.onclick = async (e) => {
                e.stopPropagation();
                const delConfirmMsg = getLocale() === "ja" ?
                    `クリップ「${clip.shot_tag || clip.clip_id}」およびファイルを削除してもよろしいですか？\nこの操作は元に戻せません。` :
                    (getLocale() === "zh" ?
                        `确定彻底删除镜头 "${clip.shot_tag || clip.clip_id}" 及其媒体文件吗？\n此操作无法撤销。` :
                        `Are you sure you want to delete clip "${clip.shot_tag || clip.clip_id}"?\nThis cannot be undone.`);
                const ok = confirm(delConfirmMsg);
                if (!ok) return;
                try {
                    const resp = await api.fetchApi("/minimax/clip_bin/delete", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ project: currentProject, clip_id: clip.clip_id })
                    });
                    if (resp.ok && (await resp.json()).success) {
                        card.style.opacity = "0";
                        card.style.transform = "scale(0.8)";
                        setTimeout(() => {
                            card.remove();
                            if (currentSelection === clip.clip_id && selectionWidget) {
                                selectionWidget.value = "latest";
                                selectionWidget.callback?.(selectionWidget.value);
                                updateSelectionDisplay("latest");
                            }
                        }, 200);
                    }
                } catch (err) {
                    console.error("[Clip Bin] Failed to delete clip:", err);
                }
            };
            thumbWrap.appendChild(delBtn);

            // Body
            const body = document.createElement("div");
            body.className = "minimax-clip-body";

            // Stars
            body.appendChild(renderStars(clip.rating || 3, clip.clip_id, currentProject, (newR) => {
                clip.rating = newR;
            }));

            // Shot tag
            const shotName = document.createElement("div");
            shotName.className = "minimax-clip-shot-name";
            shotName.innerText = clip.shot_tag || "Shot";
            shotName.title = `${clip.shot_tag} (${clip.clip_id})`;
            body.appendChild(shotName);

            // Metrics
            const metrics = document.createElement("div");
            metrics.className = "minimax-clip-metrics";
            const frameUnit = getLocale() === "ja" ? "F" : (getLocale() === "zh" ? "帧" : "F");
            metrics.innerHTML = `<span>${escapeHTML(clip.frames || 124)}${frameUnit}</span><span>${escapeHTML(clip.duration_seconds || 5.2)}s</span>`;
            body.appendChild(metrics);

            // Lineage / Parent
            if (clip.parent_clip_id) {
                const lineage = document.createElement("div");
                lineage.className = "minimax-clip-lineage";
                const parentPrefix = getLocale() === "ja" ? "↳ 派生元:" : (getLocale() === "zh" ? "↳ 衍生自:" : "↳ Parent:");
                const parentTooltip = getLocale() === "ja" ? "親クリップ:" : (getLocale() === "zh" ? "父镜头:" : "Parent clip:");
                lineage.innerText = `${parentPrefix} ${clip.parent_clip_id.slice(-8)}`;
                lineage.title = `${parentTooltip} ${clip.parent_clip_id}`;
                body.appendChild(lineage);
            }

            card.appendChild(body);

            // Click to select
            card.onclick = () => {
                if (selectionWidget) {
                    selectionWidget.value = clip.clip_id;
                    selectionWidget.callback?.(selectionWidget.value);
                }
                updateSelectionDisplay(clip.shot_tag ? `${clip.shot_tag} (${clip.clip_id.slice(-8)})` : clip.clip_id);
                loadClips();
            };

            deck.appendChild(card);
        });
    }

    function renderTree(filteredClips, currentSelection, currentProject) {
        treeContainer.innerHTML = "";

        if (filteredClips.length === 0) {
            const emptyMsg = document.createElement("div");
            emptyMsg.style.cssText = "padding: 30px 10px; color: #64748b; font-size: 11px;";
            emptyMsg.innerText = t("picker.empty_pool");
            treeContainer.appendChild(emptyMsg);
            return;
        }

        // Build Forest
        const clipMap = new Map();
        const roots = [];
        filteredClips.forEach(c => {
            clipMap.set(c.clip_id, { clip: c, id: c.clip_id, parentId: c.parent_clip_id, children: [] });
        });

        clipMap.forEach(item => {
            let parent = null;
            if (item.parentId) {
                parent = clipMap.get(item.parentId);
                if (!parent) {
                    for (const [otherId, otherNode] of clipMap.entries()) {
                        if (otherId.endsWith(item.parentId) || item.parentId.endsWith(otherId)) {
                            parent = otherNode;
                            break;
                        }
                    }
                }
            }
            if (parent && parent !== item) {
                parent.children.push(item);
                item.parent = parent;
            } else {
                roots.push(item);
            }
        });

        // Compute ancestors path for currentSelection
        const pathIds = new Set();
        let curr = clipMap.get(currentSelection);
        while (curr) {
            pathIds.add(curr.id);
            curr = curr.parent;
        }

        // Sort roots chronologically
        roots.sort((a, b) => (a.clip.created_at || a.id).localeCompare(b.clip.created_at || b.id));
        clipMap.forEach(item => {
            item.children.sort((a, b) => (a.clip.created_at || a.id).localeCompare(b.clip.created_at || b.id));
        });

        const forest = document.createElement("div");
        forest.className = "minimax-tree-forest";

        const rootRow = document.createElement("div");
        rootRow.className = "minimax-tree-root-row";

        // Auto / Initial Virtual Card
        const isAutoActive = currentSelection.toLowerCase() === "latest" || currentSelection.toLowerCase() === "auto" || currentSelection === "";
        const autoNode = document.createElement("div");
        autoNode.className = "minimax-tree-auto-node" + (isAutoActive ? " active-relay" : "");
        const autoTreeTitle = getLocale() === "ja" ? "Auto 初回クリップ" : (getLocale() === "zh" ? "Auto 初始镜头" : "Auto Initial Clip");
        const autoTreeDesc = isAutoActive ?
            t("picker.current_relay_source") :
            (getLocale() === "ja" ? "クリックで初回新規に設定" : (getLocale() === "zh" ? "点击设为首段全新" : "Click to set initial"));
        autoNode.innerHTML = `
            <div class="minimax-tree-auto-icon">⚡</div>
            <div class="minimax-tree-auto-title">${autoTreeTitle}</div>
            <div class="minimax-tree-auto-desc">${autoTreeDesc}</div>
        `;
        autoNode.onclick = () => {
            if (selectionWidget) {
                selectionWidget.value = "latest";
                selectionWidget.callback?.(selectionWidget.value);
            }
            updateSelectionDisplay("latest");
            loadClips();
        };
        rootRow.appendChild(autoNode);

        // Recursive Branch Builder
        function buildBranchDOM(nodeItem) {
            const clip = nodeItem.clip;
            const branch = document.createElement("div");
            const isActive = currentSelection === clip.clip_id;
            const isInPath = pathIds.has(clip.clip_id);
            const hasChildren = nodeItem.children.length > 0;

            branch.className = "minimax-tree-branch" +
                (hasChildren ? " has-children" : "") +
                (isInPath ? " in-path" : "");

            const nodeWrap = document.createElement("div");
            nodeWrap.className = "minimax-tree-node-wrap";

            const card = document.createElement("div");
            card.className = "minimax-tree-node" +
                (isActive ? " active-relay" : "") +
                (isInPath && !isActive ? " in-path" : "");

            // Thumbnail
            const thumb = document.createElement("div");
            thumb.className = "minimax-tree-node-thumb";
            if (clip.thumbnail_url) {
                const img = document.createElement("img");
                img.src = clip.thumbnail_url;
                img.loading = "lazy";
                img.onerror = () => {
                    thumb.innerHTML = `<div class="minimax-tree-node-thumb-placeholder">🎬</div>`;
                };
                thumb.appendChild(img);
            } else {
                thumb.innerHTML = `<div class="minimax-tree-node-thumb-placeholder">🎬</div>`;
            }

            // Play overlay on hover if has video
            if (clip.has_video && clip.video_url) {
                const playOverlay = document.createElement("button");
                playOverlay.className = "minimax-clip-play-overlay";
                playOverlay.innerHTML = "▶";
                playOverlay.title = getLocale() === "ja" ? "動画を再生" : (getLocale() === "zh" ? "视听播放" : "Play video");
                playOverlay.onclick = (e) => {
                    e.stopPropagation();
                    openVideoModal(clip, currentProject);
                };
                thumb.appendChild(playOverlay);
                card.ondblclick = (e) => {
                    e.stopPropagation();
                    openVideoModal(clip, currentProject);
                };
            }

            // Delete button on tree card
            const delBtn = document.createElement("button");
            delBtn.className = "minimax-clip-delete-btn";
            delBtn.innerHTML = "✕";
            delBtn.title = t("picker.delete_shot");
            delBtn.onclick = async (e) => {
                e.stopPropagation();
                const delTreeMsg = getLocale() === "ja" ?
                    `クリップ「${clip.shot_tag || clip.clip_id}」およびファイルを削除してもよろしいですか？\nこの操作は元に戻せません。` :
                    (getLocale() === "zh" ?
                        `确定彻底删除镜头 "${clip.shot_tag || clip.clip_id}" 及其媒体文件吗？\n此操作无法撤销。` :
                        `Are you sure you want to delete clip "${clip.shot_tag || clip.clip_id}"?\nThis cannot be undone.`);
                const ok = confirm(delTreeMsg);
                if (!ok) return;
                try {
                    const resp = await api.fetchApi("/minimax/clip_bin/delete", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ project: currentProject, clip_id: clip.clip_id })
                    });
                    if (resp.ok && (await resp.json()).success) {
                        if (currentSelection === clip.clip_id && selectionWidget) {
                            selectionWidget.value = "latest";
                            selectionWidget.callback?.(selectionWidget.value);
                            updateSelectionDisplay("latest");
                        }
                        loadClips();
                    }
                } catch (err) {
                    console.error("[Clip Bin] Failed to delete clip from tree:", err);
                }
            };
            thumb.appendChild(delBtn);
            card.appendChild(thumb);

            // Card body
            const body = document.createElement("div");
            body.className = "minimax-tree-node-body";

            // Title row
            const titleRow = document.createElement("div");
            titleRow.className = "minimax-tree-node-title-row";
            const title = document.createElement("span");
            title.className = "minimax-tree-node-title";
            title.innerText = clip.shot_tag || "Shot";
            title.title = `${clip.shot_tag} (${clip.clip_id})`;
            titleRow.appendChild(title);

            // Stars
            titleRow.appendChild(renderStars(clip.rating || 3, clip.clip_id, currentProject, (newR) => {
                clip.rating = newR;
            }));
            body.appendChild(titleRow);

            // Metrics
            const metrics = document.createElement("div");
            metrics.className = "minimax-tree-node-metrics";
            const frameUnit = getLocale() === "ja" ? "F" : (getLocale() === "zh" ? "帧" : "F");
            metrics.innerHTML = `<span>${escapeHTML(clip.frames || 124)}${frameUnit}</span><span>${escapeHTML(clip.duration_seconds || 5.2)}s</span>`;
            body.appendChild(metrics);

            // Badge
            const badge = document.createElement("div");
            if (isActive) {
                badge.className = "minimax-tree-badge active-badge";
                badge.innerText = t("picker.current_relay_source");
            } else if (isInPath) {
                badge.className = "minimax-tree-badge path-badge";
                badge.innerText = getLocale() === "ja" ? "🔗 メイン系統" : (getLocale() === "zh" ? "🔗 主线节点" : "🔗 Main lineage");
            } else {
                badge.className = "minimax-tree-badge branch-badge";
                const branchLabel = nodeItem.parentId ?
                    (getLocale() === "ja" ? "🌿 派生ブランチ" : (getLocale() === "zh" ? "🌿 衍生分支" : "🌿 Branch")) :
                    (getLocale() === "ja" ? "🌱 初回ルートクリップ" : (getLocale() === "zh" ? "🌱 初始根镜头" : "🌱 Root Clip"));
                badge.innerText = branchLabel;
            }
            body.appendChild(badge);

            card.appendChild(body);

            // Select on click
            card.onclick = () => {
                if (selectionWidget) {
                    selectionWidget.value = clip.clip_id;
                    selectionWidget.callback?.(selectionWidget.value);
                }
                updateSelectionDisplay(clip.shot_tag ? `${clip.shot_tag} (${clip.clip_id.slice(-8)})` : clip.clip_id);
                loadClips();
            };

            nodeWrap.appendChild(card);
            branch.appendChild(nodeWrap);

            // Recursively build children
            if (hasChildren) {
                const childrenContainer = document.createElement("div");
                childrenContainer.className = "minimax-tree-children";
                nodeItem.children.forEach(childItem => {
                    childrenContainer.appendChild(buildBranchDOM(childItem));
                });
                branch.appendChild(childrenContainer);
            }

            return branch;
        }

        // Roots container
        const rootsContainer = document.createElement("div");
        rootsContainer.style.cssText = "display: flex; flex-direction: column; gap: 16px;";
        roots.forEach(rootItem => {
            rootsContainer.appendChild(buildBranchDOM(rootItem));
        });
        rootRow.appendChild(rootsContainer);
        forest.appendChild(rootRow);
        treeContainer.appendChild(forest);
    }

    function updateSelectionDisplay(val) {
        selectionInfo.innerHTML = `${selectLabel} <span class="minimax-clip-bin-selected-target">${escapeHTML(val)}</span>`;
    }

    // Bind refresh button
    refreshBtn.onclick = (e) => {
        e.stopPropagation();
        loadClips();
    };

    // Watch projectWidget changes
    if (projectWidget) {
        const origCallback = projectWidget.callback;
        projectWidget.callback = function (v) {
            const r = origCallback ? origCallback.apply(this, arguments) : undefined;
            loadClips();
            return r;
        };
    }

    // Watch rating filter changes
    if (ratingFilterWidget) {
        const origRfCallback = ratingFilterWidget.callback;
        ratingFilterWidget.callback = function (v) {
            const r = origRfCallback ? origRfCallback.apply(this, arguments) : undefined;
            loadClips();
            return r;
        };
    }

    // Watch clip_selection changes
    if (selectionWidget) {
        const origSelCallback = selectionWidget.callback;
        selectionWidget.callback = function (v) {
            const r = origSelCallback ? origSelCallback.apply(this, arguments) : undefined;
            updateSelectionDisplay(v);
            loadClips();
            return r;
        };
    }

    // Watch view_mode changes
    if (viewModeWidget) {
        const origVmCallback = viewModeWidget.callback;
        viewModeWidget.callback = function (v) {
            const r = origVmCallback ? origVmCallback.apply(this, arguments) : undefined;
            const targetMode = String(v).toLowerCase().includes("tree") ? "tree" : "deck";
            if (targetMode !== currentView) {
                switchView(targetMode);
            }
            return r;
        };
    }

    // Initial load
    setTimeout(loadClips, 200);

    // Auto-refresh when generation execution finishes
    const onExecutedEvent = (e) => {
        if (e.detail?.node === String(node.id) || e.detail?.output?.ui?.images) {
            setTimeout(loadClips, 500);
        }
    };

    const onStatusEvent = (e) => {
        if (e.detail?.exec_info?.queue_remaining === 0) {
            setTimeout(loadClips, 600);
        }
    };
    api.addEventListener("executed", onExecutedEvent);
    api.addEventListener("status", onStatusEvent);
    const previousRemoved = node.onRemoved;
    node.onRemoved = function () {
        disposed = true;
        closeCurrentModal?.();
        api.removeEventListener("executed", onExecutedEvent);
        api.removeEventListener("status", onStatusEvent);
        return previousRemoved?.apply(this, arguments);
    };
}
