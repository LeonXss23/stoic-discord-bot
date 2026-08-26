#!/usr/bin/env python3
"""
===============================================================================
STOIC & GRINDSET DISCORD DAILY QUOTE BOT
===============================================================================
Author: Antigravity Automation Engineer
Description:
    An asynchronous, production-ready Python automation that posts curated,
    raw stoic and grindset aesthetic quote images directly to Discord every day
    at 10:00 AM Europe/Ljubljana time.

Features:
    - 100% Hand-Curated Aesthetic Pool: ONLY your exact 26 pins.
    - Pure Image Posting: Zero embeds, zero text clutter, full resolution.
    - Zero Duplicates: SQLite database stores SHA-256 binary hashes.
    - Accurate Timezone & DST handling (Europe/Ljubljana).
    - Exponential backoff retry logic on network/webhook errors.
    - CLI tools: --post-now, --stats.
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
# EXACT CURATED IMAGE POOL (Strictly your 26 pin URLs)
# -----------------------------------------------------------------------------
CURATED_IMAGE_POOL = [
    "https://i.pinimg.com/736x/fd/35/4b/fd354be4931c74f0d8233df280e3c5bd.jpg",
    "https://i.pinimg.com/736x/20/52/3a/20523a2e53de5d996f2605f3ee77d598.jpg",
    "https://i.pinimg.com/originals/d8/8b/a6/d88ba6aecafc79808d04f6a47961434d.jpg",
    "https://i.pinimg.com/736x/dd/b4/73/ddb4739ea789f23e9e543cceb5e1d329.jpg",
    "https://i.pinimg.com/736x/44/d0/56/44d056e5e45338fd44a875e20c7163c9.jpg",
    "https://i.pinimg.com/originals/85/d7/91/85d791ed740af8139d8f456f9c53c8b7.jpg",
    "https://i.pinimg.com/736x/eb/23/66/eb23661f64fc68b11ddfd8443186da45.jpg",
    "https://i.pinimg.com/736x/e2/7c/f6/e27cf6da9791772259330e96bd547057.jpg",
    "https://i.pinimg.com/736x/b0/71/22/b0712279920e9f44e9ed17c90809df65.jpg",
    "https://i.pinimg.com/736x/c3/f5/ab/c3f5ab0b8139aeba7f9514f8592d7166.jpg",
    "https://i.pinimg.com/736x/b7/ec/47/b7ec47d4ef41e1387e147c65211fb827.jpg",
    "https://i.pinimg.com/736x/19/db/60/19db6083d545bff336b02896c69201c3.jpg",
    "https://i.pinimg.com/736x/46/a3/ac/46a3ac9c3c2dec4ea7c2445bbcd47ab2.jpg",
    "https://i.pinimg.com/736x/43/17/6f/43176f2c11cabefd9876234d5fb97c80.jpg",
    "https://i.pinimg.com/736x/f1/0e/88/f10e8867bb3a82221747dfc4cceef5a5.jpg",
    "https://i.pinimg.com/736x/e2/ba/93/e2ba933ab361d9cbd6477be00c92b358.jpg",
    "https://i.pinimg.com/736x/8b/5b/f7/8b5bf77b9abfd8cc1462c435f90972fa.jpg",
    "https://i.pinimg.com/736x/1b/76/2d/1b762df9f9e995632a1ce791b3d3b1e3.jpg",
    "https://i.pinimg.com/736x/c2/03/39/c20339c8c4c7263b084832866b7234a9.jpg",
    "https://i.pinimg.com/736x/20/f8/14/20f8143395cbd94a17b13c2d74d76443.jpg",
    "https://i.pinimg.com/736x/31/70/3a/31703a4f2c48dbb9891985661768d7c8.jpg",
    "https://i.pinimg.com/736x/3b/77/b5/3b77b57470f2520641d7ab9fffaa9eae.jpg",
    "https://i.pinimg.com/736x/17/a8/68/17a868485733b28764b379c5665355d4.jpg",
    "https://i.pinimg.com/736x/72/0d/0d/720d0d503c8e6eabae21db4b151e73f7.jpg",
    "https://i.pinimg.com/736x/39/80/a3/3980a39f06e2ecc67d39507d2398e523.jpg",
    "https://i.pinimg.com/736x/70/43/86/7043863d46d24752727796771368e474.jpg",
]


# -----------------------------------------------------------------------------
# CONFIGURATION MANAGEMENT
# -----------------------------------------------------------------------------
class Config:
    """Loads and validates runtime configurations from environment variables."""

    def __init__(self):
        load_dotenv()

        self.webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        self.post_time_str: str = os.getenv("POST_TIME", "10:00").strip()
        self.timezone_str: str = os.getenv("TIMEZONE", "Europe/Ljubljana").strip()
        self.db_path: str = os.getenv("DATABASE_PATH", "history.db").strip()
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper().strip()
        self.bot_username: str = os.getenv("BOT_USERNAME", "").strip()

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
            conn.commit()

    def is_duplicate(self, image_hash: str) -> bool:
        """Checks whether the image hash has already been posted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM posted_images WHERE image_hash = ?",
                (image_hash,),
            )
            return cursor.fetchone() is not None

    def record_post(
        self,
        image_hash: str,
        image_url: str,
        query_source: str = "curated_pool",
    ):
        """Records a newly posted image into the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO posted_images (image_hash, image_url, query_source)
                VALUES (?, ?, ?)
                """,
                (image_hash, image_url, query_source),
            )
            conn.commit()

    def get_stats(self) -> Dict[str, any]:
        """Returns statistics on posted history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM posted_images")
            total = cursor.fetchone()[0]

            cursor.execute(
                "SELECT posted_at, image_url FROM posted_images ORDER BY id DESC LIMIT 1"
            )
            last_row = cursor.fetchone()
            last_posted = last_row[0] if last_row else "Never"
            last_url = last_row[1] if last_row else "N/A"

            return {
                "total_posts": total,
                "last_posted_at": last_posted,
                "last_url": last_url,
                "total_curated_available": len(CURATED_IMAGE_POOL),
            }


# -----------------------------------------------------------------------------
# IMAGE FETCHER & VALIDATOR
# -----------------------------------------------------------------------------
class ImageFetcher:
    """Fetches, validates and deduplicates images strictly from the curated pool."""

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    ]

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.pinterest.com/",
        }

    async def get_next_quote(self, db: Database) -> Optional[Tuple[bytes, str]]:
        """
        Shuffles the curated pool, checks against database history,
        and returns the first unposted valid image buffer.
        """
        pool = list(CURATED_IMAGE_POOL)
        random.shuffle(pool)

        for img_url in pool:
            data_result = await self._download_and_validate(img_url)
            if not data_result:
                continue

            image_bytes, img_hash = data_result

            if db.is_duplicate(img_hash):
                logger.debug(f"Skipping already posted image hash: {img_hash[:10]}...")
                continue

            logger.info(f"Selected fresh curated quote: {img_url} (SHA256: {img_hash[:10]}...)")
            return image_bytes, img_url

        logger.error("All curated images in the pool have been posted! Reset DB or add more images.")
        return None

    async def _download_and_validate(self, url: str) -> Optional[Tuple[bytes, str]]:
        """Downloads image, validates dimensions, and computes SHA-256 hash."""
        try:
            async with self.session.get(
                url, headers=self._get_headers(), timeout=aiohttp.ClientTimeout(total=15)
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
            logger.debug(f"Error downloading {url}: {e}")
            return None


# -----------------------------------------------------------------------------
# DISCORD POSTER (PURE IMAGE ONLY - NO EMBEDS)
# -----------------------------------------------------------------------------
class DiscordPoster:
    """Handles sending pure, clean image files directly to Discord Webhooks."""

    def __init__(self, webhook_url: str, bot_username: str, session: aiohttp.ClientSession):
        self.webhook_url = webhook_url
        self.bot_username = bot_username
        self.session = session

    async def post_image(self, image_bytes: bytes) -> bool:
        """Sends the pure image directly as a multipart file upload."""
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
    """Coordinates curated selection, deduplication, scheduling, and Discord dispatching."""

    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.db_path)
        self.scheduler = AsyncIOScheduler(timezone=config.timezone)
        self._is_running = False

    async def execute_daily_routine(self) -> bool:
        """Fetches fresh curated image, records hash to DB, and posts pure image to Discord."""
        logger.info("=========================================================")
        logger.info("Starting Daily Stoic Quote Job...")
        logger.info(f"Current Time: {datetime.now(self.config.timezone).strftime('%Y-%m-%d %H:%M:%S %Z')}")

        async with aiohttp.ClientSession() as session:
            fetcher = ImageFetcher(session)
            poster = DiscordPoster(
                webhook_url=self.config.webhook_url,
                bot_username=self.config.bot_username,
                session=session,
            )

            # 1. Select unposted curated image
            result = await fetcher.get_next_quote(self.db)
            if not result:
                logger.error("Failed to source a valid unposted quote image.")
                return False

            image_bytes, img_url = result
            img_hash = hashlib.sha256(image_bytes).hexdigest()

            # 2. Dispatch pure image to Discord
            success = await poster.post_image(image_bytes)

            if success:
                # 3. Record in SQLite to guarantee zero duplicates
                self.db.record_post(image_hash=img_hash, image_url=img_url)
                logger.info(f"Recorded image hash {img_hash[:10]}... into {self.config.db_path}")
                logger.info("Daily job completed successfully.")
                logger.info("=========================================================")
                return True
            else:
                logger.error("Failed to deliver image to Discord webhook.")
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
        help="Trigger an immediate post to Discord without waiting for 10:00 AM.",
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
        print(f"Database File:           {config.db_path}")
        print(f"Total Posts Sent:        {stats['total_posts']}")
        print(f"Curated Pool Size:       {stats['total_curated_available']}")
        print(f"Last Posted At:          {stats['last_posted_at']}")
        print(f"Last Image URL:          {stats['last_url']}")
        print("------------------------------\n")
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
