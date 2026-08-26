# ⚔️ Stoic & Grindset Discord Daily Quote Bot

A production-ready asynchronous Python automation that scrapes raw stoic, discipline, and grindset aesthetic quote images from Pinterest and posts them to a Discord channel every day at **10:00 AM Europe/Ljubljana** time (with automatic Daylight Saving Time handling).

---

## ⚡ Key Features

- ⏰ **DST-Aware Scheduling**: Powered by `APScheduler` and Python's `zoneinfo` (`Europe/Ljubljana`), automatically adjusting during winter/summer time transitions.
- 🖼️ **Multi-Strategy Pinterest Scraper**: Extracts high-resolution (`736x` / `originals`) images via Redux script state (`__PWS_DATA__`), DOM analysis, and fallback curated stoic repositories.
- 🔒 **Zero Duplicate Guarantee**: Stores image SHA-256 binary content hashes and source pin IDs in an embedded SQLite database (`history.db`).
- 🎨 **Discord Webhook Styling**: Direct binary image upload as multipart attachment paired with dark aesthetic embeds (`0x111111`), custom headers, and randomized philosophical quotes.
- 🔁 **Resilience & Exponential Backoff**: Uses `tenacity` for retries on network drops or Discord HTTP 429 rate limits.
- 🛠️ **CLI Testing Tools**: Test Pinterest scraping or trigger immediate test posts using `--test-scrape`, `--post-now`, or `--stats`.

---

## 📁 Project Structure

```
stoic-discord-bot/
├── bot.py                # Main application script & scheduler
├── requirements.txt      # Python dependencies
├── .env.example          # Template environment variables
├── .env                  # Your private configuration (created from .env.example)
├── history.db            # SQLite database (auto-created on first run)
├── Dockerfile            # Container definition
├── docker-compose.yml    # Docker compose specification
├── stoic-bot.service     # Linux systemd service unit
└── README.md             # Complete documentation
```

---

## 🚀 Quickstart Guide (Local Setup)

### 1. Prerequisites
- Python 3.9+ installed
- A Discord Webhook URL ([How to create a Discord Webhook](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks))

### 2. Clone / Enter Directory & Setup Virtual Environment
```bash
cd stoic-discord-bot

# Create and activate virtual environment
python -m venv venv

# On Linux/macOS:
source venv/bin/activate

# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` and set your Discord Webhook:
```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
POST_TIME=10:00
TIMEZONE=Europe/Ljubljana
SEARCH_QUERIES=stoic quotes black and white,grind mindset signs,raw masculine wisdom aesthetic,raw street sign stoic quotes,discipline grindset black white
```

### 5. Test the Setup

#### Test Pinterest Scraper Locally (No Discord post):
```bash
python bot.py --test-scrape
```

#### Test Live Webhook Posting (Sends 1 quote to Discord immediately):
```bash
python bot.py --post-now
```

#### View Posting Stats:
```bash
python bot.py --stats
```

### 6. Start the 24/7 Daemon
```bash
python bot.py
```

---

## 🌐 24/7 Production Deployment

### Option A: Deploy via Docker & Docker Compose (Recommended)

1. Ensure Docker & Docker Compose are installed on your VPS.
2. Clone this project onto your VPS.
3. Configure your `.env` file:
   ```bash
   cp .env.example .env
   nano .env
   ```
4. Start the container in background daemon mode:
   ```bash
   docker compose up -d --build
   ```
5. View live logs:
   ```bash
   docker compose logs -f
   ```
6. Stop the bot:
   ```bash
   docker compose down
   ```

---

### Option B: Deploy via Linux `systemd` Service (Ubuntu/Debian VPS)

1. Place the repository in `/opt/stoic-discord-bot`:
   ```bash
   sudo mv stoic-discord-bot /opt/stoic-discord-bot
   cd /opt/stoic-discord-bot
   ```

2. Create virtual environment and install requirements:
   ```bash
   sudo python3 -m venv venv
   sudo /opt/stoic-discord-bot/venv/bin/pip install -r requirements.txt
   ```

3. Create and populate `.env`:
   ```bash
   sudo cp .env.example .env
   sudo nano .env
   ```

4. Copy and enable the systemd service:
   ```bash
   sudo cp stoic-bot.service /etc/systemd/system/stoic-bot.service
   sudo systemctl daemon-reload
   sudo systemctl enable stoic-bot
   sudo systemctl start stoic-bot
   ```

5. Check status and logs:
   ```bash
   sudo systemctl status stoic-bot
   sudo journalctl -u stoic-bot -f
   ```

---

## ⚙️ Configuration Reference

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `DISCORD_WEBHOOK_URL` | *None (Required)* | Target Discord Webhook URL |
| `POST_TIME` | `10:00` | 24-hour time format (`HH:MM`) |
| `TIMEZONE` | `Europe/Ljubljana` | IANA Timezone (DST-aware) |
| `SEARCH_QUERIES` | `stoic quotes black and white,...` | Comma-separated search queries |
| `DATABASE_PATH` | `history.db` | Path to SQLite deduplication database |
| `EMBED_COLOR` | `0x111111` | Hex color code for Discord embed sidebar |
| `BOT_USERNAME` | `STOIC // DAILY GRIND` | Display name of the webhook sender |
| `LOG_LEVEL` | `INFO` | Verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
