from fastapi import FastAPI
from pydantic import BaseModel
import httpx
import sqlite3
import asyncio
import os
from datetime import datetime, timedelta

app = FastAPI()

APPID = os.environ.get("APPID", "wxd185d88371e9916a")
APPSECRET = os.environ.get("APPSECRET", "d09c682a57a63790c1fae0f20978a17d")
TEMPLATE_ID = os.environ.get("TEMPLATE_ID", "wnPOFUCqyZgTiMY7pdHoNgyG65k3VBC38JXLuOfXdZw")
REMIND_HOURS = float(os.environ.get("REMIND_HOURS", "3"))

def init_db():
    conn = sqlite3.connect("tasks.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            openid TEXT NOT NULL,
            tasks TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            sent INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

init_db()


class LoginRequest(BaseModel):
    code: str

class TaskRequest(BaseModel):
    openid: str
    tasks: list[str]
    remind_hours: float = REMIND_HOURS


@app.post("/login")
async def login(req: LoginRequest):
    url = (
        f"https://api.weixin.qq.com/sns/jscode2session"
        f"?appid={APPID}&secret={APPSECRET}"
        f"&js_code={req.code}&grant_type=authorization_code"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    data = resp.json()
    openid = data.get("openid", "")
    if not openid:
        return {"error": "获取openid失败", "detail": data}
    return {"openid": openid}


@app.post("/submit-tasks")
async def submit_tasks(req: TaskRequest):
    remind_at = (datetime.now() + timedelta(hours=req.remind_hours)).isoformat()
    tasks_text = "\n".join(req.tasks)
    conn = sqlite3.connect("tasks.db")
    conn.execute(
        "INSERT INTO reminders (openid, tasks, remind_at) VALUES (?, ?, ?)",
        (req.openid, tasks_text, remind_at)
    )
    conn.commit()
    conn.close()
    return {"message": f"任务已保存，将在{REMIND_HOURS}小时后提醒"}


async def get_access_token() -> str:
    url = (
        f"https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={APPID}&secret={APPSECRET}"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    return resp.json().get("access_token", "")


async def send_reminder(openid: str, tasks: str):
    token = await get_access_token()
    url = f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}"
    task_summary = tasks.replace("\n", "，")[:20]
    payload = {
        "touser": openid,
        "template_id": TEMPLATE_ID,
        "page": "pages/progress/progress",
        "data": {
            "phrase8": {"value": "请更新进度"},
            "thing4": {"value": task_summary}
        }
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)
    print(f"[{datetime.now()}] 已发送提醒给 {openid}")


async def reminder_loop():
    while True:
        conn = sqlite3.connect("tasks.db")
        now = datetime.now().isoformat()
        rows = conn.execute(
            "SELECT id, openid, tasks FROM reminders WHERE remind_at <= ? AND sent = 0",
            (now,)
        ).fetchall()
        for row_id, openid, tasks in rows:
            await send_reminder(openid, tasks)
            conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup():
    asyncio.create_task(reminder_loop())
