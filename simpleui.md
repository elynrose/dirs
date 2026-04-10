Build simple UI tab Called Chat

I want to create a new UI which will look like a chat screen and will have a side panel and a main panel (like chatgpt web ui), the side panel will have project titles, when clicked opens its description box in the main panel, looking like a chat field and a create button. This page only runs hands off mode so when the user types the description (prompt) into the field and hit Generate, it shows project progress in the form of chat bubbles and the final video in a chat bubble when completed with a download link. 

Add an agent for the research part, it can ask questions based on the researched content and find out how the user wants the story to go, suggest narration styles, visual styles, character creation, scene count per chapter, # of chapters, ect and guide the user to get the best results.

Test when completed.

---

## Implementation status

- **Chat tab** — Added to the Studio vertical rail (`Chat`). UI lives in `apps/web/src/components/ChatStudioPage.jsx`: project sidebar, description/title/runtime composer, **Generate** always uses hands-off pipeline (`through: full_video`, `unattended: true`). Progress uses `steps_json` from `GET /v1/agent-runs/{id}` as assistant bubbles (including the character-bible step before scene images in the full-video tail); final MP4 uses `GET …/pipeline-status` + `apiCompiledVideoUrl` when the final-cut step is done.
- **Playwright** — `apps/web/e2e/chat-studio.spec.ts` checks the Chat page shell.
- **Research interviewer agent** — Not implemented yet (placeholder copy in the Chat sidebar points here).