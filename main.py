from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import httpx
import asyncio
import json
import random

app = FastAPI()

# テンプレートの設定
templates = Jinja2Templates(directory="templates")

# 使用するInvidiousインスタンスのリスト
INVIDIOUS_INSTANCES = [
    'https://inv.nadeko.net/',
    'https://invidious.f5.si/',
    'https://invidious.lunivers.trade/',
    'https://invidious.ducks.party/',
    'https://iv.melmac.space/',
    'https://invidious.nerdvpn.de/',
]

async def fetch_invidious(endpoint: str, params: dict = None):
    instances = list(INVIDIOUS_INSTANCES)
    random.shuffle(instances)
    
    last_error = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        for instance in instances:
            try:
                url = f"{instance.rstrip('/')}/api/v1{endpoint}"
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                last_error = e
                continue
    
    raise last_error if last_error else Exception("All instances failed")

## --- ルーティング ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(...), page: int = 1, type: str = "video"):
    try:
        # Invidious APIのtype指定に合わせて調整 (Shortsはvideoとして扱い検索ワードで調整されることもあるが基本はvideo)
        search_type = type if type != "short" else "video"
        # Shortsの場合は検索ワードに反映させるなどの処理も可能だが、ここではAPIのtypeに準拠
        
        data = await fetch_invidious("/search", {"q": q, "page": page, "type": search_type})
        results = []
        for item in data:
            results.append({
                "type": item.get("type"),
                "videoId": item.get("videoId"),
                "playlistId": item.get("playlistId"),
                "authorId": item.get("authorId"),
                "title": item.get("title"),
                "lengthSeconds": item.get("lengthSeconds"),
                "author": item.get("author"),
                "authorThumbnails": item.get("authorThumbnails"),
                "videoThumbnails": item.get("videoThumbnails"),
                "viewCountText": item.get("viewCountText"),
                "viewCount": item.get("viewCount"),
                "publishedText": item.get("publishedText"),
                "subCountText": item.get("subCountText"),
                "videoCount": item.get("videoCount")
            })
            
        return templates.TemplateResponse("search.html", {
            "request": request, 
            "query": q, 
            "results": results,
            "type": type,
            "page": page
        })
    except Exception as e:
        return templates.TemplateResponse("search.html", {
            "request": request, 
            "query": q, 
            "results": [],
            "type": type,
            "page": page
        })

@app.get("/watch", response_class=HTMLResponse)
async def watch(request: Request, v: str = Query(...)):
    try:
        # 動画詳細とコメントを並行して取得
        video_task = fetch_invidious(f"/videos/{v}")
        comment_task = fetch_invidious(f"/comments/{v}")
        video_data, comment_data = await asyncio.gather(video_task, comment_task, return_exceptions=True)

        if isinstance(video_data, Exception): raise video_data
        
        # ストリーム解析
        stream_urls = []
        video_urls = []
        
        # 音声のみのストリームを1つ確保（同期用）
        audio_url = None
        adaptive = video_data.get("adaptiveFormats", [])
        for fmt in adaptive:
            if "audio" in fmt.get("type", ""):
                audio_url = fmt.get("url")
                break

        # 混合ストリーム(mp4等)
        for fmt in video_data.get("formatStreams", []):
            stream_urls.append({
                "url": fmt.get("url"),
                "resolution": fmt.get("qualityLabel"),
                "format": "mp4/mixed",
                "audioUrl": ""
            })
            video_urls.append(fmt.get("url"))

        # videoOnly(webm等)ストリームの追加
        for fmt in adaptive:
            if "video" in fmt.get("type", "") and "webm" in fmt.get("container", ""):
                stream_urls.append({
                    "url": fmt.get("url"),
                    "resolution": fmt.get("qualityLabel"),
                    "format": "webm/videoOnly",
                    "audioUrl": audio_url # 同期再生用に音声を紐付け
                })

        if not video_urls and adaptive:
            video_urls = [fmt.get("url") for fmt in adaptive if "video" in fmt.get("type", "")]

        recommended = []
        for rec in video_data.get("recommendedVideos", []):
            recommended.append({
                "video_id": rec.get("videoId"),
                "title": rec.get("title"),
                "author": rec.get("author"),
                "view_count_text": rec.get("viewCountText")
            })

        return templates.TemplateResponse("watch.html", {
            "request": request,
            "videoid": v,
            "video_title": video_data.get("title"),
            "videourls": video_urls,
            "streamUrls": stream_urls,
            "author": video_data.get("author"),
            "author_icon": video_data.get("authorThumbnails", [{"url": ""}])[-1]["url"],
            "subscribers_count": video_data.get("subCountText", "非公開"),
            "view_count": video_data.get("viewCount", 0),
            "like_count": video_data.get("likeCount", 0),
            "description": video_data.get("descriptionHtml", "").replace("\n", "<br>"),
            "recommended_videos": recommended,
            "comments": comment_data.get("comments", []) if not isinstance(comment_data, Exception) else []
        })
    except Exception as e:
        return HTMLResponse(content=f"Error: {str(e)}", status_code=500)

@app.get("/suggest")
async def suggest(keyword: str):
    instances = list(INVIDIOUS_INSTANCES)
    random.shuffle(instances)
    async with httpx.AsyncClient() as client:
        for instance in instances:
            try:
                resp = await client.get(f"{instance.rstrip('/')}/api/v1/search/suggestions", params={"q": keyword})
                if resp.status_code == 200:
                    return resp.json().get("suggestions", [])
            except: continue
    return []

@app.get("/proxy/thumb")
async def proxy_thumb(v: str):
    thumb_url = f"https://i.ytimg.com/vi/{v}/mqdefault.jpg"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(thumb_url)
            return Response(content=resp.content, media_type="image/jpeg")
        except: return Response(status_code=404)

@app.get("/thumbnail")
async def thumbnail(v: str):
    return await proxy_thumb(v)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
