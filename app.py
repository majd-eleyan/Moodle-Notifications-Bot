import os
import time
import json
import sqlite3
import threading
import requests
from flask import Flask, request

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")
MOODLE_URL = "https://moodle.alaqsa.edu.ps"

app = Flask(__name__)

# ---------------- DATABASE ----------------
conn = sqlite3.connect("database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    chat_id TEXT PRIMARY KEY,
    username TEXT,
    password TEXT,
    step TEXT,
    last_seen TEXT
)
""")
conn.commit()

# ---------------- TELEGRAM ----------------
def send(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text}
    )

# ---------------- MOODLE LOGIN ----------------
def login_moodle(session, username, password):
    try:
        login_page = session.get(MOODLE_URL + "/login/index.php")
        
        # استخراج token
        import re
        token = re.search(r'name="logintoken" value="(.*?)"', login_page.text)
        token = token.group(1) if token else ""

        payload = {
            "username": username,
            "password": password,
            "logintoken": token
        }

        res = session.post(MOODLE_URL + "/login/index.php", data=payload)

        if "loginerrors" in res.text.lower():
            return False
        
        return True
    except Exception as e:
        print("Login error:", e)
        return False

# ---------------- FETCH COURSES ----------------
def fetch_updates(username, password):
    session = requests.Session()
    updates = []

    if not login_moodle(session, username, password):
        return None

    print(f"LOGIN OK -> {username}")

    try:
        dash = session.get(MOODLE_URL + "/my/")
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(dash.text, "html.parser")

        courses = soup.select("a[href*='/course/view.php?id=']")

        visited = set()

        for c in courses:
            link = c.get("href")
            title = c.text.strip()

            if not link or link in visited:
                continue

            visited.add(link)

            course_page = session.get(link)
            csoup = BeautifulSoup(course_page.text, "html.parser")

            activities = csoup.select(".activityinstance a")

            for act in activities:
                name = act.text.strip()
                href = act.get("href")

                if name:
                    updates.append(f"{title}\n{name}\n{href}")

        return list(set(updates))

    except Exception as e:
        print("Fetch error:", e)
        return []

# ---------------- WEBHOOK ----------------
@app.route("/", methods=["POST"])
def webhook():
    data = request.json
    message = data.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "")

    cursor.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
    user = cursor.fetchone()

    if text == "/start":
        cursor.execute("REPLACE INTO users VALUES (?,?,?,?,?)",
                       (chat_id, "", "", "username", "[]"))
        conn.commit()
        send(chat_id, "أدخل الرقم الجامعي")

    elif user:
        step = user[3]

        if step == "username":
            cursor.execute("UPDATE users SET username=?, step='password' WHERE chat_id=?",
                           (text, chat_id))
            conn.commit()
            send(chat_id, "أدخل كلمة السر")

        elif step == "password":
            username = user[1]
            password = text

            result = fetch_updates(username, password)

            if result is None:
                send(chat_id, "بيانات غير صحيحة")
                return "ok"

            cursor.execute("""
            UPDATE users SET password=?, step='done', last_seen=?
            WHERE chat_id=?
            """, (password, json.dumps(result), chat_id))
            conn.commit()

            send(chat_id, "تم التسجيل بنجاح")

        else:
            send(chat_id, "البوت يعمل")

    else:
        send(chat_id, "اكتب /start")

    return "ok"

# ---------------- BACKGROUND CHECK ----------------
def checker():
    while True:
        cursor.execute("SELECT * FROM users WHERE step='done'")
        users = cursor.fetchall()

        for u in users:
            chat_id, username, password, _, last_seen = u
            last_seen = json.loads(last_seen)

            new = fetch_updates(username, password)

            if not new:
                continue

            diff = [x for x in new if x not in last_seen]

            for item in diff:
                send(chat_id, f"تحديث جديد:\n{item}")

            if diff:
                cursor.execute("UPDATE users SET last_seen=? WHERE chat_id=?",
                               (json.dumps(new), chat_id))
                conn.commit()

        time.sleep(300)

threading.Thread(target=checker, daemon=True).start()

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
