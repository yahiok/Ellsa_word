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
