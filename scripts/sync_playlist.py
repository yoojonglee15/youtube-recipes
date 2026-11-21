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

def extract_videos_and_tokens(data, vids, tokens):
    for lvm in find_all_key(data, "lockupViewModel"):
        cid = lvm.get("contentId")
        if cid and len(cid) == 11 and cid not in vids:
            vids.append(cid)
    for pvr in find_all_key(data, "playlistVideoRenderer"):
        cid = pvr.get("videoId")
        if cid and len(cid) == 11 and cid not in vids:
            vids.append(cid)
    for civm in find_all_key(data, "continuationItemViewModel"):
        for cc in find_all_key(civm, "continuationCommand"):
            tok = cc.get("token")
            if tok and tok not in tokens:
                tokens.append(tok)
    for cir in find_all_key(data, "continuationItemRenderer"):
        for cc in find_all_key(cir, "continuationCommand"):
            tok = cc.get("token")
            if tok and tok not in tokens:
                tokens.append(tok)

def fetch_playlist_video_ids():
    url = f"https://www.youtube.com/playlist?list={PLAYLIST_ID}&hl=ko"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    api_key_m = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    api_key = api_key_m.group(1) if api_key_m else ""
    client_ver_m = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    client_ver = client_ver_m.group(1) if client_ver_m else "2.20240501.00.00"

    data = extract_json_var(html, "var ytInitialData =") or extract_json_var(html, "ytInitialData =")
    if not data:
        return []

    vids = []
    tokens = []
    extract_videos_and_tokens(data, vids, tokens)

    visited = set()
    while tokens:
        tok = tokens.pop(0)
        if tok in visited:
            continue
        visited.add(tok)
        b_url = f"https://www.youtube.com/youtubei/v1/browse?key={api_key}" if api_key else "https://www.youtube.com/youtubei/v1/browse"
        payload = json.dumps({
            "context": {"client": {"hl": "ko", "gl": "KR", "clientName": "WEB", "clientVersion": client_ver}},
            "continuation": tok
        }).encode("utf-8")
        breq = urllib.request.Request(b_url, data=payload, headers={**headers, "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(breq, timeout=15) as bresp:
                bdata = json.loads(bresp.read().decode("utf-8", errors="replace"))
            extract_videos_and_tokens(bdata, vids, tokens)
        except Exception:
            break

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
        if not cont_tokens:
            for ep in find_all_key(init_data, "engagementPanelSectionListRenderer"):
                if "comment" in str(ep.get("panelIdentifier", "")):
                    for c in find_all_key(ep, "continuationCommand"):
                        if c.get("token"):
                            cont_tokens.append(c["token"])
        for tok in cont_tokens[:2]:
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
                if info["creatorComment"]:
                    break
            except Exception:
                pass
    return info

def guess_category(title, text):
    combined = f"{title} {text}"
    if any(k in combined for k in ["식빵", "베이글", "치아바타", "포카치아", "바게트", "크루아상", "반죽", "발효", "스틱"]):
        return "베이킹"
    if any(k in combined for k in ["케이크", "쿠키", "타르트", "파이", "푸딩", "초콜릿", "디저트"]):
        return "디저트"
    if any(k in combined for k in ["파스타", "스파게티", "리조또", "스테이크", "피자", "샐러드", "라자냐", "부르기뇽", "수프", "스프", "샌드위치", "빠에야"]):
        return "양식"
    if any(k in combined for k in ["카레", "규동", "소바", "판메밀", "우동", "가츠동", "돈테키", "쯔유", "연어장"]):
        return "일식"
    if any(k in combined for k in ["짜장", "짬뽕", "탕수육", "마파두부"]):
        return "중식"
    if any(k in combined for k in ["찌개", "곰탕", "갈비탕", "국", "백숙", "전골"]):
        return "국·찌개"
    if any(k in combined for k in ["무생채", "파김치", "장조림", "초무침", "반찬", "멸치"]):
        return "반찬"
    return "한식"

def parse_korean_recipe_heuristic(info):
    vid = info["videoId"]
    source_text = info["creatorComment"] if len(info["creatorComment"]) > 40 else (info["description"] or info["creatorComment"])
    clean_title = re.sub(r"\[.*?\]|#\S+|❗|\"", "", info["title"]).strip() or info["title"]

    servings = None
    sm = re.search(r"(\d+)\s*인분", source_text)
    if sm:
        servings = int(sm.group(1))

    ingredients = []
    steps = []
    tips = []
    current_group = ""

    unit_pattern = re.compile(r"^([가-힣a-zA-Z0-9\s\(\)·,]+?)\s+(\d+[./~]?\d*\s*(?:g|kg|ml|L|T|t|TS|ts|Tbsp|tsp|큰술|작은술|스푼|숟가락|컵|개|대|마리|모|봉|팩|장|알|톨|줄|줌|꼬집|조각|캔).*|약간|적당량|넉넉히|조금)$")
    step_pattern = re.compile(r"^(?:\d+[\.\)]|[1-9]️⃣|[-•])\s*(.+)$")

    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "http://" in line or "https://" in line or "구독" in line or "비즈니스" in line or "문의" in line or "쿠팡" in line:
            continue
        if line.startswith(("*", "※", "💡", "✔️")) and len(line) > 8 and not unit_pattern.match(line.lstrip("*※💡✔️ ").strip()):
            tips.append(line.lstrip("*※💡✔️ ").strip())
            continue
        if len(line) <= 12 and any(h in line for h in ["재료", "소스", "양념", "드레싱", "고명", "반죽", "필링", "토핑"]):
            current_group = re.sub(r"[\[\]✔️📌■▶✏]", "", line).strip()
            continue
        stm = step_pattern.match(line)
        if stm and len(stm.group(1)) > 8:
            steps.append({"text": stm.group(1).strip(), "ts": None, "timerMin": None, "tip": None})
            continue
        um = unit_pattern.match(line.lstrip("-•· ").strip())
        if um and len(um.group(1).strip()) <= 22:
            ingredients.append({
                "group": current_group,
                "name": um.group(1).strip(),
                "amount": um.group(2).strip()
            })
            continue
        if any(line.endswith(end) for end in ["줍니다.", "줍니다", "해요.", "해요", "세요.", "세요"]) and len(line) > 10:
            steps.append({"text": line, "ts": None, "timerMin": None, "tip": None})

    if not steps:
        lines = [l.strip() for l in source_text.splitlines() if l.strip() and not l.strip().startswith("#") and "http" not in l]
        steps = [{"text": l, "ts": None, "timerMin": None, "tip": None} for l in lines[:10]] or [
            {"text": "유튜브 영상을 참고해 조리해 주세요.", "ts": None, "timerMin": None, "tip": None}
        ]

    now_ms = int(time.time() * 1000)
    return {
        "id": vid,
        "videoId": vid,
        "title": clean_title,
        "category": guess_category(clean_title, source_text),
        "channel": info["channel"],
        "channelUrl": info["channelUrl"],
        "url": f"https://youtu.be/{vid}",
        "thumb": f"thumbs/{vid}.jpg",
        "fav": False,
        "time": "",
        "servings": servings,
        "needsReview": False,
        "reviewNote": "",
        "memo": "",
        "ingredients": ingredients,
        "steps": steps,
        "tips": tips,
        "createdAt": now_ms,
        "updatedAt": now_ms
    }

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

    return parse_korean_recipe_heuristic(info)

def main():
    with open(BACKUP_PATH, "r", encoding="utf-8") as f:
        recipes = json.load(f)

    recipe_map = {r.get("videoId") or r.get("id"): r for r in recipes}
    playlist_ids = fetch_playlist_video_ids()
    if not playlist_ids:
        print("Could not fetch playlist IDs.")
        return

    new_ids = [vid for vid in playlist_ids if vid not in recipe_map]
    if not new_ids:
        print("No new videos found in playlist.")
        return

    print(f"Found {len(new_ids)} new video(s): {new_ids}")
    for vid in new_ids:
        info = fetch_video_detail_and_comments(vid)
        new_recipe = parse_with_gemini_or_heuristic(info)
        recipe_map[vid] = new_recipe
        print(f"Added recipe: {new_recipe['title']} ({vid})")

    # Re-index playlistOrder and createdAt so playlist top is always #1 newest
    ordered_recipes = []
    base_ts = int(time.time() * 1000)
    rank = 0
    for vid in playlist_ids:
        if vid in recipe_map:
            rank += 1
            r = recipe_map.pop(vid)
            r["playlistOrder"] = rank
            r["createdAt"] = base_ts - (rank - 1) * 1000
            ordered_recipes.append(r)

    for r in recipe_map.values():
        rank += 1
        r["playlistOrder"] = rank
        r["createdAt"] = base_ts - (rank - 1) * 1000
        ordered_recipes.append(r)

    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(ordered_recipes, f, ensure_ascii=False, indent=2)

    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    new_json_line = "    const DEFAULT_RECIPES = " + json.dumps(ordered_recipes, ensure_ascii=False) + ";"
    html = re.sub(r"^\s*const DEFAULT_RECIPES = \[.*\];$", new_json_line, html, count=1, flags=re.MULTILINE)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print("Updated recipes-backup.json and index.html successfully.")

if __name__ == "__main__":
    main()
