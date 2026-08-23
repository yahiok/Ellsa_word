"""
ELLSA'S Word of the Day Bot
----------------------------
Runs once a day (triggered by GitHub Actions). Each run:
  1. Checks Telegram for any group/chat changes since the last run
     (bot added to a new group, bot removed from a group, or anyone
     who has messaged it directly) and updates the saved chat list.
  2. Fetches today's word from Merriam-Webster and from Oxford.
  3. Sends one combined message to every chat currently on the list.
  4. Saves the updated chat list back to chats.json (the GitHub Actions
     workflow commits this file back to the repo so it persists
     between runs).

No web server, no always-on process -- just a script that runs,
does its job, and exits.
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
CHATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chats.json")

MW_FEED_URL = "https://www.merriam-webster.com/wotd/feed/rss2"
OXFORD_URL = "https://corp.oup.com/?lang=en_GB&siteid=oxfordlearnersdictionaries"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WordOfTheDayBot/1.0)"}


# ---------- persistence ----------

def load_state():
    if not os.path.exists(CHATS_FILE):
        return {"chat_ids": [], "last_update_id": 0}
    with open(CHATS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(CHATS_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------- keeping the chat list in sync ----------

def sync_chats(state):
    """Pull any Telegram updates since the last run and update chat_ids:
    - bot added to a group -> add its chat_id
    - bot removed from a group -> drop its chat_id
    - anyone messages the bot directly -> add that chat too (handy for testing)
    """
    params = {
        "offset": state["last_update_id"] + 1,
        "allowed_updates": json.dumps(["message", "my_chat_member"]),
        "timeout": 0,
    }
    resp = requests.get(f"{API_URL}/getUpdates", params=params, timeout=15)
    resp.raise_for_status()
    updates = resp.json().get("result", [])

    for update in updates:
        state["last_update_id"] = max(state["last_update_id"], update["update_id"])

        if "my_chat_member" in update:
            mcm = update["my_chat_member"]
            chat_id = mcm["chat"]["id"]
            status = mcm["new_chat_member"]["status"]
            if status in ("member", "administrator", "creator"):
                if chat_id not in state["chat_ids"]:
                    state["chat_ids"].append(chat_id)
                    print(f"Added chat {chat_id} (status: {status})")
            elif status in ("left", "kicked"):
                if chat_id in state["chat_ids"]:
                    state["chat_ids"].remove(chat_id)
                    print(f"Removed chat {chat_id} (status: {status})")

        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            if chat_id not in state["chat_ids"]:
                state["chat_ids"].append(chat_id)
                print(f"Added chat {chat_id} (direct message)")


# ---------- Merriam-Webster ----------

def extract_mw_example(description_html):
    """Pull the first illustrative example sentence out of MW's RSS description
    field. That field holds an extended explanation followed by one or more
    example sentences separated by '//'. Take the first example, and stop at
    an em-dash attribution (e.g. '-- Some Author, Some Magazine') if one shows
    up, since those quote outside publications rather than being MW's own
    editorial example."""
    if not description_html:
        return None
    soup = BeautifulSoup(description_html, "html.parser")
    text = soup.get_text(" ", strip=True)
    parts = [p.strip() for p in text.split("//") if p.strip()]
    if len(parts) < 2:
        return None
    example = re.split(r"\s+—\s+", parts[1])[0].strip()
    return example or None


def fetch_merriam_webster():
    """Merriam-Webster publishes an official RSS feed for word of the day,
    including a clean short definition field -- no scraping needed."""
    resp = requests.get(MW_FEED_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    item = root.find("./channel/item")
    if item is None:
        raise ValueError("MW feed returned no items")

    word = item.findtext("title", "").strip()
    link = item.findtext("link", "").strip()
    ns = {"merriam": "https://www.merriam-webster.com/word-of-the-day"}
    definition = item.findtext("merriam:shortdef", default="", namespaces=ns).strip()
    example = extract_mw_example(item.findtext("description", ""))

    return {"word": word, "definition": definition, "link": link, "example": example}


# ---------- Oxford ----------

def fetch_oxford():
    """Oxford doesn't publish an official word-of-the-day feed or API (Lexico,
    which used to offer this, shut down in 2022). This instead reads the
    word-of-the-day box that Oxford University Press embeds on their own
    corporate homepage, which is plain HTML (no JavaScript rendering needed).

    This is the most fragile part of the bot: if OUP redesigns that page,
    this parser may need updating. It's written to degrade gracefully --
    if it fails, the bot still sends the Merriam-Webster word. See the
    README section "If the Oxford word stops showing up" for how to fix it.
    """
    resp = requests.get(OXFORD_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    lines = [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]

    wotd_idx = next((i for i, ln in enumerate(lines) if "Word of the Day" in ln), None)
    if wotd_idx is None:
        raise ValueError("Couldn't find a 'Word of the Day' section on the Oxford page")

    date_pattern = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")
    for i in range(wotd_idx, min(wotd_idx + 15, len(lines) - 2)):
        if date_pattern.match(lines[i]):
            word = lines[i + 1]
            definition = lines[i + 2]
            return {"word": word, "definition": definition, "link": "https://www.oxfordlearnersdictionaries.com/"}

    raise ValueError("Found the Oxford section but couldn't locate word/definition after it")


# ---------- messaging ----------

def escape_markdown(text):
    """Escape characters that have special meaning in Telegram's (legacy) Markdown."""
    return re.sub(r"([_*`\[])", r"\\\1", text)


def format_message(mw, ox):
    parts = ["📖 *Word of the Day*"]

    if mw:
        block = (
            f"\n🇺🇸 *Merriam-Webster:* {escape_markdown(mw['word'])}\n"
            f"{escape_markdown(mw['definition'])}"
        )
        if mw.get("example"):
            block += f"\n_e.g. {escape_markdown(mw['example'])}_"
        block += f"\n[Full entry]({mw['link']})"
        parts.append(block)
    else:
        parts.append("\n🇺🇸 *Merriam-Webster:* _couldn't fetch today's word_")

    if ox:
        # Oxford's source (see fetch_oxford) only ever provides a definition,
        # no example sentence -- nothing to add here even when it succeeds.
        parts.append(
            f"\n🇬🇧 *Oxford:* {escape_markdown(ox['word'])}\n"
            f"{escape_markdown(ox['definition'])}"
        )
    else:
        parts.append("\n🇬🇧 *Oxford:* _couldn't fetch today's word_")

    return "\n".join(parts)


def send_message(chat_id, text):
    resp = requests.post(
        f"{API_URL}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if not resp.ok:
        print(f"Failed to send to {chat_id}: {resp.text}")


# ---------- main ----------

def main():
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN environment variable is not set.")
        sys.exit(1)

    state = load_state()
    sync_chats(state)

    try:
        mw = fetch_merriam_webster()
        print(f"Merriam-Webster word: {mw['word']}")
    except Exception as e:
        print(f"Merriam-Webster fetch failed: {e}")
        mw = None

    try:
        ox = fetch_oxford()
        print(f"Oxford word: {ox['word']}")
    except Exception as e:
        print(f"Oxford fetch failed: {e}")
        ox = None

    if not mw and not ox:
        print("Both sources failed -- nothing to send. Exiting without messaging chats.")
        save_state(state)
        sys.exit(1)

    message = format_message(mw, ox)

    if not state["chat_ids"]:
        print("No chats to send to yet. Add the bot to a group, or message it directly, then re-run.")
    for chat_id in state["chat_ids"]:
        send_message(chat_id, message)
        print(f"Sent to {chat_id}")

    save_state(state)


if __name__ == "__main__":
    main()
