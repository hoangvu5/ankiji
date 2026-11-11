# Ankiji

Turns your study material (PDFs, photos, screenshots, Word, PowerPoint or plain text) into Anki flashcards with AI, then puts them straight into your Anki deck without duplicates. It can also add an example sentence from anime to each card: a screenshot, the audio, and the Japanese and English subtitles.

Everything runs on your own computer. Nothing is uploaded anywhere except to the AI you choose and to Nadeshiko (for example sentences).

---

## 1. Install (about 2 minutes)

1. Download **Ankiji.zip**.
2. Right-click the zip, choose **Extract All...**, and extract it into your **Documents** folder. (Don't run it from inside the zip.)
3. Open the new **Ankiji** folder and double-click **Ankiji.exe**.
4. **Windows may show a blue "Windows protected your PC" box.** That's normal for small apps that aren't from a big company. Click **More info**, then **Run anyway**. You only need to do this once.
5. The app opens in its own window. To stop it, just close the window.

Optional: right-click **Ankiji.exe** and choose **Send to > Desktop (create shortcut)** so you can start it from your desktop.

You don't need to install Python or anything else. The app uses Microsoft Edge (already part of Windows) to show its window.

## 2. One-time setup

The app has a **Setup guide** tab with every step, including buttons that copy each command. In short:

### Make an Anki account (to study on your phone too)
1. Sign up at ankiweb.net and confirm your email.
2. In Anki on the computer click **Sync** and log in. On your phone install **AnkiDroid** (Android) or **AnkiMobile** (iPhone) and log in with the same account.
3. After adding cards, click **Sync** on the computer, then sync on the phone.

### Connect Anki
1. Install Anki from apps.ankiweb.net and open it.
2. In Anki: **Tools > Add-ons > Get Add-ons...**, enter **2055492159**, click OK, then restart Anki.
3. In the app, click **Recheck** at the top. It turns green: **Anki connected**.

Keep Anki open whenever you use the app.

### Set up the note type
In the **Setup guide** tab, under **Note type**, click **Create the Japanese note type** or **Create the Mandarin note type** (Anki must be open). This adds a note type with the fields **Front**, **Back** and **Media**, its card designs and styling. Then choose it as **Note type** on the Create cards tab.

- **Japanese Card Format**: card 1 shows the word (tap it to see the reading), card 2 shows the meaning and you handwrite the kanji on screen.
- **Mandarin Card Format**: card 1 the same with pinyin, card 2 shows the meaning and you recall the characters.

Prefer doing it by hand? The guide has the steps and every template with Copy buttons. You can also ask ChatGPT or Claude to change a template for you (bigger text, dark mode, another language) and paste the result into Anki.

### Choose an AI (you only need one)

| Option | Good if | Cost |
|---|---|---|
| **OpenAI API** (easiest) | You want the simplest setup | Pay per use, usually a few cents per batch |
| **ChatGPT subscription** | You already pay for ChatGPT Plus or higher | No extra cost |
| **Claude subscription** | You already pay for Claude Pro or Max | No extra cost |

**OpenAI API, quick version:** sign in at platform.openai.com, add $5 under **Settings > Billing**, create a key at platform.openai.com/api-keys, then in the app click **AI settings**, paste the key and click **Save**.

The two subscription options need a couple of programs installed first. Open the app's **Setup guide** tab, or click **Open the step-by-step setup guide for this AI** under the AI dropdown.

### Nadeshiko (optional, for example sentences)
1. Make a free account at nadeshiko.co, then create an API key at nadeshiko.co/user/developer.
2. In the app's **Nadeshiko** tab, paste the key.
3. Optional: paste your MyAnimeList profile link (the list must be public) and click **Update from MyAnimeList**. Then sentences come from anime you've watched first.

## 3. Create cards

1. **Material:** drop files in, or paste a screenshot with Ctrl+V. For worksheets, scans or vertical Japanese, set **How to read PDFs** to **Always as page images**.
2. **Card prompt:** pick one from the dropdown. Two come built in: **Japanese** (JLPT, with Vietnamese meanings) and **Mandarin** (HSK 1-2, for Vietnamese speakers). Edit them freely, or click **New** to make your own (it starts as a copy of the current one). Changes save automatically.
3. **Destination:** pick your deck and note type.
4. Click **Generate cards**.
5. **Review:** edit anything in the table. Cards already in Anki are marked and unticked automatically.
6. Optional: click **Fill media** (see below).
7. Click **Add N cards to Anki**.

## 4. Example sentences (Fill media)

After generating, the box above the review table fills each selected card's **Media** field with:

- the screenshot and audio of the line
- the Japanese subtitle (the word in bold)
- the English subtitle

For each card you can **Play** the audio, see the **Next sentence**, **Remove** it, or type a different word (for example 食べた instead of 食べる) and click **Search**.

Under **Options**:
- **Put it in field**: which field gets the media. It picks a field called "Media" automatically.
- **Look up the word from field**: usually the first field.
- **Kinds of media**: Anime, J-Drama, YouTube, Audiobook (these are the kinds Nadeshiko has).
- **If the word isn't in my anime, use other titles**: when it's off, only the anime ticked in the Nadeshiko tab are used.

Notes:
- Images and audio only go into Anki with **Send cards by: Straight into Anki**. An import file keeps just the text.
- Nadeshiko allows 5,000 searches a month. Each card uses 1 to 4.

## 5. Good to know

- **Duplicates:** Anki treats a card as a duplicate when its first field matches an existing card of the same note type. You can choose "this deck" or "whole collection".
- **Card direction:** leave it on **Use the note type's own cards**. If your note type makes two cards per note, you get both directions automatically.
- **Your settings** (prompts, deck, AI choice, keys) are saved in `settings.json` in the app folder, on this computer only. **Reset settings** at the top clears any of them.

## 6. Updating to a new version

Extract the new zip **over the old folder** (choose "Replace" when Windows asks). Your `settings.json` and `nadeshiko_cache.json` are kept because the new zip doesn't contain them.

## 7. Problems?

| Problem | Fix |
|---|---|
| "Anki not connected" | Open Anki. Check the AnkiConnect add-on is installed (code 2055492159) and restart Anki. Click **Recheck**. |
| Window doesn't open | Wait 10 seconds and try again. If it still fails, restart the computer. |
| Antivirus deletes or blocks the .exe | Some antivirus programs distrust small unsigned apps. Add the **Ankiji** folder as an exception. |
| AI says "not installed" or "not signed in" | Follow that AI's steps in the **Setup guide** tab, then click **Check again**. |
| "Nadeshiko's request limit was reached" | You used this month's 5,000 searches. It resets next month. |

---

## For the developer

- **Run from source:** double-click `start.bat` (creates `.venv`, installs `requirements.txt`, runs `run.py`). `run.py --browser` opens a normal browser tab instead of the app window.
- **Build the shareable zip:** double-click `build.bat`. It produces `dist\Ankiji.zip` (about 60 MB). The build never includes `settings.json`, `nadeshiko_cache.json`, the window profile or the log.
- The handwriting card uses KanjiCanvas (c) 2019 Dominik Klein, MIT License, github.com/asdfjkl/kanjicanvas. It ships as `app/static/_kanjicanvas.js`.
- Port **8770** is the app, **8765** is AnkiConnect. Set `ANKIJI_PORT` to run a second copy on another port for testing.
