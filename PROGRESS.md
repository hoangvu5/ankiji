# Progress

## 2026-11-19: Auto-tag page

Done (plans/auto-tag.md): generic `llm.ask`, Auto-tag tab, validation and retries, cache by
note ID + modified time, preview and apply. Verified with a fake AnkiConnect and a fake AI.

Not verified yet: against the real Anki (`multi`, `addTags`, `updateNoteTags`, `notesInfo.mod`)
and real AI providers for tagging.

## 2026-11-19: Nadeshiko media as one video clip

Fill media now puts one `.mp4` (`urls.videoUrl`, picture and sound together) in the Media field as
`[sound:nadeshiko_<id>.mp4]`, instead of a `.webp` image plus an `.mp3`. The review table previews
the clip. Verified with the real Nadeshiko API and a fake AnkiConnect (only the used `.mp4` is
stored). Not verified yet: playback in the user's real Anki desktop and AnkiMobile.

## 2026-11-19: Mass update page

Done (plans/mass-update.md): the Auto-tag tab became Mass update. Decks plus an Anki search the AI
can write, then one action (add tags, edit with AI, find and replace, attach media clip), preview
with before/after, apply, and undo of the last apply. Verified with a fake AnkiConnect, fake AI and
fake Nadeshiko, and in the browser (light and dark).

Not verified yet: against the real Anki (`updateNoteFields`, `findNotes` with AI-written searches)
and real AI providers for search writing and editing.

## 2026-11-19: Clips missing on the iPhone

Anki saved the mixed-case clip names (`nadeshiko_14nq3br9-eY2.mp4`) in lowercase, so the cards
pointed at names that exist only on case-insensitive Windows: Check Media showed 90 missing and 90
unused. Clip names are now lowercase (with the capitals encoded so the sentence ID can be read
back), and cards use the name Anki returns. The existing notes need a one-time repair.
