from fastapi import FastAPI
from pydantic import BaseModel
import httpx
import sqlite3
import asyncio
import os
import random
from datetime import datetime, timedelta

app = FastAPI()

APPID = os.environ.get("APPID", "wxd185d88371e9916a")
APPSECRET = os.environ.get("APPSECRET", "d09c682a57a63790c1fae0f20978a17d")
TEMPLATE_ID = os.environ.get("TEMPLATE_ID", "wnPOFUCqyZgTiMY7pdHoNgyG65k3VBC38JXLuOfXdZw")
REMIND_HOURS = float(os.environ.get("REMIND_HOURS", "3"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

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

class RandomStartRequest(BaseModel):
    openid: str
    goals: list[str]

class FocusBreakdownRequest(BaseModel):
    openid: str
    tasks: list[str]

class AnalyzeRolesRequest(BaseModel):
    longterm_goal: str

class DailyTasksRequest(BaseModel):
    longterm_goal: str
    main_quest: str = ""
    role: str = ""


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
    return {"message": f"任务已保存，将在{req.remind_hours}小时后提醒"}


async def web_search(query: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
            )
        data = resp.json()
        abstract = data.get("AbstractText", "")
        related = [r.get("Text", "") for r in data.get("RelatedTopics", [])[:3]
                   if isinstance(r, dict) and r.get("Text")]
        result = abstract if abstract else "。".join(related)
        return result or f"未找到关于「{query}」的具体信息"
    except Exception:
        return f"搜索「{query}」时出错"


SEARCH_TOOL = [{
    "name": "web_search",
    "description": "当对任务内容不清楚时（如书名、电影名、项目名、专业术语等），用此工具搜索背景信息，帮助给出更准确的第一步",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        },
        "required": ["query"]
    }
}]


@app.post("/analyze-roles")
async def analyze_roles(req: AnalyzeRolesRequest):
    import json, re
    goal = req.longterm_goal.strip()
    if not goal:
        return {"error": "长期目标为空"}
    try:
        prompt = f"""用户的长期目标是：
{goal}

请分析这个长期目标背后适合长期扮演的具体角色。角色必须是具体身份、职业、创作身份或专业实践身份，例如：摄影师、写作者、导演、独立研究者、产品设计师、策展人。

要求：
1. 返回 3-5 个角色
2. 每个角色名字不超过 8 个字
3. 每个角色说明不超过 32 个字
4. 说明要强调这个角色如何帮助用户推进长期目标
5. 不要使用抽象人格词或泛管理身份，例如“推进者”“探索员”“执行官”“执行者”“项目管理者”“项目执行者”“项目制片主任”

只返回 JSON 数组，不要任何其他文字：
[{{"name": "角色名", "desc": "角色说明"}}]"""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 800,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        data = resp.json()
        text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
        if not text_block:
            raise Exception(f"Unexpected response: {data}")

        raw = text_block["text"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        banned_names = {"推进者", "探索员", "执行官", "执行者", "项目执行者", "项目管理者", "项目制片主任", "管理者"}
        roles = [
            role for role in json.loads(raw)
            if role.get("name") and role.get("desc") and role["name"] not in banned_names
        ]
        return {"roles": roles}
    except Exception as e:
        return {"error": str(e)}


@app.post("/daily-tasks")
async def daily_tasks(req: DailyTasksRequest):
    import json, re
    goal = req.longterm_goal.strip()
    main_quest = req.main_quest.strip()
    role = req.role.strip()
    if not goal and not main_quest:
        return {"error": "长期目标为空"}
    try:
        prompt = f"""用户的长期目标：
{goal or main_quest}

今日主线：
{main_quest or goal}

今日角色：
{role or "未设定"}

请把这个长期目标拆成今天可以完成的小任务。

要求：
1. 返回 3 个任务
2. 每个任务 15-45 分钟内能完成
3. 每个任务必须是具体动作，不要抽象建议
4. 任务要能推进长期目标，而不是泛泛自我管理
5. 不要包含“制定计划”这种空泛任务，除非任务具体到产出物
6. 每个任务不超过 28 个字
7. 使用和用户长期目标相同的语言；如果长期目标是英文，就返回英文任务

只返回 JSON 数组，不要任何其他文字：
["小任务1", "小任务2", "小任务3"]"""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 700,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        data = resp.json()
        text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
        if not text_block:
            raise Exception(f"Unexpected response: {data}")

        raw = text_block["text"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        tasks = [task for task in json.loads(raw) if isinstance(task, str) and task.strip()]
        return {"tasks": tasks[:3]}
    except Exception as e:
        return {"error": str(e)}


@app.post("/random-start")
async def random_start(req: RandomStartRequest):
    import json, re
    try:
        goals_text = "\n".join(f"- {g}" for g in req.goals)
        prompt = f"""我有以下几件想做的事：
{goals_text}

请为每件事给出一个具体的第一步，要求：
1. 只需要10-20分钟就能完成
2. 简单到不需要准备，立刻就能开始
3. 一句话说清楚，不超过20个字
4. 不要拆成多步，就一个动作

如果对某个任务不了解（例如是书名、电影、专业词汇），请先用 web_search 工具查询。

只返回一个JSON数组，不要任何其他文字：
[{{"goal": "原始目标", "first_step": "具体第一步"}}]"""

        messages = [{"role": "user", "content": prompt}]

        for _ in range(4):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1000,
                        "tools": SEARCH_TOOL,
                        "messages": messages
                    }
                )
            data = resp.json()

            if data.get("stop_reason") == "tool_use":
                tool_block = next((b for b in data["content"] if b.get("type") == "tool_use"), None)
                if tool_block and tool_block["name"] == "web_search":
                    search_result = await web_search(tool_block["input"]["query"])
                    messages.append({"role": "assistant", "content": data["content"]})
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool_block["id"],
                            "content": search_result
                        }]
                    })
                    continue

            text_block = next((b for b in data["content"] if b.get("type") == "text"), None)
            if not text_block:
                raise Exception(f"Unexpected response: {data}")

            raw = text_block["text"].strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            steps = json.loads(raw)
            picked = random.choice(steps)

            remind_at = (datetime.now() + timedelta(minutes=30)).isoformat()
            conn = sqlite3.connect("tasks.db")
            conn.execute(
                "INSERT INTO reminders (openid, tasks, remind_at) VALUES (?, ?, ?)",
                (req.openid, picked["first_step"], remind_at)
            )
            conn.commit()
            conn.close()

            return {"goal": picked["goal"], "first_step": picked["first_step"]}

        raise Exception("查询次数超限，请重试")
    except Exception as e:
        return {"error": str(e)}


@app.post("/focus-breakdown")
async def focus_breakdown(req: FocusBreakdownRequest):
    import json, re
    if not req.tasks:
        return {"error": "任务列表为空"}
    try:
        goals_text = "\n".join(f"- {t}" for t in req.tasks)
        prompt = f"""我今天计划专注完成以下任务：
{goals_text}

请为每个任务给出一个具体的第一步，要求：
1. 只需要10-20分钟就能完成
2. 简单到立刻就能开始，不需要任何准备
3. 一句话说清楚，不超过20个字
4. 就一个动作，不要拆成多步

如果对某个任务不了解（书名、电影、专业词汇等），请先用 web_search 工具查询。

只返回一个JSON数组，不要任何其他文字：
[{{"task": "原始任务名", "first_step": "具体第一步"}}]"""

        messages = [{"role": "user", "content": prompt}]

        for _ in range(4):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1000,
                        "tools": SEARCH_TOOL,
                        "messages": messages
                    }
                )
            data = resp.json()

            if data.get("stop_reason") == "tool_use":
                tool_block = next((b for b in data["content"] if b.get("type") == "tool_use"), None)
                if tool_block and tool_block["name"] == "web_search":
                    search_result = await web_search(tool_block["input"]["query"])
                    messages.append({"role": "assistant", "content": data["content"]})
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool_block["id"],
                            "content": search_result
                        }]
                    })
                    continue

            text_block = next((b for b in data["content"] if b.get("type") == "text"), None)
            if not text_block:
                raise Exception(f"Unexpected response: {data}")

            raw = text_block["text"].strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            steps = json.loads(raw)
            return {"steps": steps}

        raise Exception("查询次数超限，请重试")
    except Exception as e:
        return {"error": str(e)}


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
