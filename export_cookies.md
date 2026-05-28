# How to Export YouTube Cookies

YouTube requires cookie authentication to prove you're a real browser.
Follow **one** of these methods:

---

## Method 1 — Chrome/Edge extension (easiest)

1. Install the **"Get cookies.txt LOCALLY"** extension:
   - [Chrome](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - [Edge](https://microsoftedge.microsoft.com/addons/detail/get-cookiestxt-locally/helpmhpcaghnnbdmcnnfhbfmdcpnmfmn)

2. Open [youtube.com](https://youtube.com) and **sign in** to your Google account

3. Click the extension icon → **Export** → save as `cookies.txt`

4. Place `cookies.txt` next to `bot.py`, OR send it to the bot with `/setcookies`

---

## Method 2 — Firefox extension

1. Install **"cookies.txt"** from Firefox Add-ons

2. Sign in to YouTube, click the extension → **Current Site** → save as `cookies.txt`

---

## Method 3 — yt-dlp CLI (if you have it locally)

```bash
yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download "https://youtube.com"
```

---

## Uploading cookies to the bot (on a hosted server)

Once the bot is running, send the `cookies.txt` file in Telegram chat:
- Open the chat with your bot
- Attach the `cookies.txt` file
- The bot will automatically save and use it

Or use `/delcookies` to remove cookies if they expire.

---

## Tips

- Cookies expire periodically — re-export if you get bot-detection errors again
- Use a **dedicated Google account** for the bot, not your personal one
- The cookies file is stored as `cookies.txt` next to `bot.py` on the server
