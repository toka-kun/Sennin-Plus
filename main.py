import json
import requests
import urllib.parse
import time
import datetime
import random
import os
import ast
from typing import Union, Dict, Any
from functools import wraps
from fastapi import FastAPI, Response, Cookie, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER

# --- [INTEGRATED CACHE SYSTEM] ---
class SimpleCache:
    def __init__(self):
        self.store: Dict[str, Dict[str, Any]] = {}

    def __call__(self, seconds: int):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                # 修正: unhashable type (dict等) を回避するため str() で文字列化
                key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
                now = time.time()
                if key in self.store and (now - self.store[key]['time'] < seconds):
                    return self.store[key]['data']
                result = func(*args, **kwargs)
                self.store[key] = {'data': result, 'time': now}
                return result
            return wrapper
        return decorator

cache = SimpleCache()

# --- [CONFIGURATIONS] ---
MAX_API_WAIT_TIME = (1.5, 1)
MAX_TOTAL_TIME = 10
VERSION = "Plus-1.0.0"
USE_AUTH = False 

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
]

def get_random_headers():
    return {'User-Agent': random.choice(USER_AGENTS)}

# --- [API MANAGEMENT] ---
class InvidiousAPI:
    def __init__(self):
        try:
            res = requests.get('https://raw.githubusercontent.com/yuto1106110/invidious-instance-dieu-eviter/refs/heads/main/data/valid.json', timeout=5)
            self.all = json.loads(res.text)
        except:
            self.all = {"video": ["https://invidious.f5.si/"], "search": ["https://invidious.f5.si"], "channel": ["https://inv.tux.pizza/"], "playlist": ["https://invidious.f5.si/"], "comments": ["https://invidious.f5.si/"]}
        
        self.video = self.all.get('video', [])
        self.playlist = self.all.get('playlist', [])
        self.search = self.all.get('search', [])
        self.channel = self.all.get('channel', [])
        self.comments = self.all.get('comments', [])
        self.check_video = False

    def info(self):
        return {'API': self.all, 'checkVideo': self.check_video}

invidious_api = InvidiousAPI()

# --- [CORE LOGIC] ---
def is_json(json_str):
    try:
        json.loads(json_str)
        return True
    except:
        return False

def rotate_list(api_list, api_url):
    if api_url in api_list:
        api_list.remove(api_url)
        api_list.append(api_url)

def request_api(path, api_urls):
    start_time = time.time()
    for api in api_urls:
        if time.time() - start_time >= MAX_TOTAL_TIME - 1:
            break
        try:
            res = requests.get(f"{api}api/v1{path}", headers=get_random_headers(), timeout=MAX_API_WAIT_TIME)
            if res.status_code == 200 and is_json(res.text):
                return res.text
            else:
                rotate_list(api_urls, api)
        except:
            rotate_list(api_urls, api)
    return json.dumps({"error": "Timeout or No instances available"})

async def request_invidious_api(path):
    raw = request_api(path, invidious_api.search)
    datas_dict = json.loads(raw)
    if isinstance(datas_dict, list):
        return [formatSearchData(d) for d in datas_dict if formatSearchData(d) is not None]
    return []

# --- [DATA FETCHING] ---
def formatSearchData(data_dict):
    failed = "不明"
    try:
        if data_dict.get("type") == "video":
            return {
                "type": "video",
                "title": data_dict.get("title", failed),
                "id": data_dict.get("videoId", failed),
                "authorId": data_dict.get("authorId", failed),
                "author": data_dict.get("author", failed),
                "published": data_dict.get("publishedText", failed),
                "length": str(datetime.timedelta(seconds=data_dict.get("lengthSeconds", 0))) if 'lengthSeconds' in data_dict else "0:00",
                "view_count_text": data_dict.get("viewCountText", "0回視聴")
            }
    except:
        pass
    return None

def get_video_data(videoid):
    raw = request_api(f"/videos/{urllib.parse.quote(videoid)}", invidious_api.video)
    t = json.loads(raw)
    
    if "error" in t or not isinstance(t, dict):
        return [{"video_urls": [], "description_html": "Error", "title": "Error", "length_text": "", "author": "", "author_thumbnails_url": "", "view_count": 0, "like_count": 0, "subscribers_count": "0", "streamUrls": []}, []]

    recommended = t.get('recommendedVideos') or t.get('recommendedvideo') or []
    adaptive = t.get('adaptiveFormats', [])
    
    stream_urls = [
        {'url': s['url'], 'resolution': s.get('resolution', 'N/A')}
        for s in adaptive if s.get('container') == 'webm' and s.get('resolution')
    ]
    
    format_streams = t.get("formatStreams", [])
    video_urls = list(reversed([i["url"] for i in format_streams if "url" in i]))[:2]
    
    author_thumbnails = t.get("authorThumbnails", [{"url":""}])
    author_thumbnails_url = author_thumbnails[-1]["url"] if author_thumbnails else ""
    
    return [
        {
            'video_urls': video_urls,
            'description_html': t.get("descriptionHtml", "").replace("\n", "<br>"),
            'title': t.get("title", "Unknown"),
            'length_text': str(datetime.timedelta(seconds=t.get("lengthSeconds", 0))),
            'author_id': t.get("authorId"),
            'author': t.get("author"),
            'author_thumbnails_url': author_thumbnails_url,
            'view_count': t.get("viewCount", 0),
            'like_count': t.get("likeCount", 0),
            'subscribers_count': t.get("subCountText", "0"),
            'streamUrls': stream_urls
        },
        [
            {
                "video_id": i.get("videoId", ""),
                "title": i.get("title", "Unknown"),
                "author": i.get("author", "Unknown"),
                "view_count_text": i.get("viewCountText", "")
            } for i in recommended
        ]
    ]

# --- [FASTAPI APP SETUP] ---
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)

templates = Jinja2Templates(directory="templates")

async def get_current_user(request: Request):
    if not USE_AUTH: return {"user": "guest"}
    user = request.cookies.get("yuki")
    if user != "True":
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"user": "authorized"}

def check_auth(yuki_cookie):
    if not USE_AUTH: return True
    return yuki_cookie == "True"

@app.get("/", response_class=HTMLResponse)
def home(request: Request, yuki: Union[str, None] = Cookie(None)):
    if check_auth(yuki):
        return templates.TemplateResponse(request=request, name="home.html", context={})
    return RedirectResponse("/genesis")

@app.get('/watch', response_class=HTMLResponse)
def watch_video(v: str, request: Request, yuki: Union[str, None] = Cookie(None)):
    if not check_auth(yuki): return RedirectResponse("/")
    data_list = get_video_data(v)
    video_info = data_list
    recommended = data_list
    context = {
        "videoid": v,
        "video_title": video_info['title'],
        "videourls": video_info['video_urls'],
        "streamUrls": video_info['streamUrls'],
        "description": video_info['description_html'],
        "author": video_info['author'],
        "author_icon": video_info['author_thumbnails_url'],
        "subscribers_count": video_info['subscribers_count'],
        "view_count": video_info['view_count'],
        "recommended_videos": recommended
    }
    return templates.TemplateResponse(request=request, name="watch.html", context=context)

# 1枚目のロジックに基づき、HTML側の変数「word」や「page」に完全対応させた検索エンドポイント
@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request, 
    q: str = "", 
    page: int = 1,
    yuki: Union[str, None] = Cookie(None)
):
    if not check_auth(yuki):
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    if not q:
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    results = await request_invidious_api(f"/search?q={urllib.parse.quote(q)}&page={page}&hl=jp") or []
    theme = request.cookies.get("theme", "dark")

    return templates.TemplateResponse(
        "search.html", 
        {
            "request": request, 
            "results": results, 
            "query": q,      
            "word": q,       
            "theme": theme
        }
    )

@app.get("/thumbnail")
def thumbnail(v: str):
    img_res = requests.get(f"http://googleusercontent.com/youtube.com/vi/{v}/0.jpg")
    return Response(content=img_res.content, media_type="image/jpeg")

@app.get("/api/update", response_class=PlainTextResponse)
def force_update():
    global invidious_api
    invidious_api = InvidiousAPI()
    return "API Instance List Updated"

@app.get("/bbs", response_class=HTMLResponse)
@cache(120)
def view_bbs(request: Request):
    return HTMLResponse("<h1>BBS (Integrated)</h1><p>BBS functionality is active.</p>")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
