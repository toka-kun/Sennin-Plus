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

async def fetch_invidious(endpoint: str, params: dict = None, force_instance: str = None):
    # force_instanceがある場合はそれを最優先にし、それ以外をシャッフルして繋げる
    if force_instance:
        instances = [force_instance] + [i for i in INVIDIOUS_INSTANCES if i != force_instance]
    else:
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
            except httpx.TimeoutException as e:
                # タイムアウト時は即座にTimeoutErrorを投げて専用ページへ飛ばす選択肢もあるが、
                # ここでは次のインスタンスを試行し、全てダメなら最後に判定する
                last_error = e
                continue
            except Exception as e:
                last_error = e
                continue
    
    raise last_error if last_error else Exception("All instances failed")

## --- ルーティング ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(...), page: int = 1, type: str = "video", force_instance: str = Query(None)):
    try:
        # Invidious APIのtype指定に合わせて調整 (Shortsはvideoとして扱い検索ワードで調整されることもあるが基本はvideo)
        search_type = type if type != "short" else "video"
        # 検索キーワードに"shorts"を付与して精度を上げる
        query_q = q if type != "short" else f"{q} shorts"
        
        data = await fetch_invidious("/search", {"q": query_q, "page": page, "type": search_type}, force_instance=force_instance)
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
    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception as e:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

@app.get("/shorts/{v}", response_class=HTMLResponse)
async def shorts_player(request: Request, v: str, force_instance: str = Query(None)):
    try:
        # 動画詳細とコメントを並行して取得
        video_task = fetch_invidious(f"/videos/{v}", force_instance=force_instance)
        comment_task = fetch_invidious(f"/comments/{v}", force_instance=force_instance)
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
    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception as e:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

@app.get("/watch", response_class=HTMLResponse)
async def watch(request: Request, v: str = Query(...), force_instance: str = Query(None)):
    try:
        # 動画詳細とコメントを並行して取得
        video_task = fetch_invidious(f"/videos/{v}", force_instance=force_instance)
        comment_task = fetch_invidious(f"/comments/{v}", force_instance=force_instance)
        video_data, comment_data = await asyncio.gather(video_task, comment_task, return_exceptions=True)

        if isinstance(video_data, Exception): raise video_data
        
        # ストリーム解析
        stream_urls = []
        video_urls = []
        
        # 音声のみのストリームを1つ確保（同期用）
        audio_url = None
        adaptive = video_data.get("adaptiveFormats", [])
        for fmt in adaptive:
            if "audio" in fmt.get("type", "") and fmt.get("language") == "ja":
                audio_url = fmt.get("url")
                break
        if not audio_url:
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

    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception as e:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

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
async def playlist(request: Request, list: str = Query(...), force_instance: str = Query(None)):
    try:
        data = await fetch_invidious(f"/playlists/{list}", force_instance=force_instance)
        return templates.TemplateResponse("playlist.html", {
            "request": request,
            "title": data.get("title"),
            "playlistId": list,
            "author": data.get("author"),
            "authorId": data.get("authorId"),
            "videos": data.get("videos", []),
            "description": data.get("descriptionHtml", "")
        })
    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception as e:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

@app.get("/channel/{ucid}", response_class=HTMLResponse)
async def channel(request: Request, ucid: str, sort_by: str = "newest", tab: str = "videos", force_instance: str = Query(None)):
    try:
        # 並行してデータを取得
        tasks = [
            fetch_invidious(f"/channels/{ucid}", {"sort_by": sort_by}, force_instance=force_instance),
            fetch_invidious(f"/channels/{ucid}/shorts", force_instance=force_instance),
            fetch_invidious(f"/channels/{ucid}/playlists", force_instance=force_instance),
            fetch_invidious(f"/channels/{ucid}/community", force_instance=force_instance)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 最初のタスク（チャンネル基本情報）が致命的エラーなら例外を投げる
        if isinstance(results, httpx.TimeoutException): raise results
        if isinstance(results, Exception): raise results

        channel_data = results
        shorts_data = results if not isinstance(results, Exception) else {}
        playlists_data = results if not isinstance(results, Exception) else {}
        community_data = results if not isinstance(results, Exception) else {}

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
    except httpx.TimeoutException:
        return templates.TemplateResponse("apitimeout.html", {"request": request})
    except Exception as e:
        return templates.TemplateResponse("apiallerror.html", {"request": request, "instances": INVIDIOUS_INSTANCES})

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

@app.get("/block.html", response_class=HTMLResponse)
async def read_block(request: Request):
    return templates.TemplateResponse("block.html", {"request": request})

@app.get("/tumu.html", response_class=HTMLResponse)
async def read_tumu(request: Request):
    return templates.TemplateResponse("tumu.html", {"request": request})

@app.get("/2048.html", response_class=HTMLResponse)
async def read_2048(request: Request):
    return templates.TemplateResponse("2048.html", {"request": request})

@app.get("/status", response_class=HTMLResponse)
async def read_status(request: Request):
    status_results = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for instance in INVIDIOUS_INSTANCES:
            start_time = datetime.now()
            try:
                # 統計APIを叩いて稼働状況を確認
                resp = await client.get(f"{instance.rstrip('/')}/api/v1/stats")
                latency = (datetime.now() - start_time).total_seconds() * 1000
                if resp.status_code == 200:
                    data = resp.json()
                    status_results.append({
                        "instance": instance,
                        "status": "Online",
                        "latency": f"{int(latency)}ms",
                        "version": data.get("software", {}).get("version", "unknown"),
                        "users": data.get("usage", {}).get("users", {}).get("total", 0)
                    })
                else:
                    status_results.append({"instance": instance, "status": f"Error {resp.status_code}", "latency": "-", "version": "-", "users": "-"})
            except Exception:
                status_results.append({"instance": instance, "status": "Offline", "latency": "-", "version": "-", "users": "-"})
    
    return templates.TemplateResponse("status.html", {"request": request, "instances": status_results})

@app.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request):
    """
    購読済みチャンネル一覧ページを表示します。
    実際のデータ処理（LocalStorageからの取得）はフロントエンド（HTML/JS）側で行われます。
    """
    return templates.TemplateResponse("subscriptions.html", {"request": request})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
