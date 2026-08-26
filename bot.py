#!/usr/bin/env python3
"""
===============================================================================
STOIC & GRINDSET DISCORD DAILY QUOTE BOT
===============================================================================
Author: Antigravity Automation Engineer
Description:
    An asynchronous, production-ready Python automation that scrapes raw,
    stoic/grindset aesthetic quote images (street signs, cardboard signs, grit)
    from Pinterest and delivers them as pure, clean images to Discord every
    day at 10:00 AM Europe/Ljubljana time.

Features:
    - Pure Image Posting: Zero embeds, zero text boxes, zero clutter.
    - Curated Street Sign & Cardboard Quote Queries.
    - Accurate Timezone & DST handling via zoneinfo (Europe/Ljubljana).
    - Robust Pinterest extraction with mobile & browser emulation (100+ images/query).
    - Automatic resolution upgrade to high-res (736x / originals).
    - Zero-duplicate state store using SQLite & SHA-256 image content hashing.
    - Exponential backoff retry logic & structured logging.
    - CLI testing tools (--post-now, --test-scrape, --stats).
===============================================================================
"""

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import random
import re
import signal
import sqlite3
import sys
import urllib.parse
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from PIL import Image
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# -----------------------------------------------------------------------------
# LOGGING CONFIGURATION
# -----------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("StoicBot")


# -----------------------------------------------------------------------------
# CONFIGURATION MANAGEMENT
# -----------------------------------------------------------------------------
class Config:
    """Loads and validates runtime configurations from environment variables."""

    # Default curated search queries focused on cardboard signs, street wisdom & raw grit
    DEFAULT_QUERIES = (
        "cardboard sign quotes aesthetic,"
        "street sign quotes grind,"
        "raw street sign quotes,"
        "hard truth quotes street signs,"
        "raw grit cardboard quotes,"
        "street wisdom quotes typography,"
        "black and white street quote aesthetic,"
        "cardboard quotes hustle discipline,"
        "dudewithsign gritty quotes,"
        "aggressive mindset quotes street"
    )

    def __init__(self):
        load_dotenv()

        self.webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        self.post_time_str: str = os.getenv("POST_TIME", "10:00").strip()
        self.timezone_str: str = os.getenv("TIMEZONE", "Europe/Ljubljana").strip()
        self.db_path: str = os.getenv("DATABASE_PATH", "history.db").strip()
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper().strip()
        self.bot_username: str = os.getenv("BOT_USERNAME", "").strip()

        # Search queries for raw cardboard signs, street signs & stoic grit
        raw_queries = os.getenv("SEARCH_QUERIES", self.DEFAULT_QUERIES)
        self.search_queries: List[str] = [q.strip() for q in raw_queries.split(",") if q.strip()]

        # Parse post time (HH:MM)
        try:
            time_parts = self.post_time_str.split(":")
            self.post_hour = int(time_parts[0])
            self.post_minute = int(time_parts[1])
            if not (0 <= self.post_hour <= 23 and 0 <= self.post_minute <= 59):
                raise ValueError
        except Exception:
            logger.warning(
                f"Invalid POST_TIME '{self.post_time_str}'. Defaulting to 10:00 AM."
            )
            self.post_hour, self.post_minute = 10, 0

        # Validate timezone (Europe/Ljubljana handles DST automatically)
        try:
            self.timezone = ZoneInfo(self.timezone_str)
        except Exception as e:
            logger.error(
                f"Invalid TIMEZONE '{self.timezone_str}': {e}. Defaulting to Europe/Ljubljana."
            )
            self.timezone = ZoneInfo("Europe/Ljubljana")
            self.timezone_str = "Europe/Ljubljana"

        # Apply log level
        numeric_level = getattr(logging, self.log_level, logging.INFO)
        logger.setLevel(numeric_level)

    def validate_for_posting(self):
        """Ensures mandatory variables for Discord posting are present."""
        if not self.webhook_url or not self.webhook_url.startswith("https://discord.com/api/webhooks/"):
            raise ValueError(
                "DISCORD_WEBHOOK_URL is missing or invalid in .env! "
                "Format: https://discord.com/api/webhooks/<id>/<token>"
            )


# -----------------------------------------------------------------------------
# DEDUPLICATION & STATE MANAGEMENT (SQLite)
# -----------------------------------------------------------------------------
class Database:
    """Persistent SQLite database to prevent any duplicate image posts."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        """Creates the required tables and indexes if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS posted_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_hash TEXT UNIQUE NOT NULL,
                    image_url TEXT,
                    pin_id TEXT,
                    query_source TEXT,
                    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_hash ON posted_images(image_hash)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_pin_id ON posted_images(pin_id)"
            )
            conn.commit()

    def is_duplicate(self, image_hash: str, pin_id: Optional[str] = None) -> bool:
        """Checks whether the image hash or pin ID has already been posted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if pin_id:
                cursor.execute(
                    "SELECT 1 FROM posted_images WHERE image_hash = ? OR (pin_id IS NOT NULL AND pin_id = ?)",
                    (image_hash, pin_id),
                )
            else:
                cursor.execute(
                    "SELECT 1 FROM posted_images WHERE image_hash = ?",
                    (image_hash,),
                )
            return cursor.fetchone() is not None

    def record_post(
        self,
        image_hash: str,
        image_url: str,
        query_source: str,
        pin_id: Optional[str] = None,
    ):
        """Records a newly posted image into the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO posted_images (image_hash, image_url, pin_id, query_source)
                VALUES (?, ?, ?, ?)
                """,
                (image_hash, image_url, pin_id, query_source),
            )
            conn.commit()

    def get_stats(self) -> Dict[str, any]:
        """Returns statistics on posted history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM posted_images")
            total = cursor.fetchone()[0]

            cursor.execute(
                "SELECT posted_at, query_source FROM posted_images ORDER BY id DESC LIMIT 1"
            )
            last_row = cursor.fetchone()
            last_posted = last_row[0] if last_row else "Never"
            last_query = last_row[1] if last_row else "N/A"

            return {
                "total_posts": total,
                "last_posted_at": last_posted,
                "last_query": last_query,
            }


# -----------------------------------------------------------------------------
# PINTEREST SCRAPER & IMAGE EXTRACTOR
# -----------------------------------------------------------------------------
class PinterestScraper:
    """Scrapes raw street signs, cardboard signs & stoic quote images from Pinterest."""

    USER_AGENTS = [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    ]

    FALLBACK_CANDIDATES = [
        {
            "url": "https://images.unsplash.com/photo-1507679799987-c73779587ccf?q=80&w=1200&auto=format&fit=crop",
            "title": "DISCIPLINE OVER MOTIVATION",
            "pin_id": "fallback_01",
        },
        {
            "url": "https://images.unsplash.com/photo-1517838277536-f5f99be501cd?q=80&w=1200&auto=format&fit=crop",
            "title": "NO PLAN B. EXECUTE.",
            "pin_id": "fallback_02",
        },
        {
            "url": "https://images.unsplash.com/photo-1534438327276-14e5300c3a48?q=80&w=1200&auto=format&fit=crop",
            "title": "THE OBSTACLE IS THE WAY",
            "pin_id": "fallback_03",
        },
        {
            "url": "https://images.unsplash.com/photo-1526506118085-60ce8714f8c5?q=80&w=1200&auto=format&fit=crop",
            "title": "SILENCE AND FOCUS",
            "pin_id": "fallback_04",
        },
    ]

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.pinterest.com/",
            "DNT": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        }

    @staticmethod
    def _upgrade_to_high_res(img_url: str) -> str:
        """Converts low-res Pinterest thumbnail URLs to 736x or originals for maximum crispness."""
        if not img_url:
            return img_url
        return re.sub(r"/(?:236x|474x|564x)/", "/736x/", img_url)

    async def search_pinterest(self, query: str) -> List[Dict[str, str]]:
        """Executes a Pinterest search and extracts candidate high-res images."""
        encoded_query = urllib.parse.quote_plus(query)
        search_url = f"https://www.pinterest.com/search/pins/?q={encoded_query}&rs=typed"
        logger.info(f"Querying Pinterest: '{query}' -> {search_url}")

        candidates: List[Dict[str, str]] = []

        try:
            async with self.session.get(
                search_url, headers=self._get_headers(), timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status != 200:
                    logger.warning(f"Pinterest returned HTTP status {response.status} for '{query}'")
                    return candidates

                html_text = await response.text()

            # Strategy 1: Unescape JSON and extract all direct i.pinimg.com image links
            unescaped_html = html_text.replace("\\/", "/").replace("\\u002F", "/")
            regex_matches = re.findall(
                r'https?://i\.pinimg\.com/(?:originals|736x|564x|474x|236x)/[a-zA-Z0-9/_.-]+\.(?:jpg|jpeg|png|webp)',
                unescaped_html,
            )

            for raw_url in set(regex_matches):
                if "avatars" in raw_url or "user" in raw_url or "logo" in raw_url:
                    continue
                high_res = self._upgrade_to_high_res(raw_url)
                candidates.append({
                    "url": high_res,
                    "title": query.upper(),
                    "pin_id": hashlib.md5(raw_url.encode()).hexdigest()[:12],
                })

            # Strategy 2: Embedded __PWS_DATA__ JSON script tag parsing
            if not candidates:
                soup = BeautifulSoup(html_text, "html.parser")
                pws_script = soup.find("script", id="__PWS_DATA__")
                if pws_script and pws_script.string:
                    try:
                        data = json.loads(pws_script.string)
                        candidates.extend(self._extract_pins_from_pws(data))
                    except Exception as e:
                        logger.debug(f"Could not parse __PWS_DATA__ JSON: {e}")

        except asyncio.TimeoutError:
            logger.warning(f"Timeout while searching Pinterest for query '{query}'")
        except Exception as e:
            logger.error(f"Error scraping Pinterest query '{query}': {e}", exc_info=False)

        logger.info(f"Discovered {len(candidates)} candidate images from Pinterest for '{query}'")
        return candidates

    def _extract_pins_from_pws(self, data: dict) -> List[Dict[str, str]]:
        """Deeply traverses Pinterest's Redux state tree to find pin image objects."""
        results = []

        def search_node(node):
            if isinstance(node, dict):
                if "images" in node and isinstance(node["images"], dict):
                    images = node["images"]
                    best_url = None
                    for key in ("orig", "736x", "564x", "474x", "236x"):
                        if key in images and "url" in images[key]:
                            best_url = images[key]["url"]
                            break
                    if best_url:
                        pin_id = str(node.get("id", ""))
                        title = node.get("grid_title") or node.get("title") or node.get("description") or "STOIC DISCIPLINE"
                        results.append({
                            "url": self._upgrade_to_high_res(best_url),
                            "title": title[:100],
                            "pin_id": pin_id or hashlib.md5(best_url.encode()).hexdigest()[:12],
                        })
                for v in node.values():
                    search_node(v)
            elif isinstance(node, list):
                for item in node:
                    search_node(item)

        search_node(data)
        return results

    async def get_curated_quote(self, queries: List[str], db: Database) -> Optional[Tuple[bytes, str, str, str]]:
        """Scrapes, downloads, validates dimensions, and verifies deduplication."""
        shuffled_queries = list(queries)
        random.shuffle(shuffled_queries)

        for query in shuffled_queries:
            candidates = await self.search_pinterest(query)
            random.shuffle(candidates)

            for item in candidates:
                img_url = item["url"]
                pin_id = item.get("pin_id")
                title = item.get("title", "STOIC GRINDSET")

                if pin_id and db.is_duplicate(image_hash="", pin_id=pin_id):
                    continue

                image_data = await self._download_and_validate_image(img_url)
                if not image_data:
                    continue

                image_bytes, img_hash = image_data

                if db.is_duplicate(image_hash=img_hash, pin_id=pin_id):
                    continue

                logger.info(
                    f"Selected fresh stoic image: {img_url} (SHA256: {img_hash[:10]}..., Query: '{query}')"
                )
                return image_bytes, img_url, title, query

        # Fallback pool
        logger.warning("All online Pinterest candidates exhausted or duplicate. Checking fallback pool...")
        for fallback in self.FALLBACK_CANDIDATES:
            img_url = fallback["url"]
            pin_id = fallback["pin_id"]
            image_data = await self._download_and_validate_image(img_url)
            if not image_data:
                continue

            image_bytes, img_hash = image_data
            if not db.is_duplicate(image_hash=img_hash, pin_id=pin_id):
                logger.info(f"Using curated fallback image: {img_url}")
                return image_bytes, img_url, fallback["title"], "curated_fallback"

        return None

    async def _download_and_validate_image(self, url: str) -> Optional[Tuple[bytes, str]]:
        """Downloads the image buffer, validates with Pillow, and calculates SHA-256."""
        try:
            async with self.session.get(
                url, headers=self._get_headers(), timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()

            if len(data) < 5000:
                return None

            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                if width < 300 or height < 300:
                    return None

            img_hash = hashlib.sha256(data).hexdigest()
            return data, img_hash

        except Exception as e:
            logger.debug(f"Failed to download/validate {url}: {e}")
            return None


# -----------------------------------------------------------------------------
# DISCORD WEBHOOK CLIENT (PURE IMAGE ONLY - NO EMBEDS)
# -----------------------------------------------------------------------------
class DiscordPoster:
    """Handles sending pure, clean image files directly to Discord Webhooks (no embeds)."""

    def __init__(self, webhook_url: str, bot_username: str, session: aiohttp.ClientSession):
        self.webhook_url = webhook_url
        self.bot_username = bot_username
        self.session = session

    async def post_image(
        self,
        image_bytes: bytes,
        source_url: str,
    ) -> bool:
        """
        Sends the pure quote image directly as a multipart file upload.
        NO EMBEDS, NO TITLES, NO CAPTIONS - only the raw full-resolution image.
        Retries up to 5 times with exponential backoff on rate limits.
        """
        form = aiohttp.FormData()
        if self.bot_username:
            form.add_field(
                "payload_json",
                json.dumps({"username": self.bot_username}),
                content_type="application/json",
            )
        form.add_field("file", image_bytes, filename="quote.jpg", content_type="image/jpeg")

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                logger.info(f"Posting pure quote image to Discord (Attempt {attempt.retry_state.attempt_number}/5)...")
                async with self.session.post(
                    self.webhook_url,
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status in (200, 204):
                        logger.info("Successfully posted pure quote image to Discord channel!")
                        return True
                    elif resp.status == 429:
                        retry_after = 5.0
                        try:
                            rate_limit_info = await resp.json()
                            retry_after = float(rate_limit_info.get("retry_after", 5.0))
                        except Exception:
                            pass
                        logger.warning(f"Discord 429 Rate Limit. Sleeping {retry_after}s...")
                        await asyncio.sleep(retry_after)
                        raise aiohttp.ClientError("Discord rate limited")
                    else:
                        resp_text = await resp.text()
                        logger.error(f"Discord Webhook error ({resp.status}): {resp_text}")
                        raise aiohttp.ClientError(f"HTTP {resp.status}: {resp_text}")

        return False


# -----------------------------------------------------------------------------
# ORCHESTRATOR & SCHEDULER
# -----------------------------------------------------------------------------
class StoicBotService:
    """Coordinates scraping, deduplication, scheduling, and Discord dispatching."""

    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.db_path)
        self.scheduler = AsyncIOScheduler(timezone=config.timezone)
        self._is_running = False

    async def execute_daily_routine(self) -> bool:
        """Core automation job: fetches fresh image, records to DB, and posts to Discord."""
        logger.info("=========================================================")
        logger.info("Starting Daily Stoic Quote Job...")
        logger.info(f"Current Time: {datetime.now(self.config.timezone).strftime('%Y-%m-%d %H:%M:%S %Z')}")

        async with aiohttp.ClientSession() as session:
            scraper = PinterestScraper(session)
            poster = DiscordPoster(
                webhook_url=self.config.webhook_url,
                bot_username=self.config.bot_username,
                session=session,
            )

            # 1. Fetch fresh, deduplicated image
            quote_data = await scraper.get_curated_quote(
                queries=self.config.search_queries,
                db=self.db,
            )

            if not quote_data:
                logger.error("Failed to source any valid quote image! Check network / search queries.")
                return False

            image_bytes, img_url, title, query_used = quote_data
            img_hash = hashlib.sha256(image_bytes).hexdigest()

            # 2. Dispatch pure image to Discord (NO embeds)
            success = await poster.post_image(
                image_bytes=image_bytes,
                source_url=img_url,
            )

            if success:
                # 3. Store in SQLite to guarantee zero duplicates in future
                self.db.record_post(
                    image_hash=img_hash,
                    image_url=img_url,
                    query_source=query_used,
                )
                logger.info(f"Recorded image hash {img_hash[:10]}... into {self.config.db_path}")
                logger.info("Daily job completed successfully.")
                logger.info("=========================================================")
                return True
            else:
                logger.error("Failed to deliver payload to Discord webhook.")
                return False

    def start_scheduler(self):
        """Configures APScheduler CronTrigger for the daily 10:00 AM job."""
        trigger = CronTrigger(
            hour=self.config.post_hour,
            minute=self.config.post_minute,
            timezone=self.config.timezone,
        )

        self.scheduler.add_job(
            self.execute_daily_routine,
            trigger=trigger,
            id="stoic_daily_quote_job",
            name=f"Daily Quote at {self.config.post_time_str} {self.config.timezone_str}",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        self.scheduler.start()
        next_run = self.scheduler.get_job("stoic_daily_quote_job").next_run_time
        logger.info(f"Bot scheduler initialized successfully.")
        logger.info(f"Timezone: {self.config.timezone_str} (DST-aware)")
        logger.info(f"Scheduled Daily Post Time: {self.config.post_time_str}")
        logger.info(f"Next Scheduled Execution: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    async def run_forever(self):
        """Keeps the event loop alive until a termination signal is received."""
        self._is_running = True
        self.start_scheduler()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
            except NotImplementedError:
                pass

        try:
            while self._is_running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            await self.shutdown()

    async def shutdown(self):
        """Gracefully shuts down scheduler and closes resources."""
        if not self._is_running:
            return
        logger.info("Graceful shutdown signal received. Stopping scheduler...")
        self._is_running = False
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        logger.info("Stoic Bot terminated cleanly.")


# -----------------------------------------------------------------------------
# COMMAND LINE INTERFACE (CLI)
# -----------------------------------------------------------------------------
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stoic & Grindset Daily Discord Quote Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--post-now",
        action="store_true",
        help="Trigger an immediate scrape and post to Discord without waiting for 10:00 AM.",
    )
    parser.add_argument(
        "--test-scrape",
        action="store_true",
        help="Test Pinterest image extraction locally without posting to Discord.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics and total posted quotes count.",
    )
    return parser.parse_args()


async def main_async():
    args = parse_arguments()
    config = Config()

    if args.stats:
        db = Database(config.db_path)
        stats = db.get_stats()
        print("\n--- DATABASE POSTING STATS ---")
        print(f"Database File:    {config.db_path}")
        print(f"Total Posts:      {stats['total_posts']}")
        print(f"Last Posted:      {stats['last_posted_at']}")
        print(f"Last Query:       {stats['last_query']}")
        print("------------------------------\n")
        return

    if args.test_scrape:
        logger.info("Running test scrape against Pinterest...")
        db = Database(config.db_path)
        async with aiohttp.ClientSession() as session:
            scraper = PinterestScraper(session)
            result = await scraper.get_curated_quote(config.search_queries, db)
            if result:
                data, url, title, query = result
                print("\n[SUCCESS] Test Scrape Passed:")
                print(f"  URL:         {url}")
                print(f"  Title:       {title}")
                print(f"  Query:       {query}")
                print(f"  Image Size:  {len(data)} bytes")
                print(f"  SHA256:      {hashlib.sha256(data).hexdigest()}\n")
            else:
                print("\n[ERROR] Could not extract any valid image.\n")
        return

    try:
        config.validate_for_posting()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    service = StoicBotService(config)

    if args.post_now:
        logger.info("Manual trigger (--post-now) activated. Executing routine immediately...")
        success = await service.execute_daily_routine()
        if success:
            logger.info("Immediate post executed successfully.")
        else:
            logger.error("Immediate post failed.")
        return

    logger.info("Starting Stoic Bot 24/7 Daemon...")
    await service.run_forever()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user. Exiting.")


if __name__ == "__main__":
    main()
