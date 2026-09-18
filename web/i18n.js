/**
 * ComfyUI-MiniMaxH3-PrefixStream 多言語対応（i18n）モジュール
 * 日本語 (ja), 英語 (en), 中国語 (zh) をサポート
 */

const translations = {
    ja: {
        // === 共通 ===
        "common.loading": "読み込み中...",
        "common.confirm": "確認",
        "common.cancel": "キャンセル",
        "common.close": "閉じる",
        "common.save": "保存",
        "common.delete": "削除",
        "common.reset": "リセット",
        "common.refresh": "更新",
        "common.copy": "コピー",
        "common.copied": "コピーしました",
        "common.failed": "失敗しました",
        "common.success": "成功しました",
        "common.warning": "警告",
        "common.error": "エラー",
        "common.all": "すべて",
        "common.none": "なし",
        "common.auto": "自動",
        "common.total": "合計",
        "common.frames": "フレーム",
        "common.seconds": "秒",

        // === Clip Bin Picker (クリップ選択・ギャラリー) ===
        "picker.title": "🎬 MiniMax H3 クリップ選択・ギャラリー",
        "picker.view_deck": "🎴 カード一覧",
        "picker.view_tree": "🌳 系統ツリー",
        "picker.view_deck_tooltip": "カード形式で全クリップをサムネイル一覧表示します",
        "picker.view_tree_tooltip": "親クリップと子クリップの派生関係を系統ツリーで表示します",
        "picker.refresh_tooltip": "プールからクリップ一覧を再読み込みします",
        "picker.empty_pool": "プールに保存されたクリップがまだありません。動画を生成するとここに自動登録されます。",
        "picker.pool_not_ready": "クリッププールがまだ初期化されていません。ワークフローを実行してください。",
        "picker.select_as_relay": "🎯 このクリップを継続元に設定",
        "picker.current_relay_source": "⭐ 現在の継続元クリップ",
        "picker.relay_source_set": "クリップ #{id} を継続元に設定しました",
        "picker.generation_info": "生成情報",
        "picker.prompt": "プロンプト",
        "picker.motion_cfg": "Motion CFG",
        "picker.frames_count": "フレーム数: {count}F ({sec}秒)",
        "picker.created_at": "生成日時: {time}",
        "picker.parent_shot": "親クリップ: #{id}",
        "picker.no_parent": "親クリップ: なし（初回生成）",
        "picker.child_shots": "派生クリップ ({count}本)",
        "picker.clip_details": "クリップ詳細",
        "picker.open_in_new_tab": "新しいタブで動画を開く",
        "picker.delete_shot": "このクリップをプールから削除",
        "picker.delete_shot_confirm": "クリップ #{id} をプールから削除してもよろしいですか？（元動画ファイルは削除されません）",
        "picker.search_placeholder": "IDまたはプロンプトで検索...",
        "picker.sort_newest": "新しい順",
        "picker.sort_oldest": "古い順",
        "picker.card_shot_badge": "Clip #{id}",
        "picker.copy_shot_id": "クリップIDをコピー",
        "picker.card_duration": "{sec}秒 / {frames}F",

        // === Video Timeline (動画スマート分割スタジオ / パーツ差し替え・統合タイムライン) ===
        "timeline.workbench_title": "🎬 動画スマート分割スタジオ",
        "timeline.assembler_title": "🧩 動画パーツ差し替え・統合タイムライン",
        "timeline.open_workbench": "🎬 スマート分割スタジオを開く",
        "timeline.open_assembler": "🧩 統合タイムラインを開く",
        "timeline.total_duration": "総再生時間: {duration}秒 / {frames}フレーム",
        "timeline.segments_count": "クリップ数: {count}本",
        "timeline.unrendered_count": "未生成: {count}本",
        "timeline.completed_count": "完了: {count}本",
        "timeline.current_editing": "生成対象クリップ: #{index} ({startSec}s - {endSec}s)",
        "timeline.prev_shot": "⏮️ 前のクリップ",
        "timeline.next_shot": "⏭️ 次のクリップ",
        "timeline.next_unrendered": "🎯 次の未生成クリップ",
        "timeline.reset_current_shot": "↩️ このクリップをリセット",
        "timeline.reset_current_confirm": "クリップ #{index} の生成結果をリセットし、未生成状態に戻しますか？",
        "timeline.export_full_mp4": "🎬 完成動画をMP4出力",
        "timeline.exporting": "MP4動画を出力・結合中...",
        "timeline.export_success": "MP4動画の出力が完了しました！",
        "timeline.export_failed": "動画の出力に失敗しました: {error}",
        "timeline.download_mp4": "📥 完成動画をダウンロード",
        "timeline.segment_info": "クリップ #{index} 情報",
        "timeline.segment_range": "区間: {start}s 〜 {end}s ({frames}フレーム)",
        "timeline.segment_status_unrendered": "⚪ 未生成",
        "timeline.segment_status_rendering": "🟡 生成中...",
        "timeline.segment_status_completed": "🟢 完了",
        "timeline.segment_status_custom": "🟣 外部パーツ差し替え済み",
        "timeline.set_as_current_target": "🎯 このクリップを生成対象に設定",
        "timeline.split_at_cursor": "✂️ 再生位置で分割",
        "timeline.merge_with_next": "🔗 次のクリップと結合",
        "timeline.delete_segment": "🗑️ このクリップ区間を削除",
        "timeline.zoom_in": "🔍 拡大",
        "timeline.zoom_out": "🔍 縮小",
        "timeline.fit_timeline": "↔️ 画面幅に合わせる",
        "timeline.play": "▶️ 再生",
        "timeline.pause": "⏸️ 一時停止",
        "timeline.speed": "速度: {rate}x",
        "timeline.cut_point": "カットポイント: {sec}秒 ({frame}F)",
        "timeline.recommended_length": "💡 1クリップの推奨長は 約5秒（85フレーム）〜 10秒（171フレーム）です",
        "timeline.auto_advance_label": "生成完了時に次の未生成クリップへ自動切替",
        "timeline.session_id": "セッションID: {id}",
        "timeline.source_video": "元動画: {name}",
        "timeline.replace_source": "元動画を差し替え",
        "timeline.save_session": "セッションを保存",
        "timeline.load_session": "セッションを読み込み",

        // === ノード表示名・カテゴリ ===
        "node.prefix_stream_config": "MiniMax H3 継続生成設定",
        "node.apply_prefix_stream": "MiniMax H3 継続生成適用",
        "node.clean_prefix_frames": "MiniMax H3 先頭重複フレーム削除",
        "node.save_to_clip_bin": "MiniMax H3 クリップ保存・プール",
        "node.clip_bin_picker": "MiniMax H3 クリップ選択・ギャラリー",
        "node.smart_video_slicer": "🎬 MiniMax 動画スマート分割スライサー",
        "node.video_timeline_assembler": "🧩 MiniMax 動画パーツ差し替え・統合",

        // === ツールチップ (ノード・パラメータ) ===
        "tooltip.mode": "継続生成（リレー）方式を選択します。\n• Auto (推奨): 初回は新規生成、2本目以降は直前クリップの末尾フレームから自動継続\n• Force Initial: 常に完全新規生成（単発クリップ）\n• Force Sequential: 常に直前クリップの末尾から継続生成\n• Force Specific: 指定した shot_id のクリップから継続生成",
        "tooltip.prefix_frames": "直前クリップから引き継ぐ末尾参照フレーム数です（推奨: 5フレーム = 17k+5の境界）。",
        "tooltip.blend_mode": "継続接合部のブレンド方式を選択します。\n• Native Masked AV (推奨): ComfyUI標準のマスクドAV方式で劣化なし\n• Motion Context: 従来のモーションコンテキスト方式",
        "tooltip.pool_id": "クリップを管理・保存するプールの一意識別子です。ワークフロー単位で独立したプールを持てます。",
        "tooltip.target_shot": "操作または継続元として指定するクリップのIDです。",
        "tooltip.auto_advance": "動画生成が完了した際、自動的に次の未生成クリップを選択状態にします。"
    },

    en: {
        // === Common ===
        "common.loading": "Loading...",
        "common.confirm": "Confirm",
        "common.cancel": "Cancel",
        "common.close": "Close",
        "common.save": "Save",
        "common.delete": "Delete",
        "common.reset": "Reset",
        "common.refresh": "Refresh",
        "common.copy": "Copy",
        "common.copied": "Copied",
        "common.failed": "Failed",
        "common.success": "Success",
        "common.warning": "Warning",
        "common.error": "Error",
        "common.all": "All",
        "common.none": "None",
        "common.auto": "Auto",
        "common.total": "Total",
        "common.frames": "frames",
        "common.seconds": "seconds",

        // === Clip Bin Picker ===
        "picker.title": "🎬 MiniMax H3 Clip Gallery & Picker",
        "picker.view_deck": "🎴 Card Deck",
        "picker.view_tree": "🌳 Lineage Tree",
        "picker.view_deck_tooltip": "View all clips as thumbnail cards",
        "picker.view_tree_tooltip": "View clip lineage relationships in a family tree",
        "picker.refresh_tooltip": "Reload clips from the pool",
        "picker.empty_pool": "No clips saved in the pool yet. Generated videos will appear here automatically.",
        "picker.pool_not_ready": "Clip pool not initialized yet. Please run the workflow first.",
        "picker.select_as_relay": "🎯 Set as Relay Source",
        "picker.current_relay_source": "⭐ Current Relay Source",
        "picker.relay_source_set": "Set Clip #{id} as relay source",
        "picker.generation_info": "Generation Info",
        "picker.prompt": "Prompt",
        "picker.motion_cfg": "Motion CFG",
        "picker.frames_count": "Frames: {count}F ({sec}s)",
        "picker.created_at": "Created: {time}",
        "picker.parent_shot": "Parent Clip: #{id}",
        "picker.no_parent": "Parent Clip: None (Initial Generation)",
        "picker.child_shots": "Derived Clips ({count})",
        "picker.clip_details": "Clip Details",
        "picker.open_in_new_tab": "Open Video in New Tab",
        "picker.delete_shot": "Remove Clip from Pool",
        "picker.delete_shot_confirm": "Are you sure you want to remove Clip #{id} from the pool? (Video file will remain on disk)",
        "picker.search_placeholder": "Search by ID or prompt...",
        "picker.sort_newest": "Newest First",
        "picker.sort_oldest": "Oldest First",
        "picker.card_shot_badge": "Clip #{id}",
        "picker.copy_shot_id": "Copy Clip ID",
        "picker.card_duration": "{sec}s / {frames}F",

        // === Video Timeline ===
        "timeline.workbench_title": "🎬 Smart Video Split Studio",
        "timeline.assembler_title": "🧩 Video Timeline Assembler",
        "timeline.open_workbench": "🎬 Open Split Studio",
        "timeline.open_assembler": "🧩 Open Assembler Timeline",
        "timeline.total_duration": "Total Duration: {duration}s / {frames} frames",
        "timeline.segments_count": "Clips: {count}",
        "timeline.unrendered_count": "Unrendered: {count}",
        "timeline.completed_count": "Completed: {count}",
        "timeline.current_editing": "Active Target Clip: #{index} ({startSec}s - {endSec}s)",
        "timeline.prev_shot": "⏮️ Previous Clip",
        "timeline.next_shot": "⏭️ Next Clip",
        "timeline.next_unrendered": "🎯 Next Unrendered Clip",
        "timeline.reset_current_shot": "↩️ Reset This Clip",
        "timeline.reset_current_confirm": "Reset render result for Clip #{index} and mark as unrendered?",
        "timeline.export_full_mp4": "🎬 Export Complete Video (MP4)",
        "timeline.exporting": "Rendering and assembling MP4...",
        "timeline.export_success": "MP4 export completed successfully!",
        "timeline.export_failed": "Export failed: {error}",
        "timeline.download_mp4": "📥 Download Complete Video",
        "timeline.segment_info": "Clip #{index} Info",
        "timeline.segment_range": "Range: {start}s - {end}s ({frames} frames)",
        "timeline.segment_status_unrendered": "⚪ Unrendered",
        "timeline.segment_status_rendering": "🟡 Rendering...",
        "timeline.segment_status_completed": "🟢 Completed",
        "timeline.segment_status_custom": "🟣 Custom Replaced",
        "timeline.set_as_current_target": "🎯 Set as Target Clip",
        "timeline.split_at_cursor": "✂️ Split at Cursor",
        "timeline.merge_with_next": "🔗 Merge with Next Clip",
        "timeline.delete_segment": "🗑️ Delete Clip Segment",
        "timeline.zoom_in": "🔍 Zoom In",
        "timeline.zoom_out": "🔍 Zoom Out",
        "timeline.fit_timeline": "↔️ Fit Timeline",
        "timeline.play": "▶️ Play",
        "timeline.pause": "⏸️ Pause",
        "timeline.speed": "Speed: {rate}x",
        "timeline.cut_point": "Cut point: {sec}s ({frame}F)",
        "timeline.recommended_length": "💡 Recommended clip length: ~5s (85F) to 10s (171F)",
        "timeline.auto_advance_label": "Auto-advance to next unrendered clip on completion",
        "timeline.session_id": "Session ID: {id}",
        "timeline.source_video": "Source Video: {name}",
        "timeline.replace_source": "Replace Source Video",
        "timeline.save_session": "Save Session",
        "timeline.load_session": "Load Session",

        // === Node Display Names ===
        "node.prefix_stream_config": "MiniMax H3 Relay Config",
        "node.apply_prefix_stream": "MiniMax H3 Apply Relay",
        "node.clean_prefix_frames": "MiniMax H3 Trim Prefix Frames",
        "node.save_to_clip_bin": "MiniMax H3 Save to Clip Bin",
        "node.clip_bin_picker": "MiniMax H3 Clip Gallery & Picker",
        "node.smart_video_slicer": "🎬 MiniMax Smart Video Slicer",
        "node.video_timeline_assembler": "🧩 MiniMax Video Timeline Assembler",

        // === Tooltips ===
        "tooltip.mode": "Select relay generation mode.\n• Auto (Recommended): First shot is new, subsequent shots relay from prior clip\n• Force Initial: Always generate fresh standalone clip\n• Force Sequential: Always relay from the latest clip\n• Force Specific: Relay from a specified shot_id",
        "tooltip.prefix_frames": "Number of reference frames to inherit from prior clip (recommended: 5 frames for 17k+5).",
        "tooltip.blend_mode": "Select continuity blending method.\n• Native Masked AV (Recommended): ComfyUI native masked AV without quality loss\n• Motion Context: Traditional motion context method",
        "tooltip.pool_id": "Unique identifier for clip pool management.",
        "tooltip.target_shot": "Target clip ID for operation or relay source.",
        "tooltip.auto_advance": "Automatically select next unrendered clip when rendering finishes."
    },

    zh: {
        // === 常用 ===
        "common.loading": "加载中...",
        "common.confirm": "确认",
        "common.cancel": "取消",
        "common.close": "关闭",
        "common.save": "保存",
        "common.delete": "删除",
        "common.reset": "重置",
        "common.refresh": "刷新",
        "common.copy": "复制",
        "common.copied": "已复制",
        "common.failed": "失败",
        "common.success": "成功",
        "common.warning": "警告",
        "common.error": "错误",
        "common.all": "全部",
        "common.none": "无",
        "common.auto": "自动",
        "common.total": "总计",
        "common.frames": "帧",
        "common.seconds": "秒",

        // === Clip Bin Picker ===
        "picker.title": "🎬 MiniMax H3 候选片段画廊",
        "picker.view_deck": "🎴 卡片流",
        "picker.view_tree": "🌳 关系树",
        "picker.view_deck_tooltip": "卡片模式浏览所有候选片段",
        "picker.view_tree_tooltip": "树谱模式查看片段衍生继承关系",
        "picker.refresh_tooltip": "从候选池刷新片段列表",
        "picker.empty_pool": "暂无已保存的候选片段。生成视频后将自动收录到此处。",
        "picker.pool_not_ready": "片段池尚未就绪，请先运行工作流。",
        "picker.select_as_relay": "🎯 设为当前接力源",
        "picker.current_relay_source": "⭐ 当前接力源",
        "picker.relay_source_set": "已将片段 #{id} 设为接力源",
        "picker.generation_info": "生成参数",
        "picker.prompt": "提示词",
        "picker.motion_cfg": "Motion CFG",
        "picker.frames_count": "帧数: {count}F ({sec}秒)",
        "picker.created_at": "生成时间: {time}",
        "picker.parent_shot": "父片段: #{id}",
        "picker.no_parent": "父片段: 无 (首段全新生成)",
        "picker.child_shots": "衍生片段 ({count}条)",
        "picker.clip_details": "片段详情",
        "picker.open_in_new_tab": "新标签页播放视频",
        "picker.delete_shot": "从候选池中移除",
        "picker.delete_shot_confirm": "确定要从候选池中移除片段 #{id} 吗？（不会删除磁盘原视频文件）",
        "picker.search_placeholder": "按 ID 或提示词搜索...",
        "picker.sort_newest": "按时间倒序",
        "picker.sort_oldest": "按时间顺序",
        "picker.card_shot_badge": "片段 #{id}",
        "picker.copy_shot_id": "复制片段 ID",
        "picker.card_duration": "{sec}秒 / {frames}帧",

        // === Video Timeline ===
        "timeline.workbench_title": "🎬 视频智能切片工作台",
        "timeline.assembler_title": "🧩 回填总装时间轴",
        "timeline.open_workbench": "🎬 打开切片工作台",
        "timeline.open_assembler": "🧩 打开回填时间轴",
        "timeline.total_duration": "总时长: {duration}秒 / {frames}帧",
        "timeline.segments_count": "分段数: {count}",
        "timeline.unrendered_count": "未生成: {count}",
        "timeline.completed_count": "已完成: {count}",
        "timeline.current_editing": "当前编辑段: #{index} ({startSec}s - {endSec}s)",
        "timeline.prev_shot": "⏮️ 上一段",
        "timeline.next_shot": "⏭️ 下一段",
        "timeline.next_unrendered": "🎯 下一未编辑",
        "timeline.reset_current_shot": "↩️ 重置当前段",
        "timeline.reset_current_confirm": "确定重置分段 #{index} 的生成结果并恢复为未编辑状态？",
        "timeline.export_full_mp4": "🎬 导出全片 MP4",
        "timeline.exporting": "正在合成导出 MP4 全片...",
        "timeline.export_success": "MP4 导出成功！",
        "timeline.export_failed": "导出失败: {error}",
        "timeline.download_mp4": "📥 下载全片视频",
        "timeline.segment_info": "分段 #{index} 详情",
        "timeline.segment_range": "区间: {start}s - {end}s ({frames}帧)",
        "timeline.segment_status_unrendered": "⚪ 未编辑",
        "timeline.segment_status_rendering": "🟡 生成中...",
        "timeline.segment_status_completed": "🟢 已完成",
        "timeline.segment_status_custom": "🟣 自定义回填",
        "timeline.set_as_current_target": "🎯 设为当前编辑段",
        "timeline.split_at_cursor": "✂️ 在当前光标处切分",
        "timeline.merge_with_next": "🔗 与下一段合并",
        "timeline.delete_segment": "🗑️ 删除此分段",
        "timeline.zoom_in": "🔍 放大",
        "timeline.zoom_out": "🔍 缩小",
        "timeline.fit_timeline": "↔️ 适应宽度",
        "timeline.play": "▶️ 播放",
        "timeline.pause": "⏸️ 暂停",
        "timeline.speed": "倍速: {rate}x",
        "timeline.cut_point": "切分点: {sec}秒 ({frame}帧)",
        "timeline.recommended_length": "💡 建议单段长度约为 5秒 (85帧) 至 10秒 (171帧)",
        "timeline.auto_advance_label": "生成完成后自动切换到下一未编辑分段",
        "timeline.session_id": "会话 ID: {id}",
        "timeline.source_video": "源视频: {name}",
        "timeline.replace_source": "替换源视频",
        "timeline.save_session": "保存会话",
        "timeline.load_session": "加载会话",

        // === 节点名称 ===
        "node.prefix_stream_config": "MiniMax H3 接力配置",
        "node.apply_prefix_stream": "MiniMax H3 应用接力",
        "node.clean_prefix_frames": "MiniMax H3 剔除前缀重复帧",
        "node.save_to_clip_bin": "MiniMax H3 存入候选池",
        "node.clip_bin_picker": "MiniMax H3 候选片段画廊",
        "node.smart_video_slicer": "🎬 MiniMax 视频智能切片工作台",
        "node.video_timeline_assembler": "🧩 MiniMax 回填总装时间轴",

        // === 提示说明 ===
        "tooltip.mode": "选择接力模式。\n• Auto (推荐): 首段全新，后续自动从上一段接力\n• Force Initial: 始终强制全新生成独立片段\n• Force Sequential: 始终强制从最新生成接力\n• Force Specific: 强制从指定的 shot_id 接力",
        "tooltip.prefix_frames": "从前一片段继承的尾部参考帧数（建议: 5帧，对应 17k+5 边界）。",
        "tooltip.blend_mode": "接力融合方式。\n• Native Masked AV (推荐): ComfyUI 原生 Masked AV，无损画质\n• Motion Context: 传统 Motion Context 模式",
        "tooltip.pool_id": "片段池唯一标识符。",
        "tooltip.target_shot": "指定的片段 ID 或接力源。",
        "tooltip.auto_advance": "生成完成后自动选中下一未编辑片段。"
    }
};

/**
 * 現在のロケールを取得
 * 1. ComfyUIの設定 (app.ui.settings)
 * 2. localStorage (prefixstream_locale)
 * 3. ブラウザの言語 (navigator.language)
 * デフォルト: "ja" (日本語環境なら ja, 中国語なら zh, それ以外は en)
 */
export function getLocale() {
    try {
        const saved = localStorage.getItem("prefixstream_locale");
        if (saved && translations[saved]) {
            return saved;
        }

        // ComfyUI settings check
        if (window.app?.ui?.settings?.getSettingValue) {
            const comfyLang = window.app.ui.settings.getSettingValue("Comfy.Locale") || 
                              window.app.ui.settings.getSettingValue("AGL.Locale");
            if (comfyLang) {
                if (comfyLang.startsWith("ja")) return "ja";
                if (comfyLang.startsWith("zh")) return "zh";
                if (comfyLang.startsWith("en")) return "en";
            }
        }

        const navLang = (navigator.language || navigator.userLanguage || "ja").toLowerCase();
        if (navLang.startsWith("ja")) return "ja";
        if (navLang.startsWith("zh")) return "zh";
        return "ja"; // 日本語環境優先
    } catch (e) {
        return "ja";
    }
}

/**
 * 翻訳キーから文字列を取得
 * @param {string} key 翻訳キー (例: 'timeline.export_full_mp4')
 * @param {object} [params] プレースホルダー置換オブジェクト (例: {id: 3, count: 5})
 * @param {string} [fallback] キーが見つからない場合のフォールバック文字列
 */
export function t(key, params = {}, fallback = "") {
    const locale = getLocale();
    let str = translations[locale]?.[key] || 
              translations["en"]?.[key] || 
              translations["zh"]?.[key] || 
              fallback || 
              key;

    if (params && typeof params === "object") {
        for (const [k, v] of Object.entries(params)) {
            str = str.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
        }
    }
    return str;
}

/**
 * ロケールを手動設定
 */
export function setLocale(locale) {
    if (translations[locale]) {
        localStorage.setItem("prefixstream_locale", locale);
        return true;
    }
    return false;
}

export default { t, getLocale, setLocale, translations };
