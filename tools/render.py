#!/usr/bin/env python3
"""Floor TV refresh · step 6 · turn the 3 screens into pictures for the Roku app.

Usage:  python3 render.py <workdir>
Needs   <workdir>/index.html  (the TV page, from the "Ambassador Floor TV" artifact)
        <workdir>/data.js     (written by build.py)
Writes  <workdir>/out/screen-1.jpg, screen-2.jpg, screen-3.jpg  (1920x1080, the still-photo view #foto-N)
        <workdir>/out/status.json  {"generatedAt": ..., "renderedAt": ..., "theme": "navidad" or "",
                                    "lights": {"1": [[x, y, "#color", group], ...], ...}}
        The Roku app (1.1.0+) reads theme and lights to add falling snow and make the garland bulbs twinkle.
        <workdir>/out/screen-1.mp4 ... screen-3.mp4  one-minute loops of the moving page (#video-N), for the Roku app 1.2.0+;
        listed in status.json as "video": {"1": "screen-1.mp4", ...}. Needs ffmpeg; skipped with --no-video.
Takes about 5 minutes with the videos (each screen is recorded in real time), so run it with a 10-minute timeout.
The page is rendered offline (no network), so nothing leaves the machine here.
"""
import asyncio, base64, json, os, shutil, subprocess, sys, time, datetime as dt
from playwright.async_api import async_playwright

ARGS = [a for a in sys.argv[1:] if not a.startswith('--')]
W = os.path.abspath(ARGS[0] if ARGS else '.')
FFMPEG = None if '--no-video' in sys.argv else shutil.which('ffmpeg')
SECS = 60
OUT = os.path.join(W, 'out'); os.makedirs(OUT, exist_ok=True)
CHROME = next((p for p in ('/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser') if os.path.exists(p)), None)

async def main():
    async with async_playwright() as p:
        kw = {'executable_path': CHROME} if CHROME else {}
        b = await p.chromium.launch(**kw)
        pg = await b.new_page(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1)
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        async def only_local(route):
            if route.request.url.startswith('file://'): await route.continue_()
            else: await route.abort()
        await pg.route('**/*', only_local)
        theme, lights = '', {}
        for n in (1, 2, 3):
            await pg.goto(f'file://{W}/index.html#foto-{n}')
            await pg.wait_for_timeout(1500)
            await pg.screenshot(path=os.path.join(OUT, f'screen-{n}.jpg'), type='jpeg', quality=88)
            try:
                info = await pg.evaluate('''() => ({theme: document.documentElement.getAttribute('data-theme') || '',
                    lights: Array.from(document.querySelectorAll('.xg .xb')).map(e => { const r = e.getBoundingClientRect();
                        return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2), e.getAttribute('data-c') || '#FFFFFF', +(e.getAttribute('data-g') || 0)]; })
                        .filter(v => v[0] >= 0 && v[0] <= 1920 && v[1] >= 0 && v[1] <= 1080)})''')
                theme = theme or info.get('theme') or ''
                if info.get('lights'): lights[str(n)] = info['lights']
            except Exception as e:
                errs.append('lights: ' + str(e))
        videos = {}
        if FFMPEG:
            for n in (1, 2, 3):
                try:
                    t = time.time()
                    if await record(b, n, only_local): videos[str(n)] = f'screen-{n}.mp4'
                    print(f'video {n}: {"ok" if str(n) in videos else "failed"} in {time.time() - t:.0f}s', flush=True)
                except Exception as e:
                    errs.append(f'video {n}: {e}')
        await b.close()
    gen = None
    try:
        s = open(os.path.join(W, 'data.js'), encoding='utf-8').read()
        gen = json.loads(s[s.index('{'):s.rindex('}') + 1]).get('generatedAt')
    except Exception:
        pass
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    with open(os.path.join(OUT, 'status.json'), 'w') as f:
        json.dump({'generatedAt': gen, 'renderedAt': now, 'theme': theme, 'lights': lights if theme else {}, 'video': videos}, f, separators=(',', ':'))
    sizes = [os.path.getsize(os.path.join(OUT, f'screen-{n}.jpg')) // 1024 for n in (1, 2, 3)]
    vs = [os.path.getsize(os.path.join(OUT, v)) // 1024 for v in videos.values()]
    print(f"OK pictures {sizes} KB · videos {vs} KB · page errors {len(errs)}" + (f" · {errs[0][:120]}" if errs else ''))

async def record(b, n, only_local):
    """Record #video-n in real time for SECS seconds (Chrome screencast) and encode a seamless one-minute H.264 loop."""
    fdir = os.path.join(OUT, f'frames-{n}'); shutil.rmtree(fdir, ignore_errors=True); os.makedirs(fdir)
    pg = await b.new_page(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1)
    await pg.route('**/*', only_local)
    cdp = await pg.context.new_cdp_session(pg); frames = []
    def on_frame(ev):
        path = os.path.join(fdir, f'{len(frames):05d}.jpg')
        with open(path, 'wb') as fh: fh.write(base64.b64decode(ev['data']))
        frames.append((ev['metadata']['timestamp'], path))
        asyncio.ensure_future(ack(ev['sessionId']))
    async def ack(sid):
        try: await cdp.send('Page.screencastFrameAck', {'sessionId': sid})
        except Exception: pass   # the page may already be closed
    cdp.on('Page.screencastFrame', on_frame)
    await pg.goto('about:blank')
    await cdp.send('Page.startScreencast', {'format': 'jpeg', 'quality': 88, 'maxWidth': 1920, 'maxHeight': 1080, 'everyNthFrame': 1})
    await pg.goto(f'file://{W}/index.html#video-{n}')
    await pg.wait_for_timeout(SECS * 1000 + 1500)
    await cdp.send('Page.stopScreencast'); await pg.close()
    start = next((i for i, (_, pth) in enumerate(frames) if os.path.getsize(pth) > 60000), None)
    if start is None: return False
    t0 = frames[start][0]; sel = [(t - t0, pth) for t, pth in frames[start:] if t - t0 < SECS]
    if len(sel) < SECS * 10: return False
    lst = os.path.join(fdir, 'list.txt')
    with open(lst, 'w') as fh:
        for i, (t, pth) in enumerate(sel):
            nxt = sel[i + 1][0] if i + 1 < len(sel) else SECS
            fh.write(f"file '{pth}'\nduration {max(0.001, nxt - t):.4f}\n")
        fh.write(f"file '{sel[-1][1]}'\n")
    out = os.path.join(OUT, f'screen-{n}.mp4')
    r = subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-vf', 'fps=30,format=yuv420p', '-t', str(SECS), '-an',
                        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '27', '-profile:v', 'high', '-level', '4.1', '-g', '300', '-movflags', '+faststart', out])
    shutil.rmtree(fdir, ignore_errors=True)
    return r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 100000

asyncio.run(main())
