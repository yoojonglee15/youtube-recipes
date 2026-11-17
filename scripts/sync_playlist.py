import json
import os
import re
import time
import urllib.request

PLAYLIST_ID = "PLcWkGfOMFapVjR5529pJ2-D5ArO5ua83d"
BACKUP_PATH = "recipes-backup.json"
INDEX_PATH = "index.html"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cookie": "CONSENT=YES+cb.20210328-17-p0.en+FX+417",
}

def extract_json_var(html, var_name):
    idx = html.find(var_name)
    if idx == -1:
        return None
    brace_idx = html.find("{", idx)
    if brace_idx == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, brace_idx)
        return obj
    except Exception:
        return None

def find_all_key(obj, key):
    res = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                res.append(v)
            res.extend(find_all_key(v, key))
    elif isinstance(obj, list):
        for x in obj:
            res.extend(find_all_key(x, key))
    return res

def fetch_playlist_video_ids():
    url = f"https://www.youtube.com/playlist?list={PLAYLIST_ID}&hl=ko"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    data = extract_json_var(html, "var ytInitialData =") or extract_json_var(html, "ytInitialData =")
    if not data:
        return []

    vids = []
    for lvm in find_all_key(data, "lockupViewModel"):
        cid = lvm.get("contentId")
        if cid and len(cid) == 11 and cid not in vids:
            vids.append(cid)
    for pvr in find_all_key(data, "playlistVideoRenderer"):
        cid = pvr.get("videoId")
        if cid and len(cid) == 11 and cid not in vids:
            vids.append(cid)
    return vids

def fetch_video_detail_and_comments(vid):
    info = {"videoId": vid, "title": "", "description": "", "channel": "", "channelUrl": "", "creatorComment": ""}
    url = f"https://www.youtube.com/watch?v={vid}&hl=ko"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    api_key_m = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    api_key = api_key_m.group(1) if api_key_m else ""

    player = extract_json_var(html, "var ytInitialPlayerResponse =") or extract_json_var(html, "ytInitialPlayerResponse =")
    if player:
        vd = player.get("videoDetails", {})
        info["title"] = vd.get("title", "")
        info["description"] = vd.get("shortDescription", "")
        info["channel"] = vd.get("author", "")
        cid = vd.get("channelId", "")
        if cid:
            info["channelUrl"] = f"https://www.youtube.com/channel/{cid}"

    init_data = extract_json_var(html, "var ytInitialData =") or extract_json_var(html, "ytInitialData =")
    if init_data:
        cont_tokens = []
        for item_sec in find_all_key(init_data, "itemSectionRenderer"):
            if item_sec.get("sectionIdentifier") == "comment-item-section":
                for c in find_all_key(item_sec, "continuationCommand"):
                    if c.get("token"):
                        cont_tokens.append(c["token"])
        for tok in cont_tokens[:1]:
            n_url = f"https://www.youtube.com/youtubei/v1/next?key={api_key}" if api_key else "https://www.youtube.com/youtubei/v1/next"
            n_payload = json.dumps({
                "context": {"client": {"hl": "ko", "gl": "KR", "clientName": "WEB", "clientVersion": "2.20240501.00.00"}},
                "continuation": tok
            }).encode("utf-8")
            nreq = urllib.request.Request(n_url, data=n_payload, headers={**headers, "Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(nreq, timeout=10) as nresp:
                    ndata = json.loads(nresp.read().decode("utf-8", errors="replace"))
                for cpayload in find_all_key(ndata, "commentEntityPayload"):
                    if cpayload.get("author", {}).get("isCreator"):
                        info["creatorComment"] = cpayload.get("properties", {}).get("content", {}).get("content", "")
                        break
            except Exception:
                pass
    return info

def parse_with_gemini_or_heuristic(info):
    vid = info["videoId"]
    raw_text = f"제목: {info['title']}\n채널: {info['channel']}\n설명:\n{info['description']}\n고정댓글:\n{info['creatorComment']}"
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if api_key:
        try:
            g_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            prompt = (
                "다음 유튜브 요리 영상 정보를 바탕으로 JSON 객체를 생성해줘. "
                "필드: title(깔끔한 요리명), category(한식/양식/일식/중식/베이킹/디저트/반찬/국·찌개/기타 중 택1), "
                "time(예: '약 20분'), servings(숫자 또는 null), "
                "ingredients([{\"group\":\"\",\"name\":\"재료명\",\"amount\":\"분량\"}]), "
                "steps([{\"text\":\"조리단계\",\"ts\":null,\"timerMin\":null,\"tip\":null}]), "
                "tips([\"팁 문자열\"]). JSON만 출력해:\n\n" + raw_text
            )
            payload = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }).encode("utf-8")
            req = urllib.request.Request(g_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=25) as resp:
                gdata = json.loads(resp.read().decode("utf-8"))
            txt = gdata["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(txt)
            now_ms = int(time.time() * 1000)
            return {
                "id": vid,
                "videoId": vid,
                "title": parsed.get("title") or info["title"],
                "category": parsed.get("category") or "한식",
                "channel": info["channel"],
                "channelUrl": info["channelUrl"],
                "url": f"https://youtu.be/{vid}",
                "thumb": f"thumbs/{vid}.jpg",
                "fav": False,
                "time": parsed.get("time") or "",
                "servings": parsed.get("servings"),
                "needsReview": False,
                "reviewNote": "",
                "memo": "",
                "ingredients": parsed.get("ingredients") or [],
                "steps": parsed.get("steps") or [],
                "tips": parsed.get("tips") or [],
                "createdAt": now_ms,
                "updatedAt": now_ms
            }
        except Exception as e:
            print(f"Gemini parse fallback for {vid}: {e}")

    # Heuristic fallback using creator comment or description lines
    source_text = info["creatorComment"] or info["description"]
    lines = [l.strip() for l in source_text.splitlines() if l.strip() and not l.strip().startswith("#")]
    steps = [{"text": l, "ts": None, "timerMin": None, "tip": None} for l in lines[:12]] or [
        {"text": "유튜브 영상을 참고해 조리해 주세요.", "ts": None, "timerMin": None, "tip": None}
    ]
    now_ms = int(time.time() * 1000)
    clean_title = re.sub(r"#\S+", "", info["title"]).strip() or info["title"]
    return {
        "id": vid,
        "videoId": vid,
        "title": clean_title,
        "category": "한식",
        "channel": info["channel"],
        "channelUrl": info["channelUrl"],
        "url": f"https://youtu.be/{vid}",
        "thumb": f"thumbs/{vid}.jpg",
        "fav": False,
        "time": "",
        "servings": None,
        "needsReview": False,
        "reviewNote": "",
        "memo": "",
        "ingredients": [],
        "steps": steps,
        "tips": [],
        "createdAt": now_ms,
        "updatedAt": now_ms
    }

def main():
    with open(BACKUP_PATH, "r", encoding="utf-8") as f:
        recipes = json.load(f)

    existing_ids = {r.get("videoId") or r.get("id") for r in recipes}
    playlist_ids = fetch_playlist_video_ids()
    new_ids = [vid for vid in playlist_ids if vid not in existing_ids]

    if not new_ids:
        print("No new videos found in playlist.")
        return

    print(f"Found {len(new_ids)} new video(s): {new_ids}")
    for vid in new_ids:
        info = fetch_video_detail_and_comments(vid)
        new_recipe = parse_with_gemini_or_heuristic(info)
        recipes.insert(0, new_recipe)
        print(f"Added recipe: {new_recipe['title']} ({vid})")

    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(recipes, f, ensure_ascii=False, indent=2)

    # Also update embedded DEFAULT_RECIPES in index.html
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    new_json_line = "    const DEFAULT_RECIPES = " + json.dumps(recipes, ensure_ascii=False) + ";"
    html = re.sub(r"^\s*const DEFAULT_RECIPES = \[.*\];$", new_json_line, html, count=1, flags=re.MULTILINE)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print("Updated recipes-backup.json and index.html successfully.")

if __name__ == "__main__":
    main()
