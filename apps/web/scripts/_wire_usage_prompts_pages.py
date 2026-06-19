"""Replace usage and prompts page blocks in App.jsx only."""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "src/App.jsx"


def replace_branch(text: str, page_id: str, next_page_id: str, replacement: str) -> str:
    pat = rf'(\) : activePage === "{re.escape(page_id)}" \? )\s*.*?\s*(\) : activePage === "{re.escape(next_page_id)}")'
    rep = replacement.strip()
    out, n = re.subn(pat, rf"\1\n        {rep}\n      \2", text, count=1, flags=re.DOTALL)
    if n != 1:
        raise SystemExit(f"replace failed for {page_id} (n={n})")
    return out


def main() -> None:
    text = APP.read_text(encoding="utf-8")
    text = replace_branch(
        text,
        "usage",
        "prompts",
        """<StudioUsagePage
          usageSummary={usageSummary}
          usageErr={usageErr}
          usageLoading={usageLoading}
          usageDays={usageDays}
          setUsageDays={setUsageDays}
          loadUsageSummary={loadUsageSummary}
        />""",
    )
    text = replace_branch(
        text,
        "prompts",
        "research_chapters",
        """<StudioPromptsPage
          llmPromptsErr={llmPromptsErr}
          llmPromptsBusy={llmPromptsBusy}
          llmPrompts={llmPrompts}
          loadLlmPrompts={loadLlmPrompts}
          llmPromptDrafts={llmPromptDrafts}
          setLlmPromptDrafts={setLlmPromptDrafts}
          saveLlmPrompt={saveLlmPrompt}
          resetLlmPrompt={resetLlmPrompt}
        />""",
    )
    APP.write_text(text, encoding="utf-8")
    print("usage/prompts pages wired")


if __name__ == "__main__":
    main()
