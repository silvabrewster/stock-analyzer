# 📈 Stock Convergence Analyzer

A multi-source stock analysis engine that ranks stocks using a **7-signal consensus system** and sends a daily report via email.

---

## 🚀 Features

* 7-source scoring system:

  * Yahoo Finance analyst ratings
  * Zacks Strong Buy list
  * Morningstar-style fundamentals
  * Insider buying activity
  * Vanguard holdings
  * Earnings revisions
  * Relative strength vs S&P 500

* Automated daily run (Railway cron)

* Email reports (Resend API)

* Google Drive Excel logging

* Streak tracking + trend signals

---

## ⚙️ How It Works

1. Builds a stock universe
2. Pulls data from multiple sources
3. Scores each stock
4. Ranks top stocks
5. Sends email + saves results

---

## 🛠️ Setup (Railway)

### Environment Variables

Add these in Railway → Variables:

```
RESEND_KEY=your_resend_api_key
EMAIL_FROM=onboarding@resend.dev
EMAIL_TO=your_email
GOOGLE_CREDENTIALS={your full JSON}
DRIVE_FOLDER_ID=your_drive_folder_id
```

---

### Cron Job

```
0 14 * * *
```

Runs daily at **7:00 AM Pacific (14:00 UTC)**

---

### Start Command

```
python scheduler.py
```

---

## 📦 Install

```
pip install -r requirements.txt
```

---

## 📂 Project Structure

```
scheduler.py
analyzer.py
requirements.txt
```

---

## ⚠️ Notes

* Uses external APIs → may hit rate limits
* Designed for Railway deployment
* Not financial advice

---

## 📊 Output

* Ranked stock list
* Email report
* Excel log in Google Drive

---

## ⚠️ Disclaimer

For educational purposes only.
Not financial advice.
