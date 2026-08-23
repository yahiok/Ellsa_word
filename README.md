# ELLSA'S Word of the Day Bot

A Telegram bot that posts the Merriam-Webster and Oxford words of the day
to any group it's added to. Runs once a day via GitHub Actions -- no
server, no hosting bill, no credit card.

## How it works

There's no bot process running 24/7. Instead, GitHub runs `main.py` on a
daily schedule. Each run: checks Telegram for any chats the bot was
added to or removed from since last time, fetches both words of the
day, sends them to every chat on its list, then saves the updated list
back into `chats.json` in the repo (that's how it remembers between runs).

## Setup

### 1. Create the bot in Telegram

1. Open Telegram, message **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`.
3. When it asks for a **name**, send: `ELLSA'S Word of the Day bot`
   (this is the display name -- apostrophes and spaces are fine here).
4. When it asks for a **username**, it needs to end in "bot" and can't
   have spaces or apostrophes -- try something like `EllsaWordOfTheDayBot`.
5. BotFather replies with a **token** (looks like `123456789:AAH...`).
   Save it somewhere -- you'll paste it into GitHub in step 3. Keep it
   private; anyone with this token can control the bot.
6. Still in BotFather, send `/setjoingroups` and make sure it's enabled
   (it is by default) so the bot can be added to groups.
7. Optional but recommended: send `/setprivacy` and choose **Disable**
   for this bot. Group bots can't read group messages by default; you
   don't need that for this bot, but disabling privacy mode makes sure
   nothing about group visibility gets in the way later if you extend it.

### 2. Create the GitHub repo

1. On [github.com](https://github.com), create a new repository (public
   is simplest -- public repos get unlimited free GitHub Actions
   minutes; private also works fine at this usage level, just with a
   monthly minutes allowance).
2. Upload every file from this project **keeping the folder structure**
   -- `.github/workflows/daily.yml` needs to stay at that exact path
   for GitHub to recognize it as a workflow.

### 3. Add your bot token as a secret

1. In your new repo: **Settings → Secrets and variables → Actions →
   New repository secret**.
2. Name: `TELEGRAM_BOT_TOKEN`
3. Value: the token BotFather gave you.
4. Save. This keeps the token out of your code -- it's encrypted and
   never shown in logs, even in a public repo.

### 4. Test it

1. Go to the **Actions** tab in your repo → **Send Word of the Day** →
   **Run workflow** (this is the manual trigger, for testing outside
   the daily schedule).
2. Check the run's logs. First run will likely say "No chats to send
   to yet" -- that's expected, since no one has added the bot anywhere.
3. In Telegram, message your bot directly (search its username, hit
   Start) **or** add it to a group.
4. Run the workflow manually again -- this time it should pick up that
   chat and send the word of the day to it.

### 5. Let it run

Once step 4 works, you're done -- it'll fire automatically every day at
06:00 UTC (9:30 AM Iran time). Anyone can add the bot to a new group at
any time; it'll start posting there from the next scheduled run onward.

## Adjusting the schedule

Edit the `cron` line in `.github/workflows/daily.yml`.
[crontab.guru](https://crontab.guru) is a good way to build/check the
expression without memorizing cron syntax.

## If the Oxford word stops showing up

This is the one part of the bot worth knowing about. Merriam-Webster
publishes an official RSS feed for word of the day, so that part is
solid. Oxford doesn't publish anything comparable anymore (their old
public API, Lexico, shut down in 2022) -- so this bot instead reads the
word-of-the-day box on Oxford University Press's own corporate
homepage (`corp.oup.com`), which happens to be plain HTML.

If OUP ever redesigns that page, `fetch_oxford()` in `main.py` might
stop finding the word. The bot is built to degrade gracefully if that
happens -- Merriam-Webster still gets sent, Oxford is just silently
skipped for that day (check the Action's logs to confirm this is what
happened) -- but the Oxford source is the more fragile of the two, and
is the first place to look if something looks off. Bring the run logs
back to this conversation and I can help fix the parsing.
