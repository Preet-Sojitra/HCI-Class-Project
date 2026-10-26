# AV Synchrony Study Prototype

A lightweight Flask + vanilla JavaScript experiment interface for the HCI project:

**Perceptual Tolerance for Audio-Visual Misalignment in AI-Generated Video**

The app:

- serves local video stimuli in the browser,
- randomizes experimental trials,
- prevents replay through the normal UI,
- collects a binary synchrony response,
- collects a 1–7 naturalness rating,
- records response timing and basic display metadata,
- saves every trial to both SQLite and CSV,
- includes a practice trial and automatic breaks,
- works locally or through ngrok / Cloudflare Tunnel.

---

## 1. Setup

```bash
cd hci_av_sync_study

python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate       # Windows PowerShell

pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:8000
```

For a quick staging check that skips onboarding, runs one randomized trial,
and does not save the response, open:

```text
http://127.0.0.1:8000/test
```

### Render keep-alive endpoint

The app provides this endpoint for an external uptime monitor:

```text
GET https://YOUR-SERVICE.onrender.com/api/keepalive
```

Each request updates a single `service_heartbeat` row and returns its timestamp
and call count. Configure an external monitor to request it less than 15 minutes
apart (for example, every 10 minutes). The route does not call itself: once a
free Render service is asleep, code inside that service cannot run to wake it.

Important: SQLite and CSV files stored on a free Render web service are
ephemeral and can be lost when the service restarts, redeploys, or spins down.
Use Render Postgres or another persistent database before collecting responses
that you need to keep.

---

## 2. Add your clips

Put experimental videos inside:

```text
static/stimuli/
```

### Required filename format

```text
<base_clip_id>__<category>__<offset_ms>.mp4
```

Examples:

```text
impact01__impact__-320.mp4
impact01__impact__-160.mp4
impact01__impact__-80.mp4
impact01__impact__0.mp4
impact01__impact__80.mp4
impact01__impact__160.mp4
impact01__impact__320.mp4

speech01__speech__0.mp4
ambient03__ambient__160.mp4
```

The app automatically discovers the files and extracts:

- base clip ID
- category
- signed audio offset in milliseconds

No code changes are needed when you add more clips.

### Practice clips

Put one or more separate practice videos in:

```text
static/practice/
```

Practice filenames can be anything, for example:

```text
practice_01.mp4
```

If the practice folder is empty, the app temporarily uses the first experimental video as a practice clip. For the actual study, use a separate practice clip.

---

## 3. Study flow

Participant flow:

```text
Welcome / participant ID
        ↓
Instructions
        ↓
Practice trial
        ↓
Experimental video
        ↓
Synchrony: Yes / No
        ↓
Naturalness: 1–7
        ↓
Save response
        ↓
Next randomized trial
        ↓
Break(s)
        ↓
Completion
```

The randomizer makes a best-effort attempt to avoid putting two variants of the same base clip directly next to one another.

---

## 4. Where responses are saved

Every completed trial is saved immediately to:

```text
data/responses.sqlite3
data/responses.csv
```

You can download the CSV from:

```text
http://127.0.0.1:8000/admin/download
```

On Render, replace the local address with the deployed service URL:

```text
https://YOUR-SERVICE.onrender.com/admin/download
```

The download becomes available after the first response is recorded. On a free
Render service, download it immediately after the study and before any restart,
redeploy, or spin-down because the local SQLite and CSV files are ephemeral.

If a network save fails, the browser keeps a local fallback copy and retries when the app is loaded again.

---

## 5. Temporary remote hosting

### Option A — ngrok

Start Flask:

```bash
python app.py
```

Then in another terminal:

```bash
ngrok http 8000
```

Share the HTTPS URL that ngrok provides.

### Option B — Cloudflare Tunnel

If `cloudflared` is installed:

```bash
cloudflared tunnel --url http://localhost:8000
```

Again, share the generated HTTPS URL.

---

## 6. Important research note for this project

Remote access is convenient for prototype demos and pilot testing, but your experiment manipulates audio timing at the millisecond level. Different participant hardware can add uncontrolled delays, especially:

- Bluetooth headphones,
- wireless speakers,
- TVs / external monitors,
- different browser audio pipelines,
- different refresh rates.

For the strongest final study, run the main experiment on one standardized computer/display with wired headphones when possible. If you collect remote responses, record that mode and treat hardware variability as a limitation or separate analysis factor.

---

## 7. Recommended real-study checks

Before main data collection:

1. Verify each `0 ms` reference clip manually.
2. Verify the sign of the offset-generation script with a synthetic flash/click file.
3. Test the experiment on the exact browser and hardware you will use.
4. Confirm your course/instructor requirements for consent / human-participant review.
5. Keep participant booking information separate from response data.
6. Back up `data/responses.csv` after every session block.
7. Do not expose `/admin/download` publicly during real remote data collection unless you add authentication.

---

## 8. Optional next upgrades

Easy additions later:

- researcher-only login for CSV download,
- a study-mode field (`in_person` vs `remote`),
- browser/headphone self-report,
- fullscreen enforcement,
- keyboard shortcuts,
- Latin-square / constrained randomization,
- database export to a tidy analysis CSV,
- a separate administrator dashboard.
