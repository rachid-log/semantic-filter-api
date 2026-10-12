"""Build the project images (Intropera card cover + LinkedIn media) from HTML with headless Chrome.

    python3 media/build_media.py            # writes .intropera/cover.png and media/linkedin/*.png

Needs Google Chrome (or Chromium) and an internet connection for Google Fonts.
Every number on the images is real: measured with BAAI/bge-small-en-v1.5 through POST /embed.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(c)), None)

FONTS = (
    "https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700"
    "&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Mono:wght@400;500&display=block"
)

REPO = "github.com/rachid-log/semantic-filter-api"

# The first 40 of the 384 numbers, real output of POST /embed (rounded to 3 decimals).
# "seo tools" and "best seo tools" have nearly the same shape; "how to bake bread" does not.
VEC = {
    "seo tools": [-0.041, -0.056, 0.018, 0.001, 0.023, 0.023, -0.03, 0.01, 0.03, 0.012, 0.022, -0.008, 0.047,
                  0.002, 0.035, 0.006, 0.038, 0.076, -0.051, -0.041, 0.078, 0.041, 0.028, -0.031, -0.008, 0.005,
                  -0.049, -0.007, -0.053, -0.086, 0.012, -0.022, 0.054, 0.021, -0.009, 0.018, 0.004, 0.012,
                  -0.009, -0.006],
    "best seo tools": [-0.027, -0.058, 0.001, -0.013, 0.03, 0.021, -0.024, 0.029, 0.006, 0.003, 0.011, 0.003,
                       0.035, 0.034, 0.041, 0.018, 0.049, 0.092, -0.028, -0.038, 0.062, 0.05, -0.001, -0.03,
                       -0.019, 0.004, -0.058, -0.02, -0.048, -0.079, -0.003, -0.021, 0.039, 0.031, -0.022, 0.021,
                       0.01, -0.007, 0.0, -0.023],
    "how to bake bread": [-0.042, 0.045, 0.035, -0.077, -0.052, 0.036, -0.04, -0.013, -0.01, -0.008, -0.081,
                          -0.085, 0.001, -0.007, 0.026, -0.025, -0.006, 0.058, -0.108, 0.007, 0.108, -0.008,
                          -0.043, -0.04, 0.043, 0.027, 0.023, -0.001, -0.005, -0.163, 0.02, -0.002, 0.056, -0.063,
                          -0.027, -0.023, 0.043, 0.043, -0.057, -0.013],
}

# Real cosine scores against "seo tools" (dot product of the full 384-number vectors).
SCORES = [
    ("best seo tools", 0.954),
    ("keyword research tool", 0.777),
    ("how to bake bread", 0.449),
]
THRESHOLD = 0.90

# Line icons (24x24, stroke = currentColor).
ICON = {
    "list": '<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4.5" cy="6" r="1.2"/><circle cx="4.5" cy="12" r="1.2"/>'
            '<circle cx="4.5" cy="18" r="1.2"/>',
    "chip": '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "angle": '<path d="M4 20L20 7M4 20h16"/><path d="M11 20a7 7 0 0 0-1.6-4.5"/>',
    "filter": '<path d="M3 5h18l-7 8.2V19l-4 2v-7.8L3 5z"/>',
    "code": '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
}


def icon(name, size=40):
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{ICON[name]}</svg>'
    )


def vector(name, h=44, bar=7, gap=4, scale=0.055):
    """A vector as bars around a midline: up = positive, down = negative."""
    half = h / 2
    bars = []
    for n, v in enumerate(VEC[name]):
        size = max(2, min(half, abs(v) / scale * half))
        top = half - size if v >= 0 else half
        cls = "up" if v >= 0 else "dn"
        bars.append(f'<i class="{cls}" style="left:{n * (bar + gap)}px;top:{top:.1f}px;height:{size:.1f}px"></i>')
    width = len(VEC[name]) * (bar + gap)
    return (f'<div class="vec" style="height:{h}px;width:{width}px;--bar:{bar}px;--gap:{gap}px">'
            f'<b></b>{"".join(bars)}</div>')


def score_row(label, score, size=26):
    dup = score >= THRESHOLD
    badge = ('<span class="badge dup">duplicate · skip</span>' if dup
             else '<span class="badge keep">new · keep</span>')
    return f"""
<div class="row">
  <div class="row-top"><span class="mono" style="font-size:{size}px">{label}</span>
    <span class="score{' hi' if dup else ''}" style="font-size:{size + 4}px">{score:.3f}</span></div>
  <div class="row-bot"><div class="track"><div class="fill{' hi' if dup else ''}" style="width:{score * 100:.1f}%"></div>
    <div class="mark" style="left:{THRESHOLD * 100:.0f}%"></div></div>{badge}</div>
</div>"""


CSS = """
:root{--night:#0B0C11;--ink:#0E1016;--on:#EEF0F5;--muted:#9CA1B3;--acc:#8A7DF5;--pri:#6050DC;--lav:#B3A9FF;
--glass:rgba(255,255,255,.055);--edge:rgba(255,255,255,.13)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--night)}
body{width:VARW;height:VARH;overflow:hidden;zoom:VARZ}
.canvas{position:relative;width:DESW;height:DESH;overflow:hidden;color:var(--on);
font-family:'IBM Plex Sans',sans-serif;background:
radial-gradient(900px 700px at 85% 15%,rgba(96,80,220,.38),transparent 60%),
radial-gradient(800px 600px at 5% 95%,rgba(138,125,245,.22),transparent 60%),
linear-gradient(160deg,#0E1016,#0B0C11 60%)}
.grid{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),
linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:64px 64px;
mask-image:radial-gradient(ellipse at 60% 40%,#000 30%,transparent 75%)}
.glass{background:var(--glass);border:1px solid var(--edge);border-radius:28px;backdrop-filter:blur(24px);
box-shadow:inset 0 1px 0 rgba(255,255,255,.12),0 30px 80px rgba(0,0,0,.45)}
.label{font-family:'IBM Plex Mono',monospace;font-size:20px;letter-spacing:.14em;text-transform:uppercase;color:var(--acc)}
h1{font-family:Sora,sans-serif;font-weight:600;letter-spacing:-.02em;line-height:1.02}
.muted{color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:12px}
.chip{font-family:'IBM Plex Mono',monospace;font-size:20px;padding:10px 18px;border-radius:999px;
border:1px solid rgba(138,125,245,.45);background:rgba(96,80,220,.14);color:var(--on);white-space:nowrap}
.chip.kw{font-size:22px;background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.2)}
.mono{font-family:'IBM Plex Mono',monospace}
.ico{width:72px;height:72px;border-radius:20px;display:grid;place-items:center;color:#fff;flex:none;
background:linear-gradient(145deg,rgba(138,125,245,.55),rgba(96,80,220,.25));border:1px solid rgba(255,255,255,.18)}
.arrow{width:2px;height:30px;margin:5px 0 5px 64px;background:linear-gradient(var(--acc),transparent)}
.stat b{display:block;font-family:Sora,sans-serif;font-size:40px;font-weight:600}
.stat span{font-size:20px;color:var(--muted)}
.vec{position:relative;flex:none}
.vec b{position:absolute;left:0;right:0;top:50%;height:1px;background:rgba(255,255,255,.18)}
.vec i{position:absolute;width:var(--bar);border-radius:3px}
.vec i.up{background:linear-gradient(180deg,#B3A9FF,#8A7DF5)}
.vec i.dn{background:linear-gradient(180deg,#6050DC,rgba(96,80,220,.55))}
.vecrow{display:flex;align-items:center;gap:22px}
.vecrow .mono{width:230px;font-size:21px;color:var(--on);flex:none}
.row{margin-top:14px}
.row-top{display:flex;justify-content:space-between;align-items:baseline}
.row-bot{display:flex;align-items:center;gap:16px;margin-top:9px}
.score{font-family:Sora,sans-serif;font-weight:600;color:var(--muted)}
.score.hi{color:var(--lav)}
.track{position:relative;flex:1;height:10px;border-radius:99px;background:rgba(255,255,255,.08)}
.fill{position:absolute;left:0;top:0;bottom:0;border-radius:99px;background:rgba(156,161,179,.55)}
.fill.hi{background:linear-gradient(90deg,#6050DC,#B3A9FF)}
.mark{position:absolute;top:-6px;bottom:-6px;width:2px;background:rgba(255,255,255,.55)}
.badge{font-family:'IBM Plex Mono',monospace;font-size:17px;letter-spacing:.06em;text-transform:uppercase;
padding:6px 12px;border-radius:999px;white-space:nowrap;flex:none;width:230px;text-align:center}
.badge.dup{color:#fff;background:rgba(96,80,220,.55);border:1px solid rgba(179,169,255,.6)}
.badge.keep{color:var(--muted);border:1px solid rgba(255,255,255,.18)}
.foot{position:absolute;left:0;right:0;bottom:44px;display:flex;justify-content:space-between;padding:0 110px;
font-family:'IBM Plex Mono',monospace;font-size:22px;color:var(--muted)}
.wm{font-family:Sora,sans-serif;font-weight:600;color:var(--on);letter-spacing:-.018em}
.wm i{font-style:normal;color:var(--acc)}
"""


def page(body, des_w, des_h, out_w, out_h):
    zoom = out_w / des_w
    css = (
        CSS.replace("VARW", f"{des_w}px").replace("VARH", f"{des_h}px").replace("VARZ", f"{zoom}")
        .replace("DESW", f"{des_w}px").replace("DESH", f"{des_h}px")
    )
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{FONTS}">'
        f"<style>{css}</style></head><body><div class=\"canvas\"><div class=\"grid\"></div>{body}</div></body></html>"
    )


def pipeline_card(width):
    kws = "".join(f'<span class="chip kw">{k}</span>' for k in ("seo tools", "best seo tools", "how to bake bread"))
    vecs = "".join(
        f'<div class="vecrow" style="margin-top:10px"><span class="mono">{k}</span>{vector(k, 46, 8, 3)}</div>'
        for k in ("seo tools", "best seo tools", "how to bake bread")
    )
    rows = score_row("best seo tools", 0.954, 23) + score_row("how to bake bread", 0.449, 23)
    return f"""
<div style="width:{width}px">
  <div class="glass" style="padding:24px 32px">
    <div class="label">1 · Keywords in</div>
    <div class="chips" style="margin-top:14px">{kws}</div>
  </div>
  <div class="arrow"></div>
  <div class="glass" style="padding:24px 32px">
    <div class="label">2 · POST /embed → 384 numbers each</div>
    {vecs}
  </div>
  <div class="arrow"></div>
  <div class="glass" style="padding:24px 32px 26px">
    <div class="label">3 · Dot product with "seo tools"</div>
    {rows}
  </div>
</div>"""


def cover():
    """Landscape: the Intropera card (16:9) and the LinkedIn cover (1.91:1) share this layout."""
    return f"""
<div style="position:absolute;inset:0 0 70px 0;display:flex;align-items:center;gap:72px;padding:0 110px">
  <div style="flex:1">
    <div class="label">Open source · Apache-2.0</div>
    <h1 style="font-size:112px;margin:24px 0 28px">Semantic<br>Filter API</h1>
    <p class="muted" style="font-size:34px;line-height:1.4;max-width:800px">
      Catch near-duplicate keywords before you pay for a scraper run. Self-hosted embeddings, on CPU.</p>
    <div class="chips" style="margin:34px 0 40px">
      <span class="chip">bge-small-en-v1.5</span><span class="chip">ONNX · no GPU</span>
      <span class="chip">FastAPI</span><span class="chip">n8n</span></div>
    <div style="display:flex;gap:20px">
      <div class="glass stat" style="padding:22px 26px"><b>0.954</b><span>“seo tools” vs<br>“best seo tools”</span></div>
      <div class="glass stat" style="padding:22px 26px"><b>66.5 MB</b><span>model, baked<br>into the image</span></div>
      <div class="glass stat" style="padding:22px 26px"><b>$0</b><span>per call,<br>on your server</span></div>
    </div>
  </div>
  {pipeline_card(800)}
</div>
<div class="foot"><span>{REPO}</span><span class="wm">intropera<i>.</i></span></div>"""


def how_it_works():
    steps = [
        ("list", "Collect keywords", "From n8n, a sheet or a script. Many mean the same thing.",
         '"seo tools", "best seo tools", …'),
        ("chip", "Embed them on your CPU", "384 numbers per keyword, in one call. No GPU.",
         "POST /embed · 256 keywords ≈ 0.7 s"),
        ("angle", "Compare with a dot product", "Vectors have length 1, so dot product = cosine.",
         "seo tools · best seo tools → 0.954"),
        ("filter", "Skip the duplicates", "0.90 or more: same search. Don't pay twice.",
         'duplicateOf: "seo tools"'),
    ]
    rows = "".join(
        f"""<div class="glass" style="padding:24px 34px;display:flex;gap:28px;align-items:flex-start;margin-bottom:16px">
  <div class="ico">{icon(ic)}</div>
  <div style="flex:1"><div class="label">{i + 1:02d}</div>
    <div style="font-family:Sora;font-weight:600;font-size:38px;margin:6px 0 8px">{t}</div>
    <div class="muted" style="font-size:24px;line-height:1.4">{d}</div>
    <div class="mono" style="font-size:22px;color:var(--acc);margin-top:12px">{code}</div></div>
</div>"""
        for i, (ic, t, d, code) in enumerate(steps)
    )
    return f"""
<div style="position:absolute;inset:0;padding:80px 80px">
  <div class="label">How it works</div>
  <h1 style="font-size:64px;margin:18px 0 34px">Same search, other words.<br>Pay for it once.</h1>
  {rows}
</div>
<div class="foot" style="padding:0 80px"><span>Semantic Filter API · Apache-2.0</span><span class="wm">intropera<i>.</i></span></div>"""


def api_call():
    curl = (
        'curl -X POST "$URL/embed" \\\n'
        '  -H "Authorization: Bearer $TOKEN" \\\n'
        "  -d '{\"texts\": [\n"
        '    "seo tools",\n'
        '    "best seo tools",\n'
        '    "keyword research tool",\n'
        '    "how to bake bread"\n'
        "  ]}'"
    )
    rows = "".join(score_row(f"seo tools · {k}", s, 23) for k, s in SCORES)
    legend = (
        '<span class="chip">≥ 0.90 duplicate</span><span class="chip">0.85–0.90 your call</span>'
        '<span class="chip">&lt; 0.60 different</span>'
    )
    return f"""
<div style="position:absolute;inset:0;padding:84px 80px">
  <div class="label">One call</div>
  <h1 style="font-size:76px;margin:20px 0 36px">Keywords in.<br>Duplicates out.</h1>
  <div class="glass" style="padding:30px 38px">
    <div style="display:flex;gap:10px;margin-bottom:20px"><i style="width:14px;height:14px;border-radius:50%;background:#ff5f57"></i>
      <i style="width:14px;height:14px;border-radius:50%;background:#febc2e"></i><i style="width:14px;height:14px;border-radius:50%;background:#28c840"></i></div>
    <pre class="mono" style="font-size:25px;line-height:1.55;white-space:pre-wrap;color:#D9DBF5">{curl}</pre>
  </div>
  <div class="glass" style="padding:26px 38px 30px;margin-top:22px">
    <div class="mono" style="font-size:24px;color:#7EE0A1">HTTP 200 · 4 vectors × 384 numbers</div>
    {rows}
  </div>
  <div class="chips" style="margin-top:26px">{legend}</div>
</div>
<div class="foot" style="padding:0 80px"><span>{REPO}</span><span class="wm">intropera<i>.</i></span></div>"""


# (output path, html, design size, output size)
JOBS = [
    (".intropera/cover.png", cover, (1920, 1080), (1920, 1080)),
    ("media/linkedin/01-cover-1200x627.png", cover, (1920, 1003), (1200, 627)),
    ("media/linkedin/02-how-it-works-1080x1350.png", how_it_works, (1080, 1350), (1080, 1350)),
    ("media/linkedin/03-api-call-1080x1350.png", api_call, (1080, 1350), (1080, 1350)),
]


def main():
    if not CHROME:
        sys.exit("Google Chrome or Chromium is needed")
    only = sys.argv[1:]  # optional: output paths to build, for quick iterations
    with tempfile.TemporaryDirectory() as tmp:
        for out, body, (dw, dh), (ow, oh) in JOBS:
            if only and out not in only:
                continue
            html = Path(tmp) / (Path(out).stem + ".html")
            html.write_text(page(body(), dw, dh, ow, oh), encoding="utf-8")
            target = ROOT / out
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                 f"--window-size={ow},{oh}", "--virtual-time-budget=15000", f"--screenshot={target}", html.as_uri()],
                check=True, capture_output=True,
            )
            print("wrote", out)


if __name__ == "__main__":
    main()
