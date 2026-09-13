"""
Seed script for this project: adds fake users + random posts. 60% of new
users get a profile picture, pulled from the images in ./pics_src (cycled
if there are more users than images). If pics_src is empty/missing, falls
back to generating a simple colored-initial avatar instead.

Uses the real SQLAlchemy models (async engine from database.py) instead of
raw sqlite3, so it creates the full schema itself -- including the
password_reset_token table -- and can't drift out of sync with models.py.
This means it also works fine against a brand new / deleted database, with
no need to start the FastAPI app first.

Run from inside the project folder (same folder as models.py / image_utils.py):

    python populate_db.py
"""

import asyncio
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
import random
from datetime import datetime, timedelta, UTC
from io import BytesIO
from pathlib import Path

from faker import Faker
from PIL import Image, ImageDraw, ImageFont
from pwdlib import PasswordHash
from sqlalchemy import select, func

import models
from database import Base, engine, AsyncSessionLocal
from image_utils import process_profile_image

BASE_DIR = Path(__file__).resolve().parent
PICS_SRC_DIR = BASE_DIR / "pics_src"

N_USERS = 60
PROFILE_PIC_RATE = 0.6   # 60% of new users get a profile picture
MIN_POSTS, MAX_POSTS = 1, 6
TEST_PASSWORD = "TestPass123"  # every fake user shares this password, for easy login testing

fake = Faker()
password_hash = PasswordHash.recommended()

AVATAR_COLORS = [
    "#EF4444", "#F97316", "#F59E0B", "#84CC16", "#22C55E",
    "#10B981", "#14B8A6", "#06B6D4", "#3B82F6", "#6366F1",
    "#8B5CF6", "#D946EF", "#EC4899", "#F43F5E",
]


def make_avatar_bytes(username: str) -> bytes:
    """Fallback: generate a simple colored-initial avatar."""
    color = random.choice(AVATAR_COLORS)
    img = Image.new("RGB", (400, 400), color=color)
    draw = ImageDraw.Draw(img)
    initials = "".join(w[0] for w in username.replace("_", " ").replace(".", " ").split()[:2]).upper() or username[0].upper()
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 160)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), initials, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((400 - w) / 2 - bbox[0], (400 - h) / 2 - bbox[1]), initials, fill="white", font=font)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def load_pic_pool() -> list[bytes]:
    if not PICS_SRC_DIR.exists():
        return []
    exts = {".jpg", ".jpeg", ".png"}
    return [p.read_bytes() for p in sorted(PICS_SRC_DIR.iterdir()) if p.suffix.lower() in exts]


async def main():
    pic_pool = load_pic_pool()
    use_real_pics = bool(pic_pool)
    print(f"Avatar source: {'pics_src (' + str(len(pic_pool)) + ' images)' if use_real_pics else 'generated placeholders'}")

    # Create all tables (users, posts, password_reset_token, ...) straight from
    # the models -- works even if the db file was deleted and never started
    # via the FastAPI app's lifespan.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        existing_usernames = {
            u.lower() for u in (await session.execute(select(models.User.username))).scalars().all()
        }
        existing_emails = {
            e.lower() for e in (await session.execute(select(models.User.email))).scalars().all()
        }

        created_users = 0
        users_with_pics = 0
        total_posts = 0
        pic_cursor = 0

        for _ in range(N_USERS):
            for _ in range(50):
                username = fake.unique.user_name()
                if username.lower() not in existing_usernames:
                    break
            existing_usernames.add(username.lower())

            for _ in range(50):
                email = fake.unique.email()
                if email.lower() not in existing_emails:
                    break
            existing_emails.add(email.lower())

            pw_hash = password_hash.hash(TEST_PASSWORD)

            image_file = None
            if random.random() < PROFILE_PIC_RATE:
                if use_real_pics:
                    content = pic_pool[pic_cursor % len(pic_pool)]
                    pic_cursor += 1
                else:
                    content = make_avatar_bytes(username)
                image_file = process_profile_image(content)
                users_with_pics += 1

            user = models.User(
                username=username,
                email=email,
                password_hash=pw_hash,
                image_file=image_file,
            )
            session.add(user)
            await session.flush()  # populate user.id for the posts below
            created_users += 1

            n_posts = random.randint(MIN_POSTS, MAX_POSTS)
            for _ in range(n_posts):
                title = fake.sentence(nb_words=8).rstrip(".")[:100]
                content = "\n\n".join(fake.paragraphs(nb=random.randint(1, 4)))
                days_ago = random.randint(0, 365)
                date_posted = datetime.now(UTC) - timedelta(days=days_ago)
                session.add(
                    models.Post(
                        title=title,
                        content=content,
                        user_id=user.id,
                        date_posted=date_posted,
                    )
                )
                total_posts += 1

        await session.commit()

        total_users_db = (await session.execute(select(func.count()).select_from(models.User))).scalar()
        total_posts_db = (await session.execute(select(func.count()).select_from(models.Post))).scalar()

    await engine.dispose()

    print(f"Created {created_users} new users (test password for all: {TEST_PASSWORD})")
    print(f"  - {users_with_pics} with a profile picture")
    print(f"  - {total_posts} posts created")
    print(f"DB totals -> users: {total_users_db}, posts: {total_posts_db}")


if __name__ == "__main__":
    asyncio.run(main())