#!/usr/bin/env python3
"""Video reference analyzer v2 (chat-first).

Usage:
  analyze.py <x_post_url | local_video_file> [--out DIR] [--whisper-model small|base|tiny|none] [--no-ocr]
             [--media-url MP4_URL] [--post-json saved_x_mcp_result.json] [--no-json]

Breaks a reference video (launch video, ad, short clip) into a brief for Remotion/Codex + ElevenLabs.
The report is printed to STDOUT as chat-ready Markdown. No .md files are written. Images (keyframes,
contact_sheet.png, timeline.png) and an optional report.json go to a temp run dir whose paths are
printed at the end of the report. Progress logs go to STDERR.
"""
import os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find_venv_python():
    for d in (HERE / ".venv", HERE.parent / ".venv"):
        p = d / "bin" / "python"
        if p.exists():
            return d, p
    return None, None


_venv, _vpy = _find_venv_python()
if _venv is not None and Path(sys.prefix).resolve() != _venv.resolve() and not os.environ.get("VRA_NO_REEXEC"):
    os.environ["VRA_NO_REEXEC"] = "1"
    os.execv(str(_vpy), [str(_vpy), str(Path(__file__).resolve())] + sys.argv[1:])

import argparse, json, math, re, shutil, subprocess, tempfile, time, urllib.request

if HERE.name == "scripts":  # installed as a skill: keep model downloads inside the skill folder
    os.environ.setdefault("HF_HOME", str(HERE.parent / ".models"))
if not os.environ.get("SSL_CERT_FILE"):
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass

import numpy as np

ROOT = HERE
WORK = Path(os.environ.get("VRA_WORKDIR") or (Path(tempfile.gettempdir()) / "reference-skill"))
CACHE = WORK / "cache"
os.environ.setdefault("MPLCONFIGDIR", str(WORK / "matplotlib"))
BEAT_TOL = 0.080  # seconds: raw "on beat" tolerance
LOCK_TOL = 0.035  # seconds: tolerance around an offset-corrected grid (≈2 frames at 60 fps)
UA = "Mozilla/5.0 (Macintosh) reference-skill/2.0"
REMOTION_FFPROBE = None

def log(*a):
    print("[vra]", *a, file=sys.stderr, flush=True)


def run(cmd, check=True, capture=True):
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r


def tc(t, fps=None):
    """seconds -> 00:00.000 (and frame-accurate SMPTE if fps)."""
    return f"{max(0.0, float(t)):.3f}s"


def smpte(t, fps):
    f = int(round(t * fps))
    fr = int(round(fps))
    s, ff = divmod(f, fr)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}:{ff:02d}"


def r3(x):
    return None if x is None else round(float(x), 3)



# ----------------------------------------------------------------------------- binaries

_TOOLS = {}


def ffmpeg_bin():
    if "ffmpeg" in _TOOLS:
        return _TOOLS["ffmpeg"]
    p = os.environ.get("VRA_FFMPEG") or shutil.which("ffmpeg")
    if not p:
        try:
            import imageio_ffmpeg
            p = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            p = None
    if not p:
        raise SystemExit("ffmpeg not found. Run scripts/setup.sh (installs imageio-ffmpeg) or set VRA_FFMPEG.")
    _TOOLS["ffmpeg"] = p
    return p


def ffmpeg_dir():
    """A directory containing an executable literally named 'ffmpeg' (for yt-dlp --ffmpeg-location)."""
    p = ffmpeg_bin()
    if Path(p).name == "ffmpeg":
        return str(Path(p).parent)
    d = WORK / "bin"
    d.mkdir(parents=True, exist_ok=True)
    link = d / "ffmpeg"
    if not link.exists() or os.path.realpath(link) != os.path.realpath(p):
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(p)
    return str(d)


def ytdlp_cmd():
    return [sys.executable, "-m", "yt_dlp"]


def ffprobe_json(video):
    if os.environ.get("VRA_NO_FFPROBE"):
        return None
    for p in (os.environ.get("VRA_FFPROBE"), shutil.which("ffprobe"), REMOTION_FFPROBE):
        if not p or not os.path.isfile(p):
            continue
        d = os.path.dirname(p)
        env = dict(os.environ, DYLD_LIBRARY_PATH=d + (":" + os.environ["DYLD_LIBRARY_PATH"] if os.environ.get("DYLD_LIBRARY_PATH") else ""))
        try:
            r = subprocess.run([p, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(Path(video).resolve())],
                               capture_output=True, text=True, env=env, cwd=d, timeout=60)
            if r.returncode == 0:
                return json.loads(r.stdout)
        except Exception:
            continue
    return None


def ffmpeg_probe(video):
    """Fallback metadata from `ffmpeg -i` stderr when no ffprobe is available."""
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(video)], capture_output=True, text=True)
    t = r.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", t)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    br = re.search(r"bitrate:\s*(\d+)\s*kb/s", t)
    vm = re.search(r"Stream #\d+:\d+[^\n]*?: Video: (\w+)[^\n]*?(\d{2,5})x(\d{2,5})[^\n]*", t)
    if not vm:
        raise SystemExit("Could not read the video stream (ffmpeg -i). Is this a video file?")
    vline = vm.group(0)
    fm = re.search(r"([\d.]+) fps", vline) or re.search(r"([\d.]+) tbr", vline)
    rot = re.search(r"rotation of (-?[\d.]+) degrees", t) or re.search(r"rotate\s*:\s*(-?\d+)", t)
    am = re.search(r"Stream #\d+:\d+[^\n]*?: Audio: (\w+)[^\n]*?(\d+) Hz, ([^,\n]+)", t)
    ch = None
    if am:
        lay = am.group(3).strip()
        ch = {"mono": 1, "stereo": 2}.get(lay) or (int(sum(float(x) for x in lay.split("(")[0].split("."))) if re.match(r"^\d+(\.\d+)?", lay) else None)
    streams = [{"codec_type": "video", "codec_name": vm.group(1), "width": int(vm.group(2)), "height": int(vm.group(3)),
                "avg_frame_rate": f"{float(fm.group(1)) if fm else 30.0}/1",
                "side_data_list": [{"rotation": int(float(rot.group(1)))}] if rot else []}]
    if am:
        streams.append({"codec_type": "audio", "codec_name": am.group(1), "sample_rate": am.group(2), "channels": ch})
    return {"streams": streams, "format": {"duration": dur, "bit_rate": int(br.group(1)) * 1000 if br else 0}, "_source": "ffmpeg -i"}

# ----------------------------------------------------------------------------- download

def parse_post_id(url):
    m = re.search(r"/status(?:es)?/(\d+)", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d{8,25}", url.strip()):
        return url.strip()
    raise SystemExit(f"Could not find a post id in {url!r}. Paste an x.com/<user>/status/<id> link.")


def fetch_meta_ytdlp(url):
    r = run(ytdlp_cmd() + ["-J", "--no-warnings", "--no-playlist", url], check=False)
    if r.returncode != 0:
        return None, r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "yt-dlp failed"
    d = json.loads(r.stdout)
    if d.get("_type") == "playlist":
        ents = [e for e in d.get("entries") or [] if e]
        d = ents[0] if ents else d
    return d, None


def fetch_meta_fx(post_id):
    """Public no-auth fallback (fxtwitter API)."""
    try:
        req = urllib.request.Request(f"https://api.fxtwitter.com/status/{post_id}", headers={"User-Agent": UA})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        return d.get("tweet"), None
    except Exception as e:
        return None, f"fxtwitter: {e}"


def download(url, post_id, run_dir, media_url=None, post_json=None):
    run_dir.mkdir(parents=True, exist_ok=True)
    video = run_dir / "video.mp4"
    meta = {"post_id": post_id, "url": url, "download_method": None}
    errors = []

    # Optional post metadata from the X MCP (saved JSON from get_posts_by_id[s])
    if post_json:
        try:
            pj = json.load(open(post_json))
            posts = pj.get("data") if isinstance(pj.get("data"), list) else [pj.get("data") or pj]
            users = {u["id"]: u for u in (pj.get("includes", {}).get("users") or [])}
            post = next((p for p in posts if p.get("id") == post_id), posts[0])
            meta["post_text"] = post.get("text")
            meta["created_at"] = post.get("created_at")
            meta["metrics"] = post.get("public_metrics")
            u = users.get(post.get("author_id"), {})
            meta["author"] = u.get("name")
            meta["author_handle"] = u.get("username")
            if not media_url:
                best = None
                for m in pj.get("includes", {}).get("media") or []:
                    for v in m.get("variants") or []:
                        if v.get("content_type") == "video/mp4" and (best is None or v.get("bit_rate", 0) > best.get("bit_rate", 0)):
                            best = v
                if best:
                    media_url = best["url"]
        except Exception as e:
            errors.append(f"post-json: {e}")

    ymeta = None
    if video.exists() and video.stat().st_size > 0:
        meta["download_method"] = "cached"
    if not media_url:
        ymeta, err = fetch_meta_ytdlp(url)
        if err:
            errors.append(f"yt-dlp meta: {err}")
        if ymeta:
            meta.setdefault("post_text", ymeta.get("description"))
            meta.setdefault("author", ymeta.get("uploader"))
            meta.setdefault("author_handle", ymeta.get("uploader_id"))
            meta.setdefault("created_at", ymeta.get("timestamp") and time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ymeta["timestamp"])))
            meta.setdefault("metrics", {"like_count": ymeta.get("like_count"), "repost_count": ymeta.get("repost_count"),
                                        "reply_count": ymeta.get("comment_count"), "view_count": ymeta.get("view_count")})
            if meta["download_method"] is None:
                r = run(ytdlp_cmd() + ["--no-warnings", "--no-playlist", "--ffmpeg-location", ffmpeg_dir(), "-f",
                         "bv*[height<=1080][vcodec^=avc1]+ba/bv*[height<=1080]+ba/b[height<=1080][protocol^=http]/b[height<=1080]/b",
                         "--merge-output-format", "mp4", "-o", str(video), "--force-overwrites", url], check=False)
                if r.returncode == 0 and video.exists():
                    meta["download_method"] = "yt-dlp"
                else:
                    errors.append("yt-dlp download: " + (r.stderr.strip().splitlines() or ["?"])[-1])

    if meta["download_method"] is None and not media_url:
        fx, err = fetch_meta_fx(post_id)
        if err:
            errors.append(err)
        if fx:
            meta.setdefault("post_text", fx.get("text"))
            a = fx.get("author") or {}
            meta.setdefault("author", a.get("name"))
            meta.setdefault("author_handle", a.get("screen_name"))
            vids = [m for m in ((fx.get("media") or {}).get("videos") or []) if m.get("url")]
            if vids:
                # pick the <=1080p mp4 variant with the highest bitrate when variants are listed
                v = vids[0]
                cands = [f for f in (v.get("formats") or []) if f.get("container") == "mp4" or str(f.get("url", "")).split("?")[0].endswith(".mp4")]
                cands = [f for f in cands if (f.get("height") or 0) <= 1080] or cands
                media_url = (max(cands, key=lambda f: f.get("bitrate") or 0)["url"] if cands else v["url"])
                meta["download_method_note"] = "fxtwitter public API"
            else:
                errors.append("fxtwitter: post has no video")

    if meta["download_method"] is None and media_url:
        req = urllib.request.Request(media_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(video, "wb") as f:
            shutil.copyfileobj(r, f)
        meta["download_method"] = "direct media url" + (f" ({meta.get('download_method_note')})" if meta.get("download_method_note") else "")
        meta["media_url"] = media_url

    if meta["download_method"] is None:
        raise SystemExit("No video could be downloaded for this post. Errors:\n  " + "\n  ".join(errors) +
                         "\nIf the post has a video, look it up with the X MCP (get_posts_by_ids with media.fields=variants) "
                         "and re-run with --post-json <saved.json> or --media-url <mp4 url>.")
    # expanded post text (real URLs instead of t.co) from the public fxtwitter API, best effort
    fx, _ = fetch_meta_fx(post_id)
    if fx and fx.get("text"):
        meta["post_text_expanded"] = fx["text"]
    meta["download_errors"] = errors
    return video, meta

# ----------------------------------------------------------------------------- probe

def probe(video):
    d = ffprobe_json(video)
    src_ = "ffprobe"
    if d is None:
        d = ffmpeg_probe(video)
        src_ = "ffmpeg -i (no ffprobe)"
    vs = next(s for s in d["streams"] if s["codec_type"] == "video")
    aud = [s for s in d["streams"] if s["codec_type"] == "audio"]

    def frac(x):
        try:
            n, dn = x.split("/")
            return float(n) / float(dn) if float(dn) else None
        except Exception:
            return None
    fps = frac(vs.get("avg_frame_rate")) or frac(vs.get("r_frame_rate")) or 30.0
    w, h = int(vs["width"]), int(vs["height"])
    rot = 0
    for sd in vs.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    if abs(rot) in (90, 270):
        w, h = h, w
    g = math.gcd(w, h)
    ar = w / h
    common = {16 / 9: "16:9", 9 / 16: "9:16", 1: "1:1", 4 / 5: "4:5", 4 / 3: "4:3", 3 / 4: "3:4", 21 / 9: "21:9", 2 / 3: "2:3", 3 / 2: "3:2"}
    nearest = min(common, key=lambda k: abs(k - ar))
    dur = float(d["format"].get("duration") or vs.get("duration") or 0)
    return {
        "duration_s": r3(dur), "width": w, "height": h, "fps": round(fps, 3),
        "fps_nominal": int(round(fps)), "aspect_ratio": f"{w // g}:{h // g}",
        "aspect_ratio_nearest": common[nearest], "orientation": "vertical" if h > w else ("square" if h == w else "horizontal"),
        "video_codec": vs.get("codec_name"), "has_audio": bool(aud),
        "audio_codec": aud[0].get("codec_name") if aud else None,
        "audio_channels": aud[0].get("channels") if aud else None,
        "audio_sample_rate": int(aud[0].get("sample_rate")) if aud else None,
        "frame_count_est": int(round(dur * fps)),
        "bitrate_kbps": int(int(d["format"].get("bit_rate", 0) or 0) / 1000), "probe_source": src_,
    }

# ----------------------------------------------------------------------------- video analysis

def detect_cuts(video, fps):
    """Candidate cuts from three PySceneDetect detectors (content, adaptive, perceptual hash).
    Returns {frame: set(detector names)}."""
    from scenedetect import detect, ContentDetector, AdaptiveDetector, HashDetector
    min_len = max(4, int(round(fps * 0.15)))
    cands = {}
    for name, det in (("content", ContentDetector(threshold=27.0, min_scene_len=min_len)),
                      ("adaptive", AdaptiveDetector(min_scene_len=min_len)),
                      ("hash", HashDetector(min_scene_len=min_len))):
        try:
            scenes = detect(str(video), det, show_progress=False)
        except Exception as e:
            log(f"{name} detector failed: {e}")
            continue
        for s in scenes[1:]:
            f = int(s[0].frame_num)
            near = next((k for k in cands if abs(k - f) <= 2), None)
            cands.setdefault(near if near is not None else f, set()).add(name)
    return cands


def validate_cuts(cands, nd, ecr, fps):
    """Keep strong detector hits; keep hash-only hits only if edge-change or local diff ratio confirms a real cut."""
    W = max(4, int(fps * 0.5))
    keep = []
    for f, dets in sorted(cands.items()):
        lo, hi = max(1, f - 2), min(len(nd), f + 3)
        if hi <= lo:
            continue
        i = lo + int(np.argmax(nd[lo:hi]))  # snap to the strongest-change frame
        loc = np.r_[nd[max(1, i - W):max(1, i - 1)], nd[i + 2:i + W]]
        ratio = nd[i] / (np.median(loc) + 1.0) if len(loc) else 99
        strong = ("content" in dets) or ("adaptive" in dets) or len(dets) >= 2
        if strong or ecr[i] >= 0.6 or ratio >= 8:
            keep.append(i)
    keep = sorted(set(keep))
    out = []
    for f in keep:
        if out and f - out[-1] < max(3, int(fps * 0.12)):
            continue
        out.append(f)
    return out


def frame_profile(video, fps):
    """Per-frame: diff vs previous normalized to the active (non-black) area, edge change ratio, mean luma,
    sharpness. Keeps small gray frames for optical flow."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    nd, ecr, luma, sharp, small = [], [], [], [], []
    prev = pe = None
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sw = 240 if n < 9000 else 120
    k = np.ones((3, 3), np.uint8)
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        h, w = fr.shape[:2]
        g = cv2.cvtColor(cv2.resize(fr, (sw, max(2, int(sw * h / w))), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        e = cv2.Canny(g, 60, 150) > 0
        luma.append(float(g.mean()))
        sharp.append(float(cv2.Laplacian(g, cv2.CV_32F).var()))
        if prev is None:
            nd.append(0.0); ecr.append(0.0)
        else:
            dd = np.abs(g.astype(np.int16) - prev.astype(np.int16))
            act = (g > 24) | (prev > 24)
            nd.append(float(dd[act].mean()) if act.mean() > 0.002 else float(dd.mean()))
            if e.sum() > 20 or pe.sum() > 20:
                ed = cv2.dilate(e.astype(np.uint8), k) > 0
                pd_ = cv2.dilate(pe.astype(np.uint8), k) > 0
                ecr.append(float(max((e & ~pd_).sum() / max(1, e.sum()), (pe & ~ed).sum() / max(1, pe.sum()))))
            else:
                ecr.append(0.0)
        small.append(g)
        prev, pe = g, e
    cap.release()
    return np.array(nd), np.array(ecr), np.array(luma), np.array(sharp), small


def black_boundaries(luma, fps, cuts):
    """Fades through black that the content detector missed: luma dips below ~8% with brighter frames on both sides."""
    extra = []
    dark = luma < 18
    i = 0
    n = len(luma)
    while i < n:
        if dark[i]:
            j = i
            while j < n and dark[j]:
                j += 1
            mid = (i + j) // 2
            before = luma[max(0, i - int(fps * 0.5)):i]
            after = luma[j:j + int(fps * 0.5)]
            if i > 0 and j < n and len(before) and len(after) and before.max() > 45 and after.max() > 45:
                if not any(abs(mid - c) < fps * 0.4 for c in cuts):
                    extra.append(j)  # new shot starts when it comes out of black
            i = j
        else:
            i += 1
    return extra


def classify_transition(c, diffs, luma, sharp, small, fps):
    import cv2
    n = len(diffs)
    W = max(6, int(fps * 0.5))
    lo, hi = max(1, c - W), min(n, c + W + 1)
    win = diffs[lo:hi]
    ring = np.concatenate([diffs[max(1, c - 3 * W):max(1, c - W)], diffs[min(n, c + W):min(n, c + 3 * W)]])
    base = float(np.median(ring)) if len(ring) else float(np.median(diffs))
    peak = float(diffs[max(1, c - 1):min(n, c + 2)].max())
    thr = base + 0.35 * max(0.0, peak - base)
    # count consecutive elevated frames around the cut
    k = c
    while k - 1 >= lo and diffs[k - 1] > thr and diffs[k - 1] > 6:
        k -= 1
    e = c
    while e + 1 < hi and diffs[e + 1] > thr and diffs[e + 1] > 6:
        e += 1
    elevated = e - k + 1
    lwin = luma[max(0, c - 6):min(n, c + 7)]
    lb = float(np.median(luma[max(0, c - W):max(1, c - 6)])) if c > 6 else float(luma[0])
    la = float(np.median(luma[min(n - 1, c + 7):min(n, c + W)])) if c + 7 < n else float(luma[-1])
    sb = float(np.median(sharp[max(0, c - 3 * W):max(1, c - W)])) if c > W else float(sharp[0])
    sa = float(np.median(sharp[min(n - 1, c + W):min(n, c + 3 * W)])) if c + W < n else float(sharp[-1])
    smin = float(sharp[max(0, c - 3):min(n, c + 4)].min())
    flow_mag, flow_dir = None, None
    try:
        a, b = small[max(0, c - 1)], small[min(n - 1, c + 1)]
        mags, dxs, dys = [], [], []
        for i in range(max(1, c - 3), min(n, c + 3)):
            f = cv2.calcOpticalFlowFarneback(small[i - 1], small[i], None, 0.5, 3, 15, 3, 5, 1.2, 0)
            dxs.append(float(np.median(f[..., 0]))); dys.append(float(np.median(f[..., 1])))
            mags.append(float(np.median(np.hypot(f[..., 0], f[..., 1]))))
        flow_mag = max(mags) if mags else None
        if dxs:
            dx, dy = float(np.mean(dxs)), float(np.mean(dys))
            flow_dir = "horizontal" if abs(dx) > abs(dy) * 1.5 else ("vertical" if abs(dy) > abs(dx) * 1.5 else "mixed")
    except Exception:
        pass
    info = {"elevated_frames": int(elevated), "elev_start": int(k), "elev_end": int(e), "peak_diff": r3(peak), "baseline_diff": r3(base),
            "luma_before": r3(lb), "luma_after": r3(la), "luma_min": r3(lwin.min()), "luma_max": r3(lwin.max())}
    if lwin.max() > max(lb, la) + 45 and lwin.max() > 190:
        kind = "flash (white/bright frame)"
    elif lwin.min() < 15 and min(lb, la) > 35:
        kind = "fade/dip through black" if elevated >= 3 else "cut via black frame"
    elif elevated <= 2 and peak > max(12.0, 2.5 * base):
        kind = "hard cut"
    elif (flow_mag or 0) > 2.0 and smin < 0.45 * max(1e-6, min(sb, sa)) and elevated >= 2:
        kind = f"whip/motion-blur ({flow_dir or 'mixed'})"
    elif elevated >= 3:
        kind = "dissolve/cross-fade or animated wipe"
    else:
        kind = "hard cut (soft — content motion around it)"
    info["flow_peak"] = r3(flow_mag)
    info["transition_frames"] = int(elevated) if "hard" not in kind and "via black" not in kind else 1
    return kind, info


def dominant_colors(img_bgr, k=4):
    import cv2
    small = cv2.resize(img_bgr, (96, max(2, int(96 * img_bgr.shape[0] / img_bgr.shape[1]))), interpolation=cv2.INTER_AREA)
    Z = small.reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(Z, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=k)
    order = np.argsort(-counts)
    out = []
    for i in order:
        b, g, r = [int(x) for x in centers[i]]
        out.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "share": r3(counts[i] / counts.sum())})
    return out


def ocr_available():
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # noqa
        return True
    except Exception:
        return False


def _font(size):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def ocr_image(img_bgr):
    if not ocr_available():
        return None
    import cv2, pytesseract
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    scale = 1600 / w if w < 1600 else 1.0
    if scale != 1.0:
        g = cv2.resize(g, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    best = []
    best_score = -1
    for variant in (g, 255 - g):
        try:
            d = pytesseract.image_to_data(variant, config="--psm 11", output_type=pytesseract.Output.DICT)
        except Exception:
            continue
        lines = {}
        for i, txt in enumerate(d["text"]):
            txt = (txt or "").strip()
            conf = float(d["conf"][i]) if str(d["conf"][i]) not in ("-1", "") else -1
            if not txt or conf < 70:
                continue
            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            lines.setdefault(key, []).append((d["left"][i], txt, conf, d["top"][i], d["height"][i]))
        res = []
        for key, ws in lines.items():
            ws.sort()
            text = " ".join(t for _, t, _, _, _ in ws)
            if not re.search(r"[A-Za-z0-9]{3,}", text):
                continue
            mc = float(np.mean([c for _, _, c, _, _ in ws]))
            if mc < 75:
                continue
            res.append({"text": text, "conf": round(mc, 1), "y": round(ws[0][3] / g.shape[0], 3),
                        "x": round(ws[0][0] / g.shape[1], 3), "rel_height": round(float(np.median([hh for *_, hh in ws])) / g.shape[0], 3)})
        score = sum(len(r["text"]) * r["conf"] for r in res)
        if score > best_score:
            best, best_score = res, score
    best.sort(key=lambda r: (r["y"], r["x"]))
    return best


def text_motion_guess(samples):
    """samples: list of (pos_fraction, ocr_lines)."""
    counts = [sum(len(l["text"]) for l in s) for _, s in samples]
    if not any(counts):
        return "no readable text"
    if counts[0] == 0 and counts[-1] > 0:
        return "text enters mid-shot (animate in)"
    if counts[-1] == 0 and counts[0] > 0:
        return "text exits before shot ends (animate out)"
    if all(counts[i] <= counts[i + 1] for i in range(len(counts) - 1)) and counts[-1] >= counts[0] + 6:
        return "text builds up over the shot (typewriter / word-by-word reveal)"
    t0 = " ".join(l["text"] for l in samples[0][1]); t1 = " ".join(l["text"] for l in samples[-1][1])
    if t0 and t1 and (t0 == t1 or _similar(t0, t1) > 0.8):
        return "text held steady through the shot"
    return "text changes within the shot (swap / animated)"


def _similar(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def shots_pass(video, shots, kf_dir, fps, do_ocr=True):
    """Second sequential pass: save keyframe per shot, OCR at 3 points, colors."""
    import cv2
    want = {}
    for s in shots:
        a, b = s["start_frame"], s["end_frame"]  # end exclusive
        L = max(1, b - a)
        mid = a + L // 2
        s["keyframe_frame"] = mid
        want.setdefault(mid, []).append((s["index"], "key"))
        if do_ocr:
            for p in (0.15, 0.5, 0.9):
                fi = min(b - 1, a + int(L * p))
                want.setdefault(fi, []).append((s["index"], p))
    byidx = {s["index"]: s for s in shots}
    for s in shots:
        s["_ocr"] = {}
    cap = cv2.VideoCapture(str(video))
    i = 0
    last = max(want) if want else -1
    while i <= last:
        ok, fr = cap.read()
        if not ok:
            break
        if i in want:
            for idx, what in want[i]:
                s = byidx[idx]
                if what == "key":
                    h, w = fr.shape[:2]
                    img = fr if w <= 1280 else cv2.resize(fr, (1280, int(1280 * h / w)), interpolation=cv2.INTER_AREA)
                    p = kf_dir / f"shot_{idx:03d}.png"
                    cv2.imwrite(str(p), img)
                    s["keyframe"] = str(p)
                    s["dominant_colors"] = dominant_colors(fr)
                else:
                    s["_ocr"][what] = ocr_image(fr) or []
        i += 1
    cap.release()
    for s in shots:
        o = s.pop("_ocr")
        if do_ocr and o:
            samples = [(p, o.get(p, [])) for p in (0.15, 0.5, 0.9)]
            # union of distinct lines, in order of appearance
            seen, lines = set(), []
            for _, ls in samples:
                for l in ls:
                    k = l["text"].lower()
                    if not any(_similar(k, x) > 0.85 for x in seen):
                        seen.add(k); lines.append(l)
            s["ocr_text"] = [l["text"] for l in lines]
            s["ocr_detail"] = lines
            s["text_motion_guess"] = text_motion_guess(samples)
        else:
            s["ocr_text"], s["ocr_detail"], s["text_motion_guess"] = [], [], "OCR unavailable"


def contact_sheet(shots, out, fps):
    from PIL import Image, ImageDraw, ImageFont
    imgs = [(s, Image.open(s["keyframe"])) for s in shots if s.get("keyframe")]
    if not imgs:
        return None
    cols = 4 if len(imgs) > 6 else min(3, len(imgs))
    tw = 400
    ar = imgs[0][1].height / imgs[0][1].width
    th = int(tw * ar)
    lab = 46
    rows = math.ceil(len(imgs) / cols)
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 8, rows * (th + lab) + (rows + 1) * 8), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    try:
        font, fsm = _font(15), _font(12)
    except Exception:
        font = fsm = ImageFont.load_default()
    for k, (s, im) in enumerate(imgs):
        r, c = divmod(k, cols)
        x, y = 8 + c * (tw + 8), 8 + r * (th + lab + 8)
        sheet.paste(im.resize((tw, th)), (x, y))
        d.text((x + 4, y + th + 4), f"#{s['index']}  {tc(s['start_s'])}–{tc(s['end_s'])}  ({s['duration_s']:.2f}s)", fill=(235, 235, 235), font=font)
        d.text((x + 4, y + th + 24), (s.get("transition_in") or "")[:52], fill=(150, 200, 255), font=fsm)
    sheet.save(out)
    return str(out)


# ----------------------------------------------------------------------------- audio

KS_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def analyze_audio(video, run_dir, cut_times, duration, whisper_model):
    import librosa
    out = {"present": False}
    wav = run_dir / "audio.wav"
    r = run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(video), "-vn", "-acodec", "pcm_s16le", "-ar", "48000", str(wav)], check=False)
    if r.returncode != 0 or not wav.exists():
        out["error"] = "no audio track"
        return out
    out["present"] = True
    out["audio_file"] = str(wav)
    y48, sr48 = librosa.load(str(wav), sr=48000, mono=False)
    y_st = y48 if y48.ndim == 2 else y48[None, :]
    sr = 22050
    y = librosa.resample(np.mean(y_st, axis=0), orig_sr=sr48, target_sr=sr)
    if np.max(np.abs(y)) < 1e-4:
        out["note"] = "audio track is digitally silent"
        out["silent_track"] = True
        return out
    hop = 512
    # loudness
    try:
        import pyloudnorm as pyln
        meter = pyln.Meter(sr48)
        data = y_st.T
        out["integrated_lufs"] = r3(meter.integrated_loudness(data)) if len(data) > sr48 * 0.5 else None
        st = []
        for t0 in np.arange(0, max(0.001, duration - 2.9), 0.5):
            seg = data[int(t0 * sr48):int((t0 + 3) * sr48)]
            if len(seg) >= sr48 * 0.5:
                v = meter.integrated_loudness(seg) if len(seg) >= int(0.4 * sr48) else -70
                st.append({"t": r3(t0 + 1.5), "lufs_st": r3(max(-70, v)) if np.isfinite(v) else -70})
        out["short_term_lufs"] = st
        out["peak_dbfs"] = r3(20 * np.log10(np.max(np.abs(data)) + 1e-9))
    except Exception as e:
        out["loudness_error"] = str(e)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    rms_db = 20 * np.log10(rms + 1e-9)
    rt = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    step = max(1, int(round(0.1 * sr / hop)))
    out["rms_curve_dbfs"] = [{"t": r3(rt[i]), "db": r3(rms_db[i])} for i in range(0, len(rms), step)]
    # silence (absolute) and near-silence relative to the mix
    sil = []
    mask = rms_db < -50
    i = 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            if rt[min(j, len(rt) - 1)] - rt[i] >= 0.25:
                sil.append({"start_s": r3(rt[i]), "end_s": r3(rt[min(j, len(rt) - 1)]), "duration_s": r3(rt[min(j, len(rt) - 1)] - rt[i])})
            i = j
        else:
            i += 1
    out["silent_sections"] = sil
    # tempo & beats
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=hop, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    beats = np.asarray(beats, dtype=float)
    ibi = np.diff(beats) if len(beats) > 2 else np.array([])
    # extend the beat grid to the whole clip (the tracker often drops beats in intros/outros)
    beats_tracked = beats.copy()
    if len(ibi) >= 3:
        p = float(np.median(ibi))
        pre = np.arange(beats[0] - p, -1e-6, -p)[::-1]
        post = np.arange(beats[-1] + p, duration + 1e-6, p)
        beats = np.r_[pre, beats, post]
        out["beats_extrapolated_count"] = int(len(pre) + len(post))
    # beat clarity: autocorrelation of onset envelope at the beat period vs. overall
    clarity = None
    if tempo > 0 and len(oenv) > 50:
        lag = int(round(60.0 / tempo * sr / hop))
        oe = (oenv - oenv.mean()) / (oenv.std() + 1e-9)
        ac = np.correlate(oe, oe, mode="full")[len(oe) - 1:] / len(oe)
        if lag < len(ac):
            clarity = float(max(ac[max(1, lag - 1):lag + 2]))
    out["tempo_bpm"] = r3(tempo)
    out["tempo_alternatives_bpm"] = [r3(tempo / 2), r3(tempo * 2)]
    out["beat_count"] = int(len(beats))
    out["beat_count_tracked"] = int(len(beats_tracked))
    out["beats_tracked_s"] = [r3(b) for b in beats_tracked]
    out["beat_period_s"] = r3(float(np.median(ibi))) if len(ibi) else None
    out["beats_s"] = [r3(b) for b in beats]
    out["beat_regularity_cv"] = r3(ibi.std() / ibi.mean()) if len(ibi) else None
    out["beat_clarity"] = r3(clarity)
    out["beat_reliable"] = bool(len(beats) >= 6 and (clarity or 0) > 0.15 and (out["beat_regularity_cv"] or 1) < 0.2)
    # pulse-timing check: where is the sharpest attack (hi-res onset envelope, 5.8ms hop) around each tracked pulse?
    try:
        oe_h = librosa.onset.onset_strength(y=y, sr=sr, hop_length=128, n_fft=512)
        th = librosa.frames_to_time(np.arange(len(oe_h)), sr=sr, hop_length=128)
        offs = []
        for bt in beats_tracked:
            w = (th > bt - 0.06) & (th < bt + 0.06)
            if w.sum() > 3:
                seg = oe_h[w]
                if seg.max() > 2 * (np.median(oe_h) + 1e-9):
                    offs.append(th[w][int(np.argmax(seg))] - bt)
        if len(offs) >= 4:
            out["pulse_attack_offset_ms"] = r3(1000 * float(np.median(offs)))
            out["pulse_attack_checked"] = len(offs)
    except Exception as e:
        out["pulse_attack_error"] = str(e)
    # onsets
    on_f = librosa.onset.onset_detect(onset_envelope=oenv, sr=sr, hop_length=hop, backtrack=False)
    on_t = librosa.frames_to_time(on_f, sr=sr, hop_length=hop)
    med = float(np.median(oenv) + 1e-9)
    out["onsets"] = [{"t": r3(t), "strength": r3(oenv[f] / med)} for t, f in zip(on_t, on_f)]
    # cut/beat alignment
    cuts = np.array([c for c in cut_times if c > 0.05])
    if len(beats) and len(cuts):
        dist = np.array([np.min(np.abs(beats - c)) for c in cuts])
        on = dist <= BEAT_TOL
        bps = len(beats) / max(1e-6, beats[-1] - beats[0]) if len(beats) > 1 else 0
        chance = min(1.0, 2 * BEAT_TOL * bps)
        out["cut_beat_alignment"] = {
            "tolerance_s": BEAT_TOL, "cuts_considered": int(len(cuts)), "cuts_on_beat": int(on.sum()),
            "pct_on_beat": r3(100 * on.mean()), "chance_pct": r3(100 * chance),
            "median_offset_ms": r3(1000 * np.median(dist)),
            "per_cut": [{"cut_s": r3(c), "nearest_beat_s": r3(beats[np.argmin(np.abs(beats - c))]), "offset_ms": r3(1000 * (c - beats[np.argmin(np.abs(beats - c))])), "on_beat": bool(o)} for c, o in zip(cuts, on)],
        }
        if len(beats) > 2 and len(cuts) >= 4:
            p = float(np.median(np.diff(beats)))
            ph = np.array([((c - beats[0]) / p) % 1.0 for c in cuts])
            z = np.exp(2j * np.pi * ph).mean()
            R = float(abs(z)); mph = float((np.angle(z) / (2 * np.pi)) % 1.0)
            out["cut_beat_alignment"]["phase_concentration_R"] = r3(R)
            out["cut_beat_alignment"]["mean_phase_of_beat"] = r3(mph)
            out["cut_beat_alignment"]["mean_offset_after_beat_ms"] = r3(1000 * mph * p)
        if len(beats) > 2:
            half = np.sort(np.r_[beats, (beats[:-1] + beats[1:]) / 2])
            hd = np.array([np.min(np.abs(half - c)) for c in cuts])
            out["cut_beat_alignment"]["pct_on_half_beat_grid"] = r3(100 * (hd <= BEAT_TOL).mean())
            out["cut_beat_alignment"]["half_grid_chance_pct"] = r3(100 * min(1.0, 4 * BEAT_TOL * bps))
        if len(on_t):
            od = np.array([np.min(np.abs(on_t - c)) for c in cuts])
            out["cut_onset_alignment_pct"] = r3(100 * (od <= BEAT_TOL).mean())
    # spectral character
    cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    out["spectral_centroid_hz_median"] = r3(np.median(cent))
    try:
        Hh, Pp = librosa.effects.hpss(y)
        out["percussive_energy_ratio"] = r3(np.sum(Pp ** 2) / (np.sum(Hh ** 2) + np.sum(Pp ** 2) + 1e-12))
    except Exception:
        Pp = y
    try:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop).mean(axis=1)
        best = (-9, None)
        for k in range(12):
            for prof, mode in ((KS_MAJ, "major"), (KS_MIN, "minor")):
                c = np.corrcoef(np.roll(prof, k), chroma)[0, 1]
                if c > best[0]:
                    best = (c, f"{NOTES[k]} {mode}")
        out["key_estimate"] = best[1]
        out["key_confidence"] = r3(best[0])
    except Exception:
        pass
    # energy sections from short-term loudness (fallback RMS)
    out["sections"] = energy_sections(out.get("short_term_lufs"), rms_db, rt, duration)
    # speech
    out["speech"] = transcribe(wav, whisper_model)
    # SFX candidates
    out["sfx_candidates"] = find_sfx(y, sr, hop, oenv, on_f, on_t, beats, out["beat_reliable"], cuts, out["speech"], rms_db, rt)
    return out


def energy_sections(st, rms_db, rt, duration):
    if st and len(st) >= 4:
        t = np.array([p["t"] for p in st]); v = np.array([p["lufs_st"] for p in st])
    else:
        step = max(1, int(len(rt) / max(1, duration * 2)))
        t, v = rt[::step], rms_db[::step]
    if len(v) < 3:
        return []
    v = np.convolve(v, np.ones(3) / 3, mode="same")
    lv = []
    for x in v:
        md = float(np.median(v))
        lv.append("quiet" if x < md - 8 else ("lower" if x < md - 3 else ("peak" if x > md + 2.5 else "steady")))
    secs = []
    s0 = 0
    for i in range(1, len(lv) + 1):
        if i == len(lv) or lv[i] != lv[s0]:
            secs.append({"start_s": r3(0 if s0 == 0 else (t[s0 - 1] + t[s0]) / 2), "end_s": r3(duration if i == len(lv) else (t[i - 1] + t[i]) / 2), "energy": lv[s0], "mean_level": r3(np.mean(v[s0:i]))})
            s0 = i
    # merge sections shorter than 1.5s into neighbors
    merged = []
    for s in secs:
        if merged and (s["end_s"] - s["start_s"] < 1.5):
            merged[-1]["end_s"] = s["end_s"]
        else:
            merged.append(s)
    for k, s in enumerate(merged):
        nm = "intro" if k == 0 else ("outro" if k == len(merged) - 1 else "body")
        s["role_guess"] = nm
    return merged


def transcribe(wav, model_name):
    if model_name in ("none", "off"):
        return {"present": False, "note": "skipped"}
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        return {"present": False, "error": f"faster-whisper unavailable: {e}"}
    try:
        import librosa
        audio16, _ = librosa.load(str(wav), sr=16000, mono=True)
        m = WhisperModel(model_name, device="cpu", compute_type="int8")
        segs, info = m.transcribe(audio16.astype(np.float32), vad_filter=True, word_timestamps=True, beam_size=5)
        res = []
        for s in segs:
            if s.no_speech_prob > 0.6 or s.avg_logprob < -1.0 or not s.text.strip():
                continue
            res.append({"start_s": r3(s.start), "end_s": r3(s.end), "text": s.text.strip(),
                        "avg_logprob": r3(s.avg_logprob), "no_speech_prob": r3(s.no_speech_prob),
                        "words": [{"w": w.word.strip(), "s": r3(w.start), "e": r3(w.end)} for w in (s.words or [])]})
        words = sum(len(r["text"].split()) for r in res)
        return {"present": words >= 3, "language": info.language, "language_prob": r3(info.language_probability),
                "model": model_name, "segments": res, "word_count": words,
                "speech_seconds": r3(sum(r["end_s"] - r["start_s"] for r in res))}
    except Exception as e:
        return {"present": False, "error": str(e)}


def sfx_type(y, sr, t, rms_db, rt, near_cut, preceding_rise):
    import librosa
    a = int(t * sr); seg = y[a:a + int(0.25 * sr)]
    if len(seg) < 256:
        return "transient", "short percussive hit", 0.3
    cent = float(np.median(librosa.feature.spectral_centroid(y=seg, sr=sr, n_fft=512, hop_length=128)))
    flat = float(np.median(librosa.feature.spectral_flatness(y=seg, n_fft=512, hop_length=128)))
    i0 = int(np.searchsorted(rt, t)); i0 = min(i0, len(rms_db) - 1)
    pk = rms_db[i0:i0 + 4].max() if i0 < len(rms_db) else -90
    j = i0
    while j < len(rms_db) - 1 and rms_db[j] > pk - 12 and rt[j] - t < 2.0:
        j += 1
    decay = float(rt[j] - t) if j < len(rt) else 0.3
    if preceding_rise:
        return "whoosh/riser into cut", f"airy whoosh swelling into a hard stop, {max(0.4, preceding_rise):.1f}s", max(0.5, preceding_rise + 0.2)
    if cent > 3500 and decay < 0.15:
        return "click/tick/UI pop", "crisp short UI click, clean, no reverb", 0.2
    if cent < 1200 and decay > 0.25:
        return "low impact/boom", "deep cinematic sub boom impact with short tail", min(2.0, decay + 0.3)
    if flat > 0.25 and decay > 0.15:
        return "swish/noise burst", "fast airy swish, short", min(1.0, decay + 0.1)
    if near_cut:
        return "hit on cut", "punchy transition hit, tight, modern", min(1.0, decay + 0.2)
    return "pop/hit", "soft punchy pop", min(0.8, decay + 0.15)


def find_sfx(y, sr, hop, oenv, on_f, on_t, beats, beat_reliable, cuts, speech, rms_db, rt):
    import librosa
    if not len(on_t):
        return []
    med = float(np.median(oenv) + 1e-9)
    strengths = oenv[on_f] / med
    thr = max(3.0, float(np.percentile(strengths, 75)))
    speech_iv = [(s["start_s"], s["end_s"]) for s in (speech or {}).get("segments", [])]
    hb_rms = None
    try:
        S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        hb = S[freqs > 2500].sum(axis=0)
        hb_rms = 20 * np.log10(hb + 1e-9)
        hb_t = librosa.frames_to_time(np.arange(len(hb)), sr=sr, hop_length=hop)
    except Exception:
        pass
    cands = []
    for t, s in zip(on_t, strengths):
        nb = float(np.min(np.abs(beats - t))) if len(beats) else 9
        nc = float(np.min(np.abs(cuts - t))) if len(cuts) else 9
        on_beat = nb <= 0.07 and beat_reliable
        at_cut = nc <= 0.12
        in_speech = any(a - 0.05 <= t <= b + 0.05 for a, b in speech_iv)
        local = oenv[max(0, int(t * sr / hop) - 40):int(t * sr / hop) + 40]
        contrast = float(oenv[int(t * sr / hop)] / (np.median(local) + 1e-9)) if len(local) else s
        if s < thr or contrast < 2.5:
            continue
        if in_speech and not at_cut and s < thr * 2:
            continue
        if on_beat and not at_cut:
            continue  # part of the music groove
        rise = None
        if at_cut and hb_rms is not None:
            i1 = int(np.searchsorted(hb_t, t)); i0 = int(np.searchsorted(hb_t, t - 0.8))
            if i1 - i0 > 5:
                seg = hb_rms[i0:i1]
                if seg[-3:].mean() - seg[:3].mean() > 8 and np.argmax(seg) > len(seg) * 0.6:
                    rise = float(t - hb_t[i0 + int(np.argmin(seg[:len(seg) // 2 + 1]))])
        kind, prompt, dur = sfx_type(y, sr, t, rms_db, rt, at_cut, rise)
        cands.append({"t": r3(t), "strength": r3(s), "local_contrast": r3(contrast), "on_beat": bool(nb <= 0.07),
                      "at_cut": at_cut, "nearest_cut_offset_ms": r3(1000 * nc) if len(cuts) else None,
                      "during_speech": in_speech, "type_guess": kind, "prompt": prompt, "duration_s": r3(dur)})
    # typing clusters: >=4 high-freq transients within 1.5s with irregular spacing
    clicks = [c for c in cands if c["type_guess"].startswith("click")]
    typing = []
    i = 0
    while i < len(clicks):
        j = i
        while j + 1 < len(clicks) and clicks[j + 1]["t"] - clicks[i]["t"] <= 1.5:
            j += 1
        if j - i + 1 >= 4:
            iois = np.diff([c["t"] for c in clicks[i:j + 1]])
            if np.std(iois) / (np.mean(iois) + 1e-9) > 0.25:
                for c in clicks[i:j + 1]:
                    c["type_guess"] = "typing/keyboard cluster"
                    c["prompt"] = "mechanical keyboard typing, close mic, light clacks"
                typing.append((clicks[i]["t"], clicks[j]["t"]))
            i = j + 1
        else:
            i += 1
    cands.sort(key=lambda c: -c["strength"])
    top = sorted(cands[:30], key=lambda c: c["t"])
    return top



# ----------------------------------------------------------------------------- beat phase / sections / motivation

def _wrap(x):
    """wrap phase (in grid periods) to [-0.5, 0.5)."""
    return (np.asarray(x) + 0.5) % 1.0 - 0.5


def beat_phases(cut_times, beats):
    """Phase of each cut inside its beat interval, by local interpolation on the (extended) pulse grid.
    0 = on a pulse, 0.5 = halfway between pulses. Returns phases in [0,1) and local periods (s)."""
    b = np.asarray(beats, float)
    ph, per = [], []
    for c in cut_times:
        k = int(np.searchsorted(b, c, side="right")) - 1
        k = min(max(k, 0), len(b) - 2)
        p = b[k + 1] - b[k]
        ph.append(((c - b[k]) / p) % 1.0)
        per.append(p)
    return np.array(ph), np.array(per)


def grid_lock(ph_beat, period, rng=None, sims=2000):
    """Best constant-offset lock of a set of cut phases to the beat grid and the half-beat grid.
    For each grid, scan offsets δ, count cuts within ±LOCK_TOL of grid+δ, and get a Monte-Carlo p-value
    (same scan on uniformly random phases, so the offset search is accounted for)."""
    rng = rng or np.random.default_rng(7)
    n = len(ph_beat)
    res = {}
    for name, mult in (("beat", 1), ("half-beat", 2)):
        gp = period / mult
        ph = (ph_beat * mult) % 1.0
        tol = LOCK_TOL / gp
        deltas = np.arange(-0.5, 0.5, 0.0025)
        if n == 0:
            continue
        d = np.abs(_wrap(ph[None, :] - deltas[:, None]))  # (D, n)
        counts = (d <= tol).sum(axis=1)
        best = int(counts.max())
        cand = np.where(counts == best)[0]
        di = cand[np.argmin(np.abs(deltas[cand]))]
        locked = d[di] <= tol
        # refine δ as the circular mean of the locked cuts
        z = np.exp(2j * np.pi * ph[locked]).mean() if locked.any() else 0
        delta = float(_wrap(np.angle(z) / (2 * np.pi))) if locked.any() else float(deltas[di])
        resid = _wrap(ph - delta) * gp
        locked = np.abs(resid) <= LOCK_TOL
        # Monte Carlo
        R = rng.random((sims, n))
        dd = np.abs(_wrap(R[:, None, :] - deltas[None, ::4, None]))  # coarser δ grid for speed
        mc = (dd <= tol).sum(axis=2).max(axis=1)
        p = float(((mc >= locked.sum()).sum() + 1) / (sims + 1))
        raw = np.abs(_wrap(ph)) * gp
        res[name] = {"grid_period_s": r3(gp), "offset_ms": r3(1000 * delta * gp), "locked": int(locked.sum()), "n": n,
                     "pct_locked_offset_corrected": r3(100 * locked.mean()),
                     "pct_raw_80ms": r3(100 * (raw <= BEAT_TOL).mean()), "pct_raw_lock_tol": r3(100 * (raw <= LOCK_TOL).mean()),
                     "chance_pct_lock_tol": r3(100 * min(1.0, 2 * LOCK_TOL / gp)),
                     "mc_p": r3(p), "resid_ms_locked_sd": r3(1000 * np.std(resid[locked])) if locked.sum() > 1 else None,
                     "locked_mask": [bool(x) for x in locked], "resid_ms": [r3(1000 * x) for x in resid]}
    # verdict: prefer the beat grid when it explains as many cuts
    best = None
    for name in ("beat", "half-beat"):
        g = res.get(name)
        if not g:
            continue
        ok = g["locked"] >= 3 and g["pct_locked_offset_corrected"] >= 50 and g["mc_p"] < 0.05
        g["is_locked"] = bool(ok)
        if ok and (best is None or g["locked"] > res[best]["locked"]):
            best = name
    return {"grids": res, "lock": best}


def pacing_sections(cut_times, duration, min_cuts=4):
    """Split the cut list into ≤3 contiguous pacing sections (log shot-length change points), plus an end hold."""
    ct = list(cut_times)
    n = len(ct)
    if n == 0:
        return [{"name": "single shot", "start_s": 0.0, "end_s": r3(duration), "cut_idx": [], "median_shot_s": r3(duration), "mean_shot_s": r3(duration)}]
    iv = np.diff([0.0] + ct)
    x = np.log(np.maximum(iv, 1 / 60))

    def sse(a, b):
        s = x[a:b]
        return float(((s - s.mean()) ** 2).sum()) if b > a else 0.0
    segs = [(0, n)]
    total = sse(0, n)
    if n >= 2 * min_cuts:
        best2 = min(((sse(0, i) + sse(i, n), i) for i in range(min_cuts, n - min_cuts + 1)), default=None)
        if best2 and best2[0] < 0.75 * total:
            i = best2[1]
            segs = [(0, i), (i, n)]
            if n >= 3 * min_cuts:
                best3 = min(((sse(0, a) + sse(a, b) + sse(b, n), a, b) for a in range(min_cuts, n - 2 * min_cuts + 1)
                             for b in range(a + min_cuts, n - min_cuts + 1)), default=None)
                if best3 and best3[0] < 0.75 * best2[0]:
                    segs = [(0, best3[1]), (best3[1], best3[2]), (best3[2], n)]
    # adjacent sections must actually differ in pace (median shot length ratio ≥ 1.6)
    merged = []
    for a, b in segs:
        if merged:
            pa, pb = merged[-1]
            r = np.median(iv[pa:pb]) / np.median(iv[a:b])
            if 1 / 1.6 < r < 1.6:
                merged[-1] = (pa, b)
                continue
        merged.append((a, b))
    out = []
    meds = [float(np.median(iv[a:b])) for a, b in merged]
    for k, (a, b) in enumerate(merged):
        med = meds[k]
        fast = med < 0.7 or med < 0.6 * max(meds)
        out.append({"name": "montage" if fast else "body", "start_s": r3(0.0 if a == 0 else ct[a - 1]), "end_s": r3(ct[b - 1]),
                    "cut_idx": list(range(a, b)), "median_shot_s": r3(med), "mean_shot_s": r3(float(np.mean(iv[a:b])))})
    tail = duration - ct[-1]
    if tail > 2.0 * meds[-1] and tail > 1.0:
        out.append({"name": "end hold", "start_s": r3(ct[-1]), "end_s": r3(duration), "cut_idx": [], "median_shot_s": r3(tail), "mean_shot_s": r3(tail)})
    else:
        out[-1]["end_s"] = r3(duration)
    # unique names
    seen = {}
    for s in out:
        seen[s["name"]] = seen.get(s["name"], 0) + 1
        if seen[s["name"]] > 1:
            s["name"] = f"{s['name']} {seen[s['name']]}"
    return out


def locked_runs(cut_times, ph_beat, period, min_len=4):
    """Longest runs of consecutive cuts that sit within ±LOCK_TOL of one constant offset on the beat or half-beat grid."""
    runs = []
    n = len(cut_times)
    for name, mult in (("beat", 1), ("half-beat", 2)):
        gp = period / mult
        ph = (np.asarray(ph_beat) * mult) % 1.0
        tol = LOCK_TOL / gp
        i = 0
        while i < n:
            best_j = i
            for j in range(i + 1, n):
                seg = ph[i:j + 1]
                # feasible if some δ puts all within tol: check against the circular mean
                z = np.exp(2j * np.pi * seg).mean()
                d = _wrap(np.angle(z) / (2 * np.pi))
                if np.all(np.abs(_wrap(seg - d)) <= tol):
                    best_j = j
                else:
                    break
            if best_j - i + 1 >= min_len:
                seg = ph[i:best_j + 1]
                z = np.exp(2j * np.pi * seg).mean()
                d = float(_wrap(np.angle(z) / (2 * np.pi)))
                res = _wrap(seg - d) * gp
                runs.append({"grid": name, "first_cut": i, "last_cut": best_j, "start_s": r3(cut_times[i]), "end_s": r3(cut_times[best_j]),
                             "cuts": best_j - i + 1, "offset_ms": r3(1000 * d * gp), "resid_sd_ms": r3(1000 * np.std(res))})
                i = best_j + 1
            else:
                i += 1
    # drop half-beat runs fully explained by a beat run of the same span
    runs.sort(key=lambda r: (-r["cuts"], r["grid"] != "beat"))
    keep = []
    for r in runs:
        if any(k["first_cut"] <= r["first_cut"] and r["last_cut"] <= k["last_cut"] for k in keep):
            continue
        keep.append(r)
    return sorted(keep, key=lambda r: r["start_s"])


def run_significance(n_cuts, run_len, period_grid, sims=2000, rng=None):
    """Monte-Carlo p that ≥run_len consecutive cuts out of n_cuts land within ±LOCK_TOL of one common offset
    on a grid with this period, if cut phases were random."""
    rng = rng or np.random.default_rng(11)
    tol = LOCK_TOL / period_grid
    hits = 0
    for _ in range(sims):
        ph = rng.random(n_cuts)
        best, i = 1, 0
        while i < n_cuts:
            j = i
            while j + 1 < n_cuts:
                seg = ph[i:j + 2]
                d = _wrap(np.angle(np.exp(2j * np.pi * seg).mean()) / (2 * np.pi))
                if np.all(np.abs(_wrap(seg - d)) <= tol):
                    j += 1
                else:
                    break
            best = max(best, j - i + 1)
            i = j + 1 if j > i else i + 1
        hits += best >= run_len
    return (hits + 1) / (sims + 1)


def cut_motivation(cut_frames, shots, diffs, fps):
    """Per cut: motion energy (mean abs frame difference over the active area) in the ~6 frames before the cut vs the
    outgoing shot's own motion, when that motion last happened (settle time), and motion in the ~6 frames after."""
    n = len(diffs)
    W = 6
    ACT = 1.0      # a frame with diff > ACT is "moving"
    MOVE = 2.0     # a shot whose top motion is below this is a static hold
    by_start = {s["start_frame"]: s for s in shots}
    out = []
    prev_start, prev_elev_end = 0, 0
    for c in cut_frames:
        sh_in = by_start.get(c, {})
        det = sh_in.get("transition_detail") or {}
        soft = "hard" not in (sh_in.get("transition_in") or "hard")
        k0 = det.get("elev_start", c) if soft else c
        k1 = det.get("elev_end", c) if soft else c
        a = max(prev_start + 1, prev_elev_end + 1)
        b = max(a + 1, k0)
        seg = diffs[a:b]
        L = len(seg)
        pre = seg[-W:]
        post = diffs[min(n - 1, k1 + 1):min(n, k1 + 1 + W)]
        m_pre = float(np.mean(pre)) if len(pre) else 0.0
        m_post = float(np.mean(post)) if len(post) else 0.0
        top = float(np.mean(np.sort(seg)[-min(W, L):])) if L else 0.0
        act_idx = np.where(seg > ACT)[0]
        act_med = float(np.median(seg[act_idx])) if len(act_idx) else 0.0
        settle_f = int(L - 1 - act_idx[-1]) if len(act_idx) else None   # still frames between last motion and the cut
        last3 = float(np.mean(pre[-3:])) if len(pre) >= 3 else m_pre
        first3 = float(np.mean(pre[:3])) if len(pre) >= 3 else m_pre
        if top < MOVE:
            kind = "static hold"
        elif m_pre >= max(MOVE, 0.5 * act_med):
            kind = "mid-motion" + (" (accelerating into cut)" if last3 > 1.5 * max(first3, 0.5) else "")
        elif m_pre < ACT or last3 < first3:
            kind = "action completion"
        else:
            kind = "mid-motion (low)"
        out.append({"cut_frame": int(c), "cut_s": r3(c / fps), "pre_motion": r3(m_pre), "shot_top_motion": r3(top),
                    "shot_active_median": r3(act_med), "incoming_motion": r3(m_post),
                    "settle_frames": settle_f, "settle_ms": r3(1000 * settle_f / fps) if settle_f is not None else None,
                    "active_frames_frac": r3(len(act_idx) / L) if L else None,
                    "motivation": kind, "incoming_starts_moving": bool(m_post >= MOVE), "outgoing_frames": int(L),
                    "confidence": "ok" if L >= 2 * W else "low (short shot)"})
        prev_start, prev_elev_end = c, k1
    return out


def sfx_placement(sfx, cut_times, beats, period, sections, sec_locks):
    ct = np.asarray(cut_times, float)
    b = np.asarray(beats, float)
    for c in sfx:
        t = c["t"]
        dc = (t - ct[np.argmin(np.abs(ct - t))]) if len(ct) else None
        db = (t - b[np.argmin(np.abs(b - t))]) if len(b) else None
        hb = np.sort(np.r_[b, (b[:-1] + b[1:]) / 2]) if len(b) > 1 else b
        dh = (t - hb[np.argmin(np.abs(hb - t))]) if len(hb) else None
        on_cut = dc is not None and abs(dc) <= 0.100
        on_beat = db is not None and abs(db) <= BEAT_TOL
        c["sfx_minus_cut_ms"] = r3(1000 * dc) if dc is not None else None
        c["sfx_minus_beat_ms"] = r3(1000 * db) if db is not None else None
        c["sfx_minus_halfbeat_ms"] = r3(1000 * dh) if dh is not None else None
        c["placement"] = "cut + beat" if (on_cut and on_beat) else ("cut" if on_cut else ("beat" if on_beat else ("half-beat" if dh is not None and abs(dh) <= BEAT_TOL else "neither")))
        sec = next((s for s in sections if s["start_s"] <= t < s["end_s"]), None)
        c["section"] = sec["name"] if sec else None
    return sfx


def rhythm_analysis(meta, shots, cuts, cut_times, diffs, audio):
    """Sections, per-cut beat phase + subdivision, offset-corrected grid lock (overall/per section/runs),
    cut motivation and SFX placement."""
    fps, dur = meta["fps"], meta["duration_s"]
    ana = {"sections": pacing_sections(cut_times, dur)}
    mot = cut_motivation(cuts, shots, diffs, fps)
    beats = audio.get("beats_s") or []
    has_grid = bool(audio.get("present") and not audio.get("silent_track") and audio.get("beat_reliable") and len(beats) >= 4)
    P = audio.get("beat_period_s") or (float(np.median(np.diff(beats))) if len(beats) > 2 else None)
    ana["has_grid"] = has_grid
    ana["beat_period_s"] = P
    sec_of = {}
    for s in ana["sections"]:
        for i in s["cut_idx"]:
            sec_of[i] = s["name"]
    per = []
    for i, (c, m) in enumerate(zip(cut_times, mot)):
        per.append({"i": i, "cut_s": r3(c), "frame": int(cuts[i]), "section": sec_of.get(i), **{k: m[k] for k in
                    ("motivation", "pre_motion", "shot_top_motion", "settle_ms", "incoming_motion", "incoming_starts_moving", "confidence")}})
    ana["per_cut"] = per
    ana["runs"] = []
    if has_grid and len(cut_times):
        ph, _ = beat_phases(cut_times, beats)
        b = np.asarray(beats)
        for pc, p_ in zip(per, ph):
            k = int(np.argmin(np.abs(b - pc["cut_s"])))
            pc["beat_offset_ms"] = r3(1000 * (pc["cut_s"] - b[k]))
            pc["phase"] = r3(p_)
            d0, dh = min(p_, 1 - p_) * P, abs(p_ - 0.5) * P
            pc["subdivision"] = "on-beat" if d0 <= BEAT_TOL else ("half-beat" if dh <= BEAT_TOL else "other")
            pc["halfbeat_offset_ms"] = r3(1000 * _wrap(p_ * 2) * P / 2)
        ana["overall"] = grid_lock(ph, P)
        for s in ana["sections"]:
            if len(s["cut_idx"]) >= 3:
                s["lock"] = grid_lock(ph[s["cut_idx"]], P)
        runs = locked_runs(cut_times, ph, P)
        for r in runs:
            r["p"] = r3(run_significance(len(cut_times), r["cuts"], P if r["grid"] == "beat" else P / 2))
            r["section"] = sec_of.get(r["first_cut"])
        ana["runs"] = runs
        # per-cut corrected subdivision: use the run that contains the cut, else the section lock
        att = audio.get("pulse_attack_offset_ms") or 0.0
        for pc, p_ in zip(per, ph):
            src = next((r for r in runs if r["first_cut"] <= pc["i"] <= r["last_cut"] and r["p"] < 0.05), None)
            off, grid = (src["offset_ms"], src["grid"]) if src else (None, None)
            if src is None:
                sec = next((s for s in ana["sections"] if pc["i"] in s["cut_idx"]), None)
                lk = (sec or {}).get("lock") or {}
                if lk.get("lock"):
                    g = lk["grids"][lk["lock"]]
                    off, grid = g["offset_ms"], lk["lock"]
            if off is not None:
                pc_ph = (p_ - off / 1000 / P) % 1.0
                gp = P if grid == "beat" else P / 2
                resid = _wrap(pc_ph * (1 if grid == "beat" else 2)) * gp
                pc["locked"] = bool(abs(resid) <= LOCK_TOL)
                pc["corrected_resid_ms"] = r3(1000 * resid)
                pc["corrected_subdivision"] = ("on-beat" if min(pc_ph, 1 - pc_ph) * P <= LOCK_TOL else
                                               ("half-beat" if abs(pc_ph - 0.5) * P <= LOCK_TOL else "other")) if pc["locked"] else "off-grid"
                pc["grid_offset_ms"] = off
            else:
                pc["locked"] = False
        ana["pulse_attack_offset_ms"] = att if audio.get("pulse_attack_offset_ms") is not None else None
    # per-section motivation + verdict
    for s in ana["sections"]:
        pcs = [per[i] for i in s["cut_idx"]]
        cnt = {}
        for pc in pcs:
            k = pc["motivation"].split(" (")[0]
            cnt[k] = cnt.get(k, 0) + 1
        s["motivation_counts"] = cnt
        st = [pc["settle_ms"] for pc in pcs if pc["motivation"] == "action completion" and pc["settle_ms"] is not None]
        s["median_settle_ms"] = r3(np.median(st)) if st else None
        n = len(pcs)
        run = max((r for r in ana["runs"] if r.get("section") == s["name"] and r["p"] < 0.05), key=lambda r: r["cuts"], default=None)
        lk = (s.get("lock") or {})
        if not n:
            s["verdict"] = "no cuts (held shot)"
            continue
        if has_grid and (lk.get("lock") or (run and run["cuts"] >= max(3, 0.5 * n))):
            g = lk["grids"][lk["lock"]] if lk.get("lock") else None
            grid = lk.get("lock") or run["grid"]
            off = g["offset_ms"] if g else run["offset_ms"]
            s["verdict"] = f"music-driven: grid-locked to the {grid} grid, {abs(off):.0f} ms {'before' if off < 0 else 'after'} the pulse"
            s["logic"] = "grid"
        elif cnt.get("action completion", 0) + cnt.get("mid-motion", 0) >= 0.6 * n:
            s["verdict"] = (f"action-driven: cuts follow on-screen motion ({cnt.get('action completion', 0)} after the motion settles, "
                            f"{cnt.get('mid-motion', 0)} mid-motion), not the pulse")
            s["logic"] = "action"
        elif cnt.get("static hold", 0) >= 0.5 * n:
            s["verdict"] = "hold-driven: timed reads on static frames"
            s["logic"] = "hold"
        else:
            s["verdict"] = "mixed"
            s["logic"] = "mixed"
        if run and s.get("logic") != "grid":
            s["verdict"] += f"; contains a grid-locked run {run['start_s']:.2f}–{run['end_s']:.2f}s"
    if audio.get("sfx_candidates"):
        sfx_placement(audio["sfx_candidates"], cut_times, beats, P, ana["sections"], None)
    return ana


# ----------------------------------------------------------------------------- charts

MOT_COLORS = {"action completion": "#2ca02c", "mid-motion": "#d62728", "static hold": "#7f7f7f", "mid-motion (low)": "#ff9896"}


def timeline_png(path, duration, cut_times, pacing, audio, shots, ana):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(4, 1, figsize=(14, 9), sharex=True, gridspec_kw={"height_ratios": [1.2, 1.5, 1.4, 0.9]})
    sec_cols = ["#e8f0fe", "#fde8e8", "#e8f8ec", "#fff4dc"]
    for k, s in enumerate(ana.get("sections", [])):
        for a in axs:
            a.axvspan(s["start_s"], s["end_s"], color=sec_cols[k % 4], zorder=-5)
        axs[0].text(s["start_s"] + 0.05, 0.62, s["name"], fontsize=8, va="bottom")
    ax = axs[0]
    for s in shots:
        ax.barh(0, s["duration_s"], left=s["start_s"], height=0.8, color=plt.cm.tab20(s["index"] % 20), edgecolor="k", linewidth=0.3)
        if s["duration_s"] > duration / 40:
            ax.text(s["start_s"] + s["duration_s"] / 2, 0, str(s["index"]), ha="center", va="center", fontsize=7)
    for pc in ana.get("per_cut", []):
        col = next((v for k, v in MOT_COLORS.items() if pc["motivation"].startswith(k)), "#9467bd")
        ax.plot(pc["cut_s"], 0.5, marker="v", color=col, ms=7)
    ax.set_ylim(-0.6, 1.0)
    ax.set_yticks([]); ax.set_title("Shots · cut motivation ▼ (green = action completion, red = mid-motion, grey = static hold) · sections shaded")
    ax = axs[1]
    if audio.get("present") and audio.get("rms_curve_dbfs"):
        ax.plot([p["t"] for p in audio["rms_curve_dbfs"]], [p["db"] for p in audio["rms_curve_dbfs"]], lw=1, label="RMS dBFS")
        if audio.get("short_term_lufs"):
            ax.plot([p["t"] for p in audio["short_term_lufs"]], [p["lufs_st"] for p in audio["short_term_lufs"]], lw=1.5, label="short-term LUFS")
        for b in audio.get("beats_s", []):
            ax.axvline(b, color="0.65", lw=0.5, zorder=0)
        for c in audio.get("sfx_candidates", []):
            ax.axvline(c["t"], color="orange", lw=1.2, ls="--")
        ax.set_ylim(-70, 0); ax.legend(loc="lower right", fontsize=7)
    ax.set_ylabel("level")
    ax.set_title("Audio level · pulses (grey) · cuts (red) · SFX candidates (orange dashed)", fontsize=9)
    for c in cut_times:
        for a in axs[:2]:
            a.axvline(c, color="red", lw=0.8)
    ax = axs[2]
    pcs = ana.get("per_cut", [])
    if pcs and pcs[0].get("beat_offset_ms") is not None:
        ax.axhspan(-BEAT_TOL * 1000, BEAT_TOL * 1000, color="0.88", zorder=-1)
        ax.axhline(0, color="0.4", lw=0.8)
        P = ana.get("beat_period_s") or 0.5
        for h in (P * 500, -P * 500):
            ax.axhline(h, color="0.6", lw=0.6, ls=":")
        cols = ["#1f77b4" if pc.get("locked") else "#bbbbbb" for pc in pcs]
        ax.scatter([pc["cut_s"] for pc in pcs], [pc["beat_offset_ms"] for pc in pcs], c=cols, s=28, zorder=3, label="cut − nearest pulse (ms)")
        for r in ana.get("runs", []):
            ax.plot([r["start_s"], r["end_s"]], [r["offset_ms"]] * 2, color="#1f77b4", lw=2)
            if r["grid"] == "half-beat":
                for h in (P * 500, -P * 500):
                    ax.plot([r["start_s"], r["end_s"]], [h + r["offset_ms"]] * 2, color="#1f77b4", lw=1, ls="--")
            ax.text(r["start_s"], r["offset_ms"] + 25, f"{r['grid']} lock {r['offset_ms']:+.0f}ms", fontsize=7, color="#1f77b4")
        ax.set_ylim(-P * 560, P * 560)
        ax.set_ylabel("ms vs pulse")
        ax.set_title("Beat phase of each cut (grey band = ±80ms; dotted = half-beat; blue = locked to an offset grid)", fontsize=9)
    else:
        ax.text(0.5, 0.5, "no pulse grid (no audio / unreliable beat)", transform=ax.transAxes, ha="center")
    ax = axs[3]
    ax.plot([p["t"] for p in pacing], [p["cuts_per_s"] for p in pacing], color="purple")
    ax.set_ylabel("cuts/s (2s)"); ax.set_xlabel("seconds")
    ax.set_xlim(0, duration)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
    return str(path)

# ----------------------------------------------------------------------------- report

def pacing_curve(cut_times, duration, win=2.0, hop=0.5):
    out = []
    ct = np.array(cut_times)
    t = 0.0
    while t <= max(0.0, duration):
        a, b = max(0, t - win / 2), min(duration, t + win / 2)
        n = int(((ct >= a) & (ct < b)).sum()) if len(ct) else 0
        out.append({"t": r3(t), "cuts_per_s": r3(n / max(1e-6, b - a))})
        t += hop
    return out


def sparkline(vals):
    bars = "▁▂▃▄▅▆▇█"
    if not vals:
        return ""
    m = max(vals) or 1
    return "".join(bars[min(7, int(v / m * 7.999))] for v in vals)


def music_prompt(meta, audio, cut_stats):
    """Returns (prompt, measured dict, guessed dict)."""
    dur = meta["duration_s"]
    if not audio.get("present") or audio.get("silent_track"):
        p = (f"Clean, modern product-launch music bed, confident and playful, light percussion, instrumental, exactly {dur:.0f} seconds; "
             f"short intro, steady body, clear ending hit with no fade tail. Tempo: choose by ear (the reference has no audio, and the tempo is not inferred from cut cadence).")
        return p, {"duration_s": dur, "bpm": None, "note": "reference has no audio track"}, {"genre": "RECOMMENDED default (not from reference)", "mood": "-", "tone": "-", "drums": "-"}
    bpm = audio.get("tempo_bpm")
    measured = {"duration_s": dur, "bpm": bpm if audio.get("beat_reliable") else None, "integrated_lufs": audio.get("integrated_lufs"),
                "key_estimate": audio.get("key_estimate"), "sections": audio.get("sections")}
    cent = audio.get("spectral_centroid_hz_median") or 2000
    perc = audio.get("percussive_energy_ratio") or 0.3
    speech = (audio.get("speech") or {}).get("present")
    minor = "minor" in (audio.get("key_estimate") or "")
    if not audio.get("beat_reliable"):
        groove = "ambient / free-time pad bed (no strong pulse detected)"
    elif bpm and bpm >= 135:
        groove = "driving high-energy electronic (techno/house/drum & bass feel)"
    elif bpm and bpm >= 112:
        groove = "upbeat modern electronic / tech-house pop groove"
    elif bpm and bpm >= 90:
        groove = ("mid-tempo punchy electronic / hip-hop beat" if (perc > 0.3 or (audio.get("integrated_lufs") or -30) > -16)
                  else "mid-tempo lo-fi / chill electronic groove")
    else:
        groove = "slow cinematic / downtempo"
    tone = "bright, airy, crisp highs" if cent > 2800 else ("warm, rounded" if cent < 1500 else "clean, balanced")
    mood = ("sleek, confident, slightly tense" if minor else "optimistic, confident, playful")
    drums = ("minimal / no drums" if not audio.get("beat_reliable") else
             ("punchy percussive drums" if perc > 0.45 else ("light percussion" if perc > 0.2 else "minimal / no drums")))
    guessed = {"genre": groove, "tone": tone, "mood": mood, "drums": drums}
    secs = audio.get("sections") or []
    struct = "; ".join(f"{s['start_s']:.0f}-{s['end_s']:.0f}s {s['role_guess']} ({s['energy']})" for s in secs) or "single section"
    vo = " Leave space for a voiceover: keep mids sparse." if speech else " Instrumental only, no vocals."
    tempo_txt = f"{bpm:.0f} BPM" if (bpm and audio.get("beat_reliable")) else "loose tempo"
    key_txt = f", in {audio.get('key_estimate')}" if audio.get("key_estimate") else ""
    hits = ""
    if cut_stats and (cut_stats.get("pct_on_beat") or 0) >= 50:
        hits = " Clear, regular accent hits so video cuts can land on them."
    prompt = (f"{groove.split(' (')[0].capitalize()}, {mood}, {tone}, {drums}, {tempo_txt}{key_txt}. "
              f"Exactly {dur:.0f} seconds for a product launch video. Structure: {struct}. "
              f"End on a clean resolved hit (no fade tail).{hits}{vo}")
    return prompt, measured, guessed


def post_title(meta):
    t = " ".join((meta.get("post_text_expanded") or meta.get("post_text") or "").split())
    t = re.sub(r"https?://(?!t\.co)(\S+?)\.?(?=\s|$)", r"\1", t)
    t = re.sub(r"https?://t\.co/\S+", "", t).replace("&amp;", "&").strip()
    first = re.split(r"(?<=[.!?])\s|\n", t)[0].strip() if t else ""
    if len(first) > 60:
        first = first[:60].rsplit(" ", 1)[0].rstrip(" .,:;") + "…"
    first = first.rstrip(" .,:;")
    who = meta.get("author") or meta.get("author_handle") or "X post"
    return f"{who} — {first}" if first else f"{who} post {meta['post_id']}"


TMAP = {"hard cut": "plain cut",
        "dissolve/cross-fade or animated wipe": "`@remotion/transitions` fade()/wipe()/slide() with linearTiming({durationInFrames: N})",
        "fade/dip through black": "black <AbsoluteFill> opacity interpolate(frame,[0,N/2,N],[0,1,0])",
        "cut via black frame": "1–2 black frames between shots",
        "flash (white/bright frame)": "white <AbsoluteFill> opacity 0→1→0 over 3–5 frames on the cut",
        "whip/motion-blur": "slide() with a short spring + velocity-keyed blur"}


def _f(t, fps):
    return f"{t:.2f}s/f{int(round(t * fps))}"


def _ms(x):
    return "–" if x is None else f"{x:+.0f}"


def chat_report(meta, shots, cut_times, pacing, audio, ana, paths, notes):
    fps, fpsn, dur = meta["fps"], meta["fps_nominal"], meta["duration_s"]
    n_cuts = len(cut_times)
    durs = [s["duration_s"] for s in shots]
    asl = r3(np.mean(durs)) if shots else None
    med = r3(np.median(durs)) if shots else None
    trans = {}
    for s in shots[1:]:
        k = s["transition_in"].split(" (")[0]
        trans[k] = trans.get(k, 0) + 1
    tr_txt = ", ".join(f"{k} ×{v}" for k, v in sorted(trans.items(), key=lambda x: -x[1])) or "single shot"
    has_audio = bool(audio.get("present")) and not audio.get("silent_track")
    cba = audio.get("cut_beat_alignment") or {}
    speech = audio.get("speech") or {}
    sfx = audio.get("sfx_candidates") or []
    mp, m_meas, m_guess = music_prompt(meta, audio, cba)
    P = ana.get("beat_period_s")
    L = []
    a = L.append
    a(f"**Video reference: {post_title(meta)}**")
    a(f"{meta['url']}")
    a("")
    a("_Signal analysis, not an audition: nobody watched or listened; every claim comes from DSP/computer vision. "
      "Labels: MEASURED = computed from the file · ESTIMATED = detector/tracker output · INFERRED = interpretation · RECOMMENDED = build advice._")
    a("")
    a("**At a glance**")
    a(f"- Format [MEASURED: {meta.get('probe_source', 'ffprobe')}]: {meta['width']}×{meta['height']} ({meta['aspect_ratio_nearest']}, {meta['orientation']}), {fps:g} fps, {dur:.2f}s = {int(round(dur * fps))} frames")
    a(f"- Shots [ESTIMATED: PySceneDetect ×3 + edge/diff validation]: {len(shots)} shots / {n_cuts} cuts · ASL {asl}s · median {med}s · {r3(n_cuts / dur) if dur else 0} cuts/s")
    a(f"- Transitions [ESTIMATED]: {tr_txt}")
    if has_audio:
        alt = audio["tempo_alternatives_bpm"]
        a(f"- Tempo [ESTIMATED: beat tracker, audio only, never from cuts]: {audio.get('tempo_bpm'):.1f} BPM (half/double {alt[0]:.0f}/{alt[1]:.0f}), pulse period {P:.3f}s = {P * fps:.1f} frames · "
          + (f"regular (clarity {audio.get('beat_clarity')}, IBI CV {audio.get('beat_regularity_cv')})" if audio.get("beat_reliable") else "pulse NOT reliable"))
        a(f"- Loudness [MEASURED: BS.1770]: {audio.get('integrated_lufs'):.1f} LUFS, peak {audio.get('peak_dbfs'):.1f} dBFS · key ~{audio.get('key_estimate')} [ESTIMATED, conf {audio.get('key_confidence')}]")
        a("- Speech [ESTIMATED: faster-whisper]: " + (f"{speech.get('word_count')} words, {speech.get('speech_seconds')}s ({speech.get('language')})" if speech.get("present")
                                                     else ("skipped" if speech.get("note") == "skipped" else "none detected (does not rule out sung/processed vocals)")))
    else:
        a("- Audio [MEASURED]: " + ("**no audio track**, so no tempo, beat, SFX or loudness. Tempo is NOT inferred from cut cadence." if not meta.get("has_audio") else (audio.get("note") or "unusable")))
    a("")
    # ---- cut logic by section
    a("**Cut logic by section** [sections MEASURED from shot lengths; verdict INFERRED]")
    for s in ana["sections"]:
        n = len(s["cut_idx"])
        head = f"- **{s['name'].capitalize()} {s['start_s']:.2f}–{s['end_s']:.2f}s** ({n} cuts, median shot {s['median_shot_s']}s)"
        if not n:
            a(head + f": held shot / end card, {s['end_s'] - s['start_s']:.2f}s.")
            continue
        mc = ", ".join(f"{k} {v}" for k, v in sorted(s["motivation_counts"].items(), key=lambda x: -x[1]))
        parts = [f"motion [ESTIMATED]: {mc}" + (f" (motion settles a median {s['median_settle_ms']:.0f} ms before the cut)" if s.get("median_settle_ms") is not None else "")]
        lk = s.get("lock")
        if lk:
            g = lk["grids"]
            parts.append("beat grid [MEASURED vs ESTIMATED pulses]: raw ±80ms on-beat {:.0f}% (chance {:.0f}%); offset-corrected lock beat {}/{} @ {:+.0f}ms (p={}), half-beat {}/{} @ {:+.0f}ms (p={})".format(
                g["beat"]["pct_raw_80ms"], 100 * min(1, 2 * BEAT_TOL / P), g["beat"]["locked"], n, g["beat"]["offset_ms"], g["beat"]["mc_p"],
                g["half-beat"]["locked"], n, g["half-beat"]["offset_ms"], g["half-beat"]["mc_p"]))
        a(head + ": " + "; ".join(parts) + f". → **{s['verdict']}** [INFERRED]")
    for r in ana.get("runs", []):
        sig = "significant" if r["p"] < 0.05 else "not significant"
        a(f"- **Locked run [MEASURED]:** {r['start_s']:.3f}–{r['end_s']:.3f}s, {r['cuts']} consecutive cuts on the {r['grid']} grid at {r['offset_ms']:+.0f} ms "
          f"(= {r['offset_ms'] * fps / 1000:+.1f} frames, spread sd {r['resid_sd_ms']:.0f} ms; chance of a run this long p={r['p']}, {sig}).")
    att = ana.get("pulse_attack_offset_ms")
    if att is not None and ana.get("runs"):
        r0 = max(ana["runs"], key=lambda r: r["cuts"])
        a(f"- Pulse timing check [MEASURED: hi-res onset peaks]: the sharpest attack sits {att:+.0f} ms from the tracked pulse, so vs the actual hit the locked cuts are ≈{r0['offset_ms'] - att:+.0f} ms "
          f"({(r0['offset_ms'] - att) * fps / 1000:+.1f} frames).")
    a("")
    # ---- beat phase overall
    if ana.get("has_grid") and ana.get("overall"):
        g = ana["overall"]["grids"]
        a("**Beat phase, whole video** [MEASURED cut times vs ESTIMATED pulses]")
        a(f"- Raw: {cba.get('cuts_on_beat')}/{cba.get('cuts_considered')} = {cba.get('pct_on_beat'):.0f}% within ±80ms of a pulse (chance {cba.get('chance_pct'):.0f}%); half-beat grid {cba.get('pct_on_half_beat_grid'):.0f}% (chance {cba.get('half_grid_chance_pct'):.0f}%).")
        a(f"- Offset-corrected (±{int(LOCK_TOL * 1000)}ms around the best constant offset): beat grid {g['beat']['pct_locked_offset_corrected']:.0f}% @ {g['beat']['offset_ms']:+.0f}ms (p={g['beat']['mc_p']}); "
          f"half-beat grid {g['half-beat']['pct_locked_offset_corrected']:.0f}% @ {g['half-beat']['offset_ms']:+.0f}ms (p={g['half-beat']['mc_p']}). p = Monte-Carlo chance of doing this well with random cut phases, offset search included.")
        a("")
    # ---- per cut table
    a("**Per cut** [times MEASURED; Δpulse vs ESTIMATED pulses; motion ESTIMATED from frame differences; labels INFERRED]")
    a("```")
    if ana.get("has_grid"):
        a(f"{'cut':>8} {'frame':>6} {'sect':<8} {'Δpulse':>7} {'phase':>5} {'subdiv':<9} {'grid-corr':<15} {'motivation':<26} {'settle':>6} {'in-mot':>6}")
    else:
        a(f"{'cut':>8} {'frame':>6} {'sect':<8} {'motivation':<26} {'settle':>6} {'pre':>6} {'top':>6} {'in-mot':>6}")
    for pc in ana["per_cut"]:
        st = "–" if pc["settle_ms"] is None else f"{pc['settle_ms']:.0f}"
        if ana.get("has_grid"):
            corr = (f"{pc['corrected_subdivision']} {pc['corrected_resid_ms']:+.0f}" if pc.get("grid_offset_ms") is not None else "–")
            a(f"{pc['cut_s']:>7.3f}s {pc['frame']:>6} {(pc['section'] or '')[:8]:<8} {_ms(pc['beat_offset_ms']):>7} {pc['phase']:>5.2f} {pc['subdivision']:<9} {corr:<15} {pc['motivation'][:26]:<26} {st:>6} {pc['incoming_motion']:>6.1f}")
        else:
            a(f"{pc['cut_s']:>7.3f}s {pc['frame']:>6} {(pc['section'] or '')[:8]:<8} {pc['motivation'][:26]:<26} {st:>6} {pc['pre_motion']:>6.1f} {pc['shot_top_motion']:>6.1f} {pc['incoming_motion']:>6.1f}")
    a("```")
    a("Δpulse = cut − nearest pulse (ms, negative = cut before the pulse). phase 0 = on a pulse, 0.5 = halfway. grid-corr = subdivision and residual (ms) after removing the section/run offset. "
      "settle = ms of stillness between the outgoing shot's last motion and the cut. in-mot = motion in the first ~6 frames of the new shot (≥2 = it starts moving right away).")
    a("")
    # ---- SFX
    if sfx:
        a("**SFX** [times ESTIMATED: onset strength; type INFERRED from spectrum; placement MEASURED vs cuts/pulses]")
        a("```")
        a(f"{'time':>8} {'frame':>6} {'placement':<11} {'Δcut':>6} {'Δpulse':>7} {'type':<22} prompt")
        for c in sfx:
            a(f"{c['t']:>7.3f}s {int(round(c['t'] * fps)):>6} {c.get('placement', ''):<11} {_ms(c.get('sfx_minus_cut_ms')):>6} {_ms(c.get('sfx_minus_beat_ms')):>7} {c['type_guess'][:22]:<22} {c['prompt']}")
        a("```")
        pl = {}
        for c in sfx:
            pl[c.get("placement")] = pl.get(c.get("placement"), 0) + 1
        a("Placement counts: " + ", ".join(f"{k} {v}" for k, v in pl.items()) + ". (cut = within ±100ms of a cut, beat = within ±80ms of a pulse; Δ = SFX − cut/pulse. Onsets on the pulse that are NOT at a cut are treated as music, so a 'beat only' SFX can't be listed.)")
        a("")
    # ---- shots
    a("**Shots** [ESTIMATED cuts; text ESTIMATED by OCR" + ("" if ocr_available() else ": OCR unavailable (no tesseract)") + "]")
    a("```")
    for s in shots:
        txt = " / ".join(s.get("ocr_text", []))[:50]
        a(f"#{s['index']:<3} {s['start_s']:>6.2f}–{s['end_s']:<6.2f} {s['duration_s']:>5.2f}s f{s['start_frame']:<5} {s['transition_in'].split(' (')[0][:24]:<24} {txt}")
    a("```")
    a("")
    # ---- remotion
    a("**Remotion/Codex brief** [MEASURED timings; RECOMMENDED implementation]")
    a(f"- Composition: width={meta['width']}, height={meta['height']}, fps={fpsn}, durationInFrames={int(round(dur * fps))}" + (f" (source {fps} fps)" if abs(fps - fpsn) > 0.01 else ""))
    for k, v in trans.items():
        a(f"- {k} ×{v} → {next((t for kk, t in TMAP.items() if k.startswith(kk.split(' (')[0])), 'plain cut; check keyframes')}")
    for s in ana["sections"]:
        if not s["cut_idx"]:
            a(f"- {s['name']} {s['start_s']:.2f}–{s['end_s']:.2f}s: hold {int(round((s['end_s'] - s['start_s']) * fps))} frames.")
            continue
        lg = s.get("logic")
        if lg == "grid" and P:
            lk = s.get("lock") or {}
            run = max((r for r in ana["runs"] if r.get("section") == s["name"]), key=lambda r: r["cuts"], default=None)
            grid = lk.get("lock") or (run or {}).get("grid")
            off = lk["grids"][lk["lock"]]["offset_ms"] if lk.get("lock") else run["offset_ms"]
            step = P * fps / (1 if grid == "beat" else 2)
            a(f"- {s['name']} {s['start_s']:.2f}–{s['end_s']:.2f}s: snap cuts to the {grid} grid (every {step:.1f} frames, multiples of it), then shift {int(round(off * fps / 1000)):+d} frames ({off:+.0f} ms) so the new shot lands {'just ahead of' if off < 0 else 'just after'} the hit.")
        elif lg == "action":
            stl = s.get("median_settle_ms")
            a(f"- {s['name']} {s['start_s']:.2f}–{s['end_s']:.2f}s: cut on the action, not the beat: let each animation finish, hold ~{int(round((stl or 200) * fps / 1000))} frames ({(stl or 200):.0f} ms), then cut; mid-motion cuts carry movement across the cut. Snap only big moments to a pulse.")
        else:
            a(f"- {s['name']} {s['start_s']:.2f}–{s['end_s']:.2f}s: keep the measured shot lengths.")
    a("```ts")
    a("export const scenes = [")
    for s in shots:
        a(f"  {{ id: {s['index']}, from: {s['start_frame']}, durationInFrames: {s['end_frame'] - s['start_frame']}, transitionIn: {json.dumps(s['transition_in'].split(' (')[0])}, text: {json.dumps(' / '.join(s.get('ocr_text', []))[:60])} }},")
    a("];")
    if ana.get("has_grid"):
        a(f"export const beatFrames = [{', '.join(str(int(round(b * fps))) for b in audio.get('beats_s', []))}]; // ESTIMATED pulses @ {fps:g} fps")
    a("```")
    a("")
    # ---- ElevenLabs
    a("**ElevenLabs**")
    a("Music prompt [RECOMMENDED; BPM/duration/loudness MEASURED or ESTIMATED as noted above; genre/mood/tone INFERRED, check by ear]:")
    a("```")
    a(mp)
    a("```")
    if has_audio:
        a(f"INFERRED character: genre \"{m_guess['genre']}\", mood \"{m_guess['mood']}\", tone \"{m_guess['tone']}\", drums \"{m_guess['drums']}\".")
    a("")
    a("**Visual style** [INFERRED: needs a look at the contact sheet] · dominant colors per shot [MEASURED: k-means]: " + "; ".join(f"#{s['index']} " + " ".join(c['hex'] for c in s.get('dominant_colors', [])[:3]) for s in shots[:30]))
    a("")
    if notes:
        a("**Run notes:** " + " · ".join(notes))
        a("")
    a(paste_block(meta, shots, cut_times, audio, ana, asl, med, tr_txt))
    a("")
    a("**Files**")
    a(f"- contact sheet: {paths.get('contact_sheet')}")
    a(f"- timeline: {paths.get('timeline')}")
    a(f"- keyframes: {paths.get('keyframes_dir')}")
    if paths.get("report_json"):
        a(f"- report.json: {paths['report_json']}")
    a(f"- video: {paths.get('video')}")
    return "\n".join(L)


def paste_block(meta, shots, cut_times, audio, ana, asl, med, tr_txt):
    fps, dur = meta["fps"], meta["duration_s"]
    has_audio = bool(audio.get("present")) and not audio.get("silent_track")
    cba = audio.get("cut_beat_alignment") or {}
    L = []
    a = L.append
    a("**Production handoff**")
    a("")
    logic = "; ".join(f"{s['name']} {s['start_s']:.1f}–{s['end_s']:.1f}s {s.get('verdict', '')}" for s in ana["sections"] if s["cut_idx"])
    if has_audio:
        aud = (f"audio ~{audio.get('tempo_bpm'):.0f} BPM (est., half/double {audio['tempo_alternatives_bpm'][0]:.0f}/{audio['tempo_alternatives_bpm'][1]:.0f}), {audio.get('integrated_lufs'):.1f} LUFS, "
               f"{cba.get('pct_on_beat', 0):.0f}% of cuts raw-on-pulse ±80ms" + ("; VO present" if (audio.get("speech") or {}).get("present") else "; no ASR speech"))
    else:
        aud = "NO audio track"
    a("(a) Reference evidence")
    a(f"- [{post_title(meta)}, {dur:.1f} seconds]({meta['url']}): {meta['width']}×{meta['height']} {meta['aspect_ratio_nearest']} @ {fps:g} fps; {len(shots)} shots, ASL {asl}s ({tr_txt}); {aud}. "
      f"Cut logic: {logic}. (Signal analysis, not an audition.)")
    a("")
    a("(b) Format/pacing row")
    a("")
    a("| Format | structure and reference timing |")
    a("|---|---|")
    secs = " → ".join(f"{s['name']} {s['start_s']:.2f}–{s['end_s']:.2f}s (f{int(round(s['start_s'] * fps))}–{int(round(s['end_s'] * fps))}, median shot {s['median_shot_s']}s, {s.get('logic', 'hold')})" for s in ana["sections"])
    a(f"| {meta['width']}×{meta['height']} ({meta['aspect_ratio_nearest']}) @ {fps:g} fps, {dur:.2f}s / {int(round(dur * fps))} frames [MEASURED] | {len(shots)} shots, {len(cut_times)} cuts, ASL {asl}s, median {med}s [ESTIMATED cuts]; "
      f"{secs}. Cuts (s/frame): " + ", ".join(f"{c:.2f}/f{int(round(c * fps))}" for c in cut_times[:30]) + (" …" if len(cut_times) > 30 else "") + " |")
    a("")
    a("(c) SFX palette")
    a("")
    a("| Effect | Starting duration | Example prompt and use |")
    a("|---|---|---|")
    sfx = audio.get("sfx_candidates") or []
    if sfx:
        groups = {}
        for c in sfx:
            groups.setdefault(c["type_guess"], []).append(c)
        for k, cs in sorted(groups.items(), key=lambda kv: kv[1][0]["t"]):
            d = float(np.median([c["duration_s"] for c in cs]))
            where = ", ".join(f"{c['t']:.2f}s/f{int(round(c['t'] * fps))} ({c.get('placement', '?')})" for c in cs[:8])
            a(f"| {k} [INFERRED] | {d:.1f}s | \"{cs[0]['prompt']}\" at {where} [ESTIMATED times] |")
    else:
        reason = "no audio track" if not has_audio else "no distinct transients detected"
        hard = [s for s in shots[1:] if "hard" in s["transition_in"]]
        soft = [s for s in shots[1:] if "hard" not in s["transition_in"]]
        if hard:
            a(f"| transition hit [RECOMMENDED; {reason}] | 0.4s | \"tight modern UI transition hit, clean, no reverb\" on hard cuts at " + ", ".join(_f(s['start_s'], fps) for s in hard[:8]) + " |")
        if soft:
            a(f"| soft whoosh [RECOMMENDED; {reason}] | 0.8s | \"soft airy whoosh, gentle swell\" under gradual transitions at " + ", ".join(_f(s['start_s'], fps) for s in soft[:8]) + " |")
        if not (hard or soft):
            a(f"| none | – | nothing to anchor SFX to ({reason}) |")
    return "\n".join(L)


# ----------------------------------------------------------------------------- main

def _jsonable(o):
    return o.item() if hasattr(o, "item") else str(o)


def main():
    ap = argparse.ArgumentParser(description="Analyze a reference video (X post URL or local file). Prints a chat-ready report to stdout.")
    ap.add_argument("source", help="x.com/<user>/status/<id> URL, or a path to a local video file")
    ap.add_argument("--out", help="run dir for images/json (default: a new dir under the system temp folder)")
    ap.add_argument("--whisper-model", default="small", help="faster-whisper model (tiny/base/small/medium) or 'none'")
    ap.add_argument("--media-url", help="direct mp4 URL (e.g. from the X MCP media variants) if yt-dlp fails")
    ap.add_argument("--post-json", help="saved X MCP get_posts_by_id(s) JSON with media variants + post text")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--no-json", action="store_true", help="do not write report.json")
    args = ap.parse_args()

    t0 = time.time()
    notes = []
    src = os.path.expanduser(args.source)
    if os.path.isfile(src):
        video = Path(src).resolve()
        post_id = re.sub(r"[^A-Za-z0-9_-]+", "-", video.stem)[:60] or "local"
        pmeta = {"post_id": post_id, "url": str(video), "download_method": "local file", "author": None, "post_text": video.name}
    else:
        post_id = parse_post_id(args.source)
        log("downloading", args.source)
        video, pmeta = download(args.source, post_id, CACHE / post_id, args.media_url, args.post_json)
        for e in pmeta.get("download_errors", []):
            notes.append("download fallback: " + e)
    out = Path(args.out).expanduser().resolve() if args.out else WORK / "runs" / f"{post_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    meta = {**pmeta, **probe(video)}
    meta.pop("download_errors", None)
    fps = meta["fps"]
    log(f"video {meta['width']}x{meta['height']} {fps}fps {meta['duration_s']}s (probe: {meta.get('probe_source')})")

    log("detecting cuts")
    cands = detect_cuts(video, fps)
    log("frame profile")
    diffs, ecr, luma, sharp, small = frame_profile(video, fps)
    cuts = validate_cuts(cands, diffs, ecr, fps)
    nfr = len(diffs)
    meta["frame_count"] = nfr
    extra = black_boundaries(luma, fps, cuts)
    if extra:
        notes.append(f"added {len(extra)} cut(s) at fades through black")
    cuts = sorted(set(c for c in cuts + extra if 0 < c < nfr))
    bounds = [0] + cuts + [nfr]
    shots = []
    for i in range(len(bounds) - 1):
        a_, b_ = bounds[i], bounds[i + 1]
        if b_ <= a_:
            continue
        s = {"index": len(shots) + 1, "edge_change_at_cut": r3(ecr[a_]) if a_ > 0 else None, "start_frame": a_, "end_frame": b_, "start_s": r3(a_ / fps), "end_s": r3(b_ / fps),
             "duration_s": r3((b_ - a_) / fps), "frames": b_ - a_,
             "motion_mean_diff": r3(np.mean(diffs[a_ + 1:b_])) if b_ - a_ > 2 else None,
             "mean_luma": r3(np.mean(luma[a_:b_]))}
        if a_ == 0:
            s["transition_in"] = "start"; s["transition_frames"] = 0
        else:
            kind, info = classify_transition(a_, diffs, luma, sharp, small, fps)
            s["transition_in"] = kind; s["transition_detail"] = info; s["transition_frames"] = info["transition_frames"]
        shots.append(s)
    del small
    cut_times = [c / fps for c in cuts]

    kf = out / "keyframes"
    kf.mkdir(exist_ok=True)
    do_ocr = (not args.no_ocr) and ocr_available()
    if not args.no_ocr and not do_ocr:
        notes.append("OCR skipped (tesseract/pytesseract not installed)")
    log(f"{len(shots)} shots; keyframes" + (" + OCR" if do_ocr else ""))
    shots_pass(video, shots, kf, fps, do_ocr=do_ocr)
    cs = contact_sheet(shots, out / "contact_sheet.png", fps)

    log("audio analysis")
    audio = analyze_audio(video, out, cut_times, meta["duration_s"], args.whisper_model) if meta["has_audio"] else {"present": False, "note": "no audio stream"}
    if (audio.get("speech") or {}).get("error"):
        notes.append("speech: " + audio["speech"]["error"][:160])
    beats = np.array(audio.get("beats_s") or [])
    for s in shots:
        s["beats_in_shot"] = int(((beats >= s["start_s"]) & (beats < s["end_s"])).sum()) if len(beats) else 0
    log("beat phase + cut motivation")
    ana = rhythm_analysis(meta, shots, cuts, cut_times, diffs, audio)
    pacing = pacing_curve(cut_times, meta["duration_s"])
    tl = timeline_png(out / "timeline.png", meta["duration_s"], cut_times, pacing, audio, shots, ana)
    paths = {"run_dir": str(out), "video": str(video), "audio": audio.get("audio_file"), "contact_sheet": cs, "timeline": tl,
             "keyframes_dir": str(kf), "report_json": None if args.no_json else str(out / "report.json")}
    report = chat_report(meta, shots, cut_times, pacing, audio, ana, paths, notes)
    if not args.no_json:
        rep = {"tool": "reference v2", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "meta": meta, "paths": paths,
               "shots": shots, "cut_times_s": [r3(c) for c in cut_times], "pacing_curve": pacing, "audio": audio, "rhythm": ana,
               "notes": notes, "runtime_s": r3(time.time() - t0)}
        (out / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=_jsonable))
    log(f"done in {time.time() - t0:.1f}s → {out}")
    sys.stdout.write(report + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
