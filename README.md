# Hot Wheels MRP Watcher — Setup Guide

Ye bot Amazon.in aur FirstCry pe "hot wheels" search karta hai, aur jab bhi
koi listing in MRP bands ke aas-paas milti hai, Telegram pe alert bhejta hai:

| Category        | MRP  |
|------------------|------|
| Mainline          | ₹179 |
| Silver Series     | ₹299 |
| Premium Series    | ₹549 |

(Thoda upar-neeche tolerance already built-in hai script me.)

Runs automatically every **5 minutes**, 24/7, via GitHub Actions — free,
no server chahiye.

---

## Step 1 — Telegram Bot Banao (5 min)

1. Telegram me `@BotFather` ko open karo.
2. `/newbot` bhejo, naam aur username set karo (username `_bot` se end hona chahiye).
3. BotFather tumhe ek **token** dega, kuch aisa dikhega:
   `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
   Ye save kar lo — isse `TELEGRAM_BOT_TOKEN` bolenge.
4. Ab apne bot ko Telegram me search karke `/start` bhejo (isse bot ko pehla message milta hai, jaruri hai).
5. Apna Chat ID nikalne ke liye browser me ye URL kholo (token apna daalna):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Response me `"chat":{"id":XXXXXXXXX...}` dikhega — ye number `TELEGRAM_CHAT_ID` hai.

---

## Step 2 — GitHub Repo Banao

1. GitHub pe naya **private** repo banao (e.g. `hotwheels-watcher`).
2. Is folder ke saare files (`hotwheels_bot.py`, `requirements.txt`, `seen.json`,
   `.github/workflows/hotwheels.yml`) us repo me push kar do:

```bash
cd hotwheels-bot
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/hotwheels-watcher.git
git push -u origin main
```

---

## Step 3 — Secrets Add Karo

Repo me: **Settings → Secrets and variables → Actions → New repository secret**

Add karo:
- `TELEGRAM_BOT_TOKEN` → jo Step 1 me mila
- `TELEGRAM_CHAT_ID` → jo Step 1 me mila

---

## Step 4 — Test Run

Repo ke **Actions** tab me jao → "Hot Wheels MRP Watcher" workflow select karo →
**Run workflow** button se manually trigger karo. Logs check karo ki scraping
sahi chal rahi hai. Agar sab thik hai, tumhe Telegram pe pehla alert milega
(agar koi matching listing currently available hai).

Uske baad ye apne aap har 15 min me chalega, background me, bina kuch kiye.

---

## Important Notes

- **Amazon scraping unreliable ho sakti hai** — Amazon bot-detection/captcha
  use karta hai, kabhi-kabhi results empty aayenge. Ye normal hai, agla run
  try karega.
- **Duplicate alerts nahi aayenge** — `seen.json` file me already-notified
  links save hote hain, workflow khud commit kar deta hai.
- Agar zyada frequently chahiye (5 min se kam), GitHub free tier me schedule
  thoda delay ho sakta hai high-load ke time — ye GitHub ki limitation hai,
  koi paid tier lena padega agar exact 24/7 real-time chahiye.
- Personal use ke liye hi rakho, requests ko rate-limit mat karo warna IP
  block ho sakta hai.
