# Mass update page

Change many notes already in Anki at once. The Auto-tag page becomes one action of it.

Flow: pick decks, describe the notes (the AI writes an Anki search, editable), find them, pick an
action, preview the changes, apply the ticked ones. The last apply can be undone.

## Tasks

- [x] Search. `POST /api/mass/write-search`: the AI turns a description into an Anki search, told
  the note types, field names and a sample value per field of the chosen decks.
  `POST /api/mass/find`: runs `(deck OR deck) (search)`, returns the count, sample fronts and
  field names. Check: bad syntax shows Anki's error; blank search = every note in the decks.
- [x] Actions on the found notes, as one background job with preview rows:
  - Add tags: the old Auto-tag (cache, validation, retries) unchanged.
  - Edit with AI: chosen fields rewritten from the user's prompt, 10 notes per call, every note
    answered, other fields read-only. Unchanged notes are only counted.
  - Find and replace: text or regex, case option, chosen fields, no AI.
  - Attach media clip: Nadeshiko `.mp4` for notes with an empty media field; with "replace",
    also for filled ones, keeping the same sentence when the field names a Nadeshiko file.
  Check: fake AI and fake AnkiConnect give the expected rows for each action.
- [x] Apply and undo. Notes edited in Anki since the preview are skipped. Fields with
  `updateNoteFields`, clips stored first with `storeMediaFile`, tags as before.
  `massupdate_undo.json` keeps the old fields and tags; Undo restores them unless the note was
  edited after the apply. Check: apply then undo gives back the original notes.
- [x] Mass update tab replaces the Auto-tag tab: decks, which notes, action with its options,
  example prompts, preview with before/after, Apply, Undo. Settings autosave.
  Check: browser run, light and dark, no console errors.
