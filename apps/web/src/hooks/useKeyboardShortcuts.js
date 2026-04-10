/**
 * useKeyboardShortcuts — register global keyboard shortcuts for the Studio editor.
 *
 * Shortcuts are disabled when the user's focus is inside a text input, textarea,
 * or contenteditable element so narration editing isn't interrupted.
 *
 * Usage:
 *   useKeyboardShortcuts({
 *     onNextScene,       // ArrowDown
 *     onPrevScene,       // ArrowUp
 *     onApproveAsset,    // A
 *     onRejectAsset,     // R
 *     onGenerateImage,   // G
 *     onPlayPause,       // Space
 *     onSaveNarration,   // Cmd/Ctrl+S
 *   });
 *
 * All handlers are optional — missing ones are silently skipped.
 */

import { useEffect } from "react";

const SKIP_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isTypingTarget(el) {
  if (!el) return false;
  if (SKIP_TAGS.has(el.tagName)) return true;
  if (el.isContentEditable) return true;
  return false;
}

/**
 * @param {Object} handlers
 * @param {Function} [handlers.onNextScene]      ArrowDown — select next scene
 * @param {Function} [handlers.onPrevScene]      ArrowUp   — select previous scene
 * @param {Function} [handlers.onApproveAsset]   A         — approve selected asset
 * @param {Function} [handlers.onRejectAsset]    R         — reject selected asset
 * @param {Function} [handlers.onGenerateImage]  G         — generate image for current scene
 * @param {Function} [handlers.onPlayPause]      Space     — play/pause audio preview
 * @param {Function} [handlers.onSaveNarration]  Cmd+S     — save narration draft
 * @param {Function} [handlers.onToggleHelp]    ?         — show/hide keyboard shortcut help
 * @param {boolean}  [enabled=true]              Set to false to disable all shortcuts
 */
export function useKeyboardShortcuts(handlers, { enabled = true } = {}) {
  useEffect(() => {
    if (!enabled) return;

    const onKeyDown = (e) => {
      if (isTypingTarget(document.activeElement)) return;

      const isMeta = e.metaKey || e.ctrlKey;

      switch (e.key) {
        case "ArrowDown":
          if (!isMeta && !e.shiftKey && handlers.onNextScene) {
            e.preventDefault();
            handlers.onNextScene();
          }
          break;

        case "ArrowUp":
          if (!isMeta && !e.shiftKey && handlers.onPrevScene) {
            e.preventDefault();
            handlers.onPrevScene();
          }
          break;

        case "a":
        case "A":
          if (!isMeta && handlers.onApproveAsset) {
            handlers.onApproveAsset();
          }
          break;

        case "r":
        case "R":
          if (!isMeta && handlers.onRejectAsset) {
            handlers.onRejectAsset();
          }
          break;

        case "g":
        case "G":
          if (!isMeta && handlers.onGenerateImage) {
            handlers.onGenerateImage();
          }
          break;

        case " ":
          if (!isMeta && handlers.onPlayPause) {
            e.preventDefault();
            handlers.onPlayPause();
          }
          break;

        case "s":
        case "S":
          if (isMeta && handlers.onSaveNarration) {
            e.preventDefault();
            handlers.onSaveNarration();
          }
          break;

        case "?":
          if (!isMeta && handlers.onToggleHelp) {
            e.preventDefault();
            handlers.onToggleHelp();
          }
          break;

        default:
          break;
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled, handlers]);
}
