from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import httpx
import asyncio
import json
import random
from datetime import datetime

app = FastAPI()

# テンプレートの設定
templates = Jinja2Templates(directory="templates")
templates.env.add_extension('jinja2.ext.do')

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
        # 検索キーワードに"shorts"を付与して精度を上げる
        query_q = q if type != "short" else f"{q} shorts"
        
        data = await fetch_invidious("/search", {"q": query_q, "page": page, "type": search_type})
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

@app.get("/shorts/{v}", response_class=HTMLResponse)
async def shorts_player(request: Request, v: str):
    try:
        # 動画詳細とコメントを並行して取得
        video_task = fetch_invidious(f"/videos/{v}")
        comment_task = fetch_invidious(f"/comments/{v}")
        video_data, comment_data = await asyncio.gather(video_task, comment_task, return_exceptions=True)

        if isinstance(video_data, Exception): raise video_data
        
        # 動画URLのリスト作成
        video_urls = [fmt.get("url") for fmt in video_data.get("formatStreams", [])]
        if not video_urls:
            adaptive = video_data.get("adaptiveFormats", [])
            video_urls = [fmt.get("url") for fmt in adaptive if "video" in fmt.get("type", "")]

        return templates.TemplateResponse("short.html", {
            "request": request,
            "videoid": v,
            "video_title": video_data.get("title"),
            "videourls": video_urls,
            "author": video_data.get("author"),
            "view_count": video_data.get("viewCount", 0),
            "like_count": video_data.get("likeCount", 0),
            "description": video_data.get("descriptionHtml", "").replace("\n", "<br>"),
            "comments": comment_data.get("comments", []) if not isinstance(comment_data, Exception) else []
        })
    except Exception as e:
        return HTMLResponse(content=f"Error: {str(e)}", status_code=500)

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

        response = templates.TemplateResponse("watch.html", {
            "request": request,
            "videoid": v,
            "video_title": video_data.get("title"),
            "videourls": video_urls,
            "streamUrls": stream_urls,
            "author": video_data.get("author"),
            "author_id": video_data.get("authorId"),
            "author_icon": video_data.get("authorThumbnails", [{"url": ""}])[-1]["url"],
            "subscribers_count": video_data.get("subCountText", "非公開"),
            "view_count": video_data.get("viewCount", 0),
            "like_count": video_data.get("likeCount", 0),
            "description": video_data.get("descriptionHtml", "").replace("\n", "<br>"),
            "recommended_videos": recommended,
            "comments": comment_data.get("comments", []) if not isinstance(comment_data, Exception) else []
        })

        # --- 視聴履歴保存ロジック ---
        history_cookie = request.cookies.get("history", "[]")
        try:
            history = json.loads(history_cookie)
        except:
            history = []

        # 重複削除して先頭に追加
        history = [item for item in history if item.get("videoId") != v]
        history.append({
            "videoId": v,
            "title": video_data.get("title"),
            "author": video_data.get("author"),
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        # 直近50件に制限
        if len(history) > 50:
            history = history[-50:]

        response.set_cookie(key="history", value=json.dumps(history), max_age=2592000, httponly=True)
        return response

    except Exception as e:
        return HTMLResponse(content=f"Error: {str(e)}", status_code=500)

@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    history_data = request.cookies.get("history", "[]")
    try:
        history_list = json.loads(history_data)
    except:
        history_list = []
    history_list.reverse() # 新しい順
    return templates.TemplateResponse("history.html", {"request": request, "history": history_list})

@app.get("/history/clear")
async def clear_history():
    response = RedirectResponse(url="/history")
    response.delete_cookie("history")
    return response

@app.get("/playlist", response_class=HTMLResponse)
async def playlist(request: Request, list: str = Query(...)):
    try:
        data = await fetch_invidious(f"/playlists/{list}")
        return templates.TemplateResponse("playlist.html", {
            "request": request,
            "title": data.get("title"),
            "playlistId": list,
            "author": data.get("author"),
            "authorId": data.get("authorId"),
            "videos": data.get("videos", []),
            "description": data.get("descriptionHtml", "")
        })
    except Exception as e:
        return HTMLResponse(content=f"Error: {str(e)}", status_code=500)

@app.get("/channel/{ucid}", response_class=HTMLResponse)
async def channel(request: Request, ucid: str, sort_by: str = "newest", tab: str = "videos"):
    try:
        # 並行してデータを取得
        tasks = [
            fetch_invidious(f"/channels/{ucid}", {"sort_by": sort_by}),
            fetch_invidious(f"/channels/{ucid}/shorts"),
            fetch_invidious(f"/channels/{ucid}/playlists"),
            fetch_invidious(f"/channels/{ucid}/community")
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        channel_data = results[0] if not isinstance(results[0], Exception) else {}
        shorts_data = results[1] if not isinstance(results[1], Exception) else {}
        playlists_data = results[2] if not isinstance(results[2], Exception) else {}
        community_data = results[3] if not isinstance(results[3], Exception) else {}

        # プレイリストの整形
        playlists = []
        for pl in playlists_data.get("playlists", []):
            thumb = pl.get("playlistThumbnail", "")
            if thumb and not thumb.startswith("http"):
                thumb = f"https://img.youtube.com/vi/{thumb}/mqdefault.jpg"
            playlists.append({
                "id": pl.get("playlistId", ""),
                "title": pl.get("title", ""),
                "video_count": pl.get("videoCount", 0),
                "thumbnail": thumb,
            })

        # コミュニティ投稿の整形
        community = []
        for post in community_data.get("comments", []):
            community.append({
                "id": post.get("commentId", ""),
                "content": post.get("contentHtml", "").replace("\n", "<br>"),
                "published_text": post.get("publishedText", ""),
                "likes": post.get("likeCount", 0),
                "author": channel_data.get("author"),
                "author_icon": channel_data.get("authorThumbnails", [{"url": ""}])[-1]["url"],
            })

        return templates.TemplateResponse("channel.html", {
            "request": request,
            "ucid": ucid,
            "author": channel_data.get("author"),
            "author_icon": channel_data.get("authorThumbnails", [{"url": ""}])[-1]["url"],
            "sub_count": channel_data.get("subCountText", "非公開"),
            "description": channel_data.get("descriptionHtml", ""),
            "videos": channel_data.get("latestVideos", []),
            "shorts": shorts_data.get("videos", []),
            "playlists": playlists,
            "community": community,
            "sort_by": sort_by,
            "tab": tab
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

@app.get("/games", response_class=HTMLResponse)
async def read_games(request: Request):
    return templates.TemplateResponse("games.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
