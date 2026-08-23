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
from email.utils import parsedate_to_datetime

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

def render_inline(tag):
    """Convert a BeautifulSoup tag's content to Telegram-HTML-safe text,
    turning MW's <em> emphasis into Telegram's <i> tag instead of flattening
    it to plain text. MW uses <em> both on the featured word itself
    (wherever it appears in an example) and on the publication/book title
    in a citation -- this preserves both."""
    parts = []
    for node in tag.children:
        if isinstance(node, str):
            parts.append(escape_html(str(node)))
        elif getattr(node, "name", None) == "em":
            parts.append(f"<i>{escape_html(node.get_text(' ', strip=True))}</i>")
        else:
            parts.append(escape_html(node.get_text(" ", strip=True)))
    text = "".join(parts)
    return re.sub(r"\s+", " ", text).strip()


def fetch_merriam_webster():
    """Merriam-Webster's RSS feed description field, per item, holds:
      - MW's own one-or-more illustrative example sentences, each in a
        <p> starting with '//'
      - exactly one real "Examples:" section: a quoted excerpt from an
        actual publication, with a source attribution

    Note: the feed's HTML nests <p> tags inside <p> tags, which is invalid
    HTML -- Python's html.parser keeps that nesting literally rather than
    auto-closing it, so the "Examples:" quote is a *child* of its label's
    parent <p>, not a following sibling. (Verified against the live feed;
    see the parser test this was built against.)
    """
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

    date = None
    pub_date_raw = item.findtext("pubDate", "").strip()
    if pub_date_raw:
        try:
            dt = parsedate_to_datetime(pub_date_raw)
            date = f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"
        except Exception:
            date = None

    description_html = item.findtext("description", "") or ""
    soup = BeautifulSoup(description_html, "html.parser")

    # Pronunciation sits as plain text between the word's own <strong> tag
    # and the part-of-speech <em> tag, e.g.:
    #   <strong>prowess</strong> • \PROW-us\  • <em>noun</em><br/>
    # -- not every word necessarily has one, so this stays None if absent.
    pronunciation = None
    word_strong = soup.find("strong", string=lambda s: s and s.strip().lower() == word.lower())
    if word_strong:
        for sib in word_strong.next_siblings:
            name = getattr(sib, "name", None)
            if name in ("br", "p"):
                break
            if isinstance(sib, str):
                match = re.search(r"\\(.+?)\\", sib)
                if match:
                    pronunciation = match.group(1).strip()
                    break

    examples = []
    for p in soup.find_all("p"):
        if p.get_text(" ", strip=True).startswith("//"):
            examples.append(re.sub(r"^/+\s*", "", render_inline(p)))

    in_context = None
    label = soup.find("strong", string=re.compile(r"^\s*Examples:?\s*$"))
    if label and label.parent:
        quote_p = label.parent.find("p")
        if quote_p:
            in_context = render_inline(quote_p)

    return {
        "word": word,
        "pronunciation": pronunciation,
        "definition": definition,
        "link": link,
        "examples": examples,
        "in_context": in_context,
        "date": date,
    }


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

def escape_html(text):
    """Escape characters with special meaning in Telegram's HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(mw, ox):
    header = "📖 <b>Word of the Day</b>"
    if mw and mw.get("date"):
        header += f" — {escape_html(mw['date'])}"
    parts = [header]

    if mw:
        word_line = f"🇺🇸 <b>Merriam-Webster:</b> {escape_html(mw['word'])}"
        if mw.get("pronunciation"):
            word_line += f" <i>\\{escape_html(mw['pronunciation'])}\\</i>"
        block = f"\n{word_line}\n{escape_html(mw['definition'])}"
        # MW usually gives one editorial example, occasionally two or more.
        for ex in mw.get("examples") or []:
            block += f"\n<i>e.g. {ex}</i>"
        # The "Examples:" section is real published usage, not MW's own
        # writing -- keep it visually separated with a blank line, and
        # keep its source attribution (already part of in_context).
        if mw.get("in_context"):
            word_label = escape_html(mw["word"].capitalize())
            block += f"\n\n<b>{word_label} in Context:</b> {mw['in_context']}"
        block += f'\n<a href="{escape_html(mw["link"])}">Full entry</a>'
        parts.append(block)
    else:
        parts.append("\n🇺🇸 <b>Merriam-Webster:</b> <i>couldn't fetch today's word</i>")

    if ox:
        # Oxford's source (see fetch_oxford) only ever provides a definition,
        # no example sentence -- nothing to add here even when it succeeds.
        parts.append(
            f"\n🇬🇧 <b>Oxford:</b> {escape_html(ox['word'])}\n"
            f"{escape_html(ox['definition'])}"
        )
    else:
        parts.append("\n🇬🇧 <b>Oxford:</b> <i>couldn't fetch today's word</i>")

    parts.append("\n@ELLSA_FUM\n@EllsaWordOfTheDayBot")

    return "\n".join(parts)


def send_message(chat_id, text):
    resp = requests.post(
        f"{API_URL}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
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
