import asyncio
import httpx
import urllib.parse
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# インスタンス管理
INSTANCES = ["https://inv.tux.im", "https://invidious.nerdvpn.de", "https://invidious.flokinet.to"]

async def fetch_api(path: str, params: dict = None):
    async with httpx.AsyncClient(timeout=3.0) as client:
        for instance in INSTANCES:
            try:
                res = await client.get(f"{instance}/api/v1{path}", params=params)
                if res.status_code == 200:
                    return res.json(), instance
            except:
                continue
    return None, None

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/suggest")
async def suggest(keyword: str):
    """Googleの検索補完を利用"""
    url = f"http://www.google.com/complete/search?client=youtube&hl=ja&ds=yt&q={urllib.parse.quote(keyword)}"
    async with httpx.AsyncClient() as client:
        res = await client.get(url)
        # JSONP形式からリスト部分を抽出
        import json
        data = res.text[19:-1]
        suggestions = [i[0] for i in json.loads(data)[1]]
    return JSONResponse(suggestions)

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str, page: int = 1):
    results, _ = await fetch_api("/search", {"q": q, "page": page, "hl": "ja"})
    return templates.TemplateResponse("search.html", {"request": request, "results": results or [], "q": q})

@app.get("/watch", response_class=HTMLResponse)
async def watch(request: Request, v: str):
    video_data, instance = await fetch_api(f"/videos/{v}")
    return templates.TemplateResponse("watch.html", {
        "request": request, "video": video_data, "v": v, "instance": instance
    })
