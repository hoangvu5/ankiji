# Auto-tag page

Tag notes that are already in Anki with tags from the user's own list, using the chosen AI.

## Tasks

- [x] Generic AI call. `llm.ask(provider, settings, system, user, schema, ...)` shared by card
  generation and tagging. Check: generating cards still works with the fake CLI and OpenAI paths.
- [x] Read and apply through AnkiConnect. `findNotes` per deck (subdecks included), `notesInfo`
  in chunks, first two fields as front/back. Apply with `multi`: `addTags` (Add) or
  `updateNoteTags` (Overwrite, Skip tagged). Check: fake AnkiConnect shows the expected tags.
- [x] AI tagging with validation. Batches of 30, tags limited by a schema enum, every answer
  checked (listed tags only, min..max, each note once), bad notes retried twice with the reason,
  then shown as failed. Check: a fake AI that breaks the rules gets retried and rejected.
- [x] Cache. `autotag_cache.json`, keyed by note ID + `mod`, plus a signature of the tag list and
  min/max. After applying, cached entries get the new `mod`. Check: a second run sends 0 notes.
- [x] Auto-tag tab. Tags, min/max (min <= max <= number of tags), decks, mode, AI, progress with
  cancel, preview table with per-row ticks, Apply. Settings autosave. Check: Playwright run,
  light and dark.
