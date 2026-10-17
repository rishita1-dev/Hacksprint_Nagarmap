"""Arcade-cabinet look and feel for NagarMap.

Pure presentation: CSS plus small HTML builders. No geospatial logic lives here.
Fonts use local fallbacks only (no external requests), so nothing breaks offline.
All dynamic text is HTML-escaped.
"""
from __future__ import annotations

import html

PALETTE = {
    "bg": "#080A12", "panel": "#111426", "cyan": "#00F5FF", "magenta": "#FF2BD6",
    "green": "#B6FF3B", "amber": "#FFB000", "red": "#FF4057", "text": "#F2F4FF",
    "muted": "#939BB8", "line": "#2A2F5A", "blue": "#3D7BFF",
}

STATUS_TONE = {"PASS": "green", "REVIEW REQUIRED": "amber", "WARNING": "amber", "FAILED": "red", "NOT RUN": "muted", "UNAVAILABLE": "muted"}


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


CSS = """
<style>
:root{
  --bg:#080A12; --panel:#111426; --panel2:#0C0F1D; --cyan:#00F5FF; --mag:#FF2BD6; --green:#B6FF3B;
  --amber:#FFB000; --red:#FF4057; --text:#F2F4FF; --muted:#939BB8; --line:#2A2F5A; --blue:#3D7BFF;
  --f-display:"Orbitron","Bahnschrift","Rajdhani","Arial Narrow","Segoe UI",sans-serif;
  --f-mono:"JetBrains Mono","Cascadia Mono","SFMono-Regular",Consolas,"Courier New",monospace;
  --f-body:"Inter","Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;
}
.stApp{background:var(--bg);
  background-image:linear-gradient(rgba(0,245,255,.035) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(0,245,255,.035) 1px,transparent 1px);
  background-size:32px 32px; font-family:var(--f-body); color:var(--text);}
[data-testid="stHeader"]{background:transparent;}
[data-testid="stMainBlockContainer"]{max-width:1500px; padding:3rem 1.4rem 2rem 1.4rem; margin-top:.4rem;
  background:linear-gradient(180deg,#0B0E1C,#090B16); border:1px solid var(--line); border-radius:18px;
  box-shadow:0 0 0 5px #0B0D19,0 0 0 6px #1B2050,0 0 46px rgba(0,245,255,.08),inset 0 0 60px rgba(0,0,0,.45);}
[data-testid="stSidebar"]{background:#0A0C19; border-right:1px solid var(--line);}
[data-testid="stSidebar"] [data-testid="stSidebarContent"]{padding-top:.5rem;}
h1,h2,h3,h4{font-family:var(--f-display); letter-spacing:.06em; text-transform:uppercase;}
p,li,label,span{font-family:var(--f-body);}
code,pre,kbd{font-family:var(--f-mono)!important;}
:focus-visible{outline:2px solid var(--cyan)!important; outline-offset:2px;}

/* ---------- bezel header ---------- */
.nm-bezel{position:relative; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:.8rem;
  padding:.8rem 1.2rem; background:linear-gradient(180deg,#151935,#0E1122); border:1px solid #343B7A; border-radius:12px;
  box-shadow:0 0 0 1px #05060C,0 8px 24px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.06); overflow:hidden;}
.nm-bezel::after{content:""; position:absolute; inset:0; pointer-events:none; opacity:.35;
  background:repeating-linear-gradient(0deg,rgba(0,0,0,.35) 0,rgba(0,0,0,.35) 1px,transparent 1px,transparent 3px);}
.nm-lights{display:flex; gap:.45rem; align-items:center; margin-right:.2rem;}
.nm-lights i{width:11px; height:11px; border-radius:50%; background:#232846; border:1px solid #3A4180; display:inline-block;}
.nm-lights i.green{background:var(--green); box-shadow:0 0 8px var(--green);}
.nm-lights i.amber{background:var(--amber); box-shadow:0 0 8px var(--amber);}
.nm-lights i.red{background:var(--red); box-shadow:0 0 8px var(--red);}
.nm-title{display:flex; flex-direction:column; gap:.1rem; position:relative; z-index:1;}
.nm-wordmark{font-family:var(--f-display); font-weight:900; font-size:2rem; letter-spacing:.18em; line-height:1; color:var(--text);
  text-shadow:0 0 6px rgba(0,245,255,.8),2px 0 0 rgba(255,43,214,.55);}
.nm-sub{font-family:var(--f-display); font-size:.78rem; letter-spacing:.28em; color:var(--cyan);}
.nm-div{font-family:var(--f-mono); font-size:.68rem; letter-spacing:.16em; color:var(--muted);}
.nm-chips{display:flex; flex-wrap:wrap; gap:.4rem; justify-content:flex-end; position:relative; z-index:1;}
.nm-chip{font-family:var(--f-mono); font-size:.7rem; letter-spacing:.06em; padding:.22rem .6rem; border:1px solid var(--line);
  background:var(--panel2); border-radius:3px; color:var(--muted); white-space:nowrap;}
.nm-chip b{color:var(--text); font-weight:600;}
.nm-chip.green{border-color:rgba(182,255,59,.55); color:var(--green);}
.nm-chip.amber{border-color:rgba(255,176,0,.6); color:var(--amber);}
.nm-chip.red{border-color:rgba(255,64,87,.65); color:var(--red);}
.nm-chip.mag{border-color:rgba(255,43,214,.55); color:var(--mag);}
.nm-chip.cyan{border-color:rgba(0,245,255,.5); color:var(--cyan);}
.nm-synth{margin:.55rem 0 .2rem; padding:.4rem .8rem; border-left:3px solid var(--amber); background:rgba(255,176,0,.06);
  color:#C9CFE8; font-size:.78rem; border-radius:3px;}

/* ---------- HUD modules ---------- */
.nm-hud{display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); gap:.6rem; margin:.7rem 0 .5rem;}
.nm-mod{position:relative; background:var(--panel); border:1px solid var(--line); border-top:2px solid var(--cyan);
  border-radius:6px; padding:.5rem .75rem .55rem; overflow:hidden; animation:nm-up .35s ease both;}
.nm-mod::after{content:""; position:absolute; inset:0; pointer-events:none; opacity:.25;
  background:repeating-linear-gradient(0deg,rgba(0,0,0,.4) 0,rgba(0,0,0,.4) 1px,transparent 1px,transparent 3px);}
.nm-mod .lab{font-family:var(--f-display); font-size:.62rem; letter-spacing:.18em; color:var(--muted);}
.nm-mod .val{font-family:var(--f-mono); font-size:1.75rem; font-weight:700; line-height:1.15; color:var(--cyan);
  text-shadow:0 0 8px rgba(0,245,255,.35);}
.nm-mod .sub{font-family:var(--f-mono); font-size:.64rem; color:var(--muted); border-top:1px solid var(--line); margin-top:.25rem; padding-top:.2rem;}
.nm-mod.red{border-top-color:var(--red);} .nm-mod.red .val{color:var(--red); text-shadow:0 0 8px rgba(255,64,87,.4);}
.nm-mod.amber{border-top-color:var(--amber);} .nm-mod.amber .val{color:var(--amber); text-shadow:none;}
.nm-mod.green{border-top-color:var(--green);} .nm-mod.green .val{color:var(--green); text-shadow:none;}
.nm-mod.mag{border-top-color:var(--mag);} .nm-mod.mag .val{color:var(--mag); text-shadow:none;}
.nm-mod.muted .val{color:#5F6788; text-shadow:none;}
.nm-bar{height:5px; background:#1B1F3A; border-radius:3px; margin-top:.3rem; overflow:hidden;}
.nm-bar i{display:block; height:100%; background:linear-gradient(90deg,var(--cyan),var(--mag)); transition:width .4s ease;}

/* ---------- navigation (scoped to key=nav) ---------- */
.st-key-nav [role="radiogroup"]{display:flex; flex-wrap:wrap; gap:.4rem;}
.st-key-nav [role="radiogroup"] > label{background:var(--panel); border:1px solid var(--line); border-radius:4px;
  padding:.3rem .85rem; margin:0; cursor:pointer; transition:border-color .15s, box-shadow .15s, background .15s;}
.st-key-nav [role="radiogroup"] > label > div:first-child{display:none;}
.st-key-nav [role="radiogroup"] > label p{font-family:var(--f-display); font-size:.74rem; letter-spacing:.14em; color:var(--muted); margin:0;}
.st-key-nav [role="radiogroup"] > label:hover{border-color:var(--cyan); box-shadow:0 0 10px rgba(0,245,255,.25);}
.st-key-nav [role="radiogroup"] > label:has(input:checked){border-color:var(--cyan); background:#0B2330;
  box-shadow:0 0 12px rgba(0,245,255,.4),inset 0 0 8px rgba(0,245,255,.15);}
.st-key-nav [role="radiogroup"] > label:has(input:checked) p{color:var(--cyan);}

/* ---------- buttons ---------- */
.stButton>button,.stDownloadButton>button{font-family:var(--f-display); letter-spacing:.12em; text-transform:uppercase; font-size:.78rem;
  background:var(--panel); color:var(--text); border:1px solid var(--line); border-radius:4px; transition:all .15s ease;}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--cyan); color:var(--cyan); box-shadow:0 0 12px rgba(0,245,255,.3);}
.stButton>button:active,.stDownloadButton>button:active{transform:translateY(1px) scale(.99); box-shadow:inset 0 0 10px rgba(0,245,255,.3);}
.stButton>button:disabled,.stDownloadButton>button:disabled{opacity:.5; box-shadow:none; color:var(--green); border-color:rgba(182,255,59,.35);}
.st-key-scan_side button,.st-key-scan_main button{background:linear-gradient(180deg,#00F5FF,#00B7C4)!important; color:#021016!important;
  border:2px solid #7DFBFF!important; font-weight:900; font-size:.95rem; padding:.55rem 1rem; letter-spacing:.2em;
  box-shadow:0 0 10px rgba(0,245,255,.55),0 4px 0 #006E76; animation:nm-pulse 2.2s ease-in-out infinite;}
.st-key-scan_side button:hover,.st-key-scan_main button:hover{color:#021016!important; box-shadow:0 0 22px rgba(0,245,255,.9),0 4px 0 #006E76;}
.st-key-scan_side button:active,.st-key-scan_main button:active{transform:translateY(3px); box-shadow:0 0 10px rgba(0,245,255,.7),0 1px 0 #006E76;}
.st-key-scan_side button:disabled,.st-key-scan_main button:disabled{background:#131830!important; color:var(--green)!important;
  border:2px solid rgba(182,255,59,.4)!important; box-shadow:none; animation:none; opacity:.85;}
.st-key-export button,.st-key-export_c button{border:2px solid var(--green); color:var(--green); font-weight:800;}
.st-key-export button:hover,.st-key-export_c button:hover{box-shadow:0 0 16px rgba(182,255,59,.5); color:var(--green); border-color:var(--green);}
.st-key-start button{background:linear-gradient(180deg,#FF2BD6,#B81C9B)!important; color:#fff!important; border:2px solid #FF8BEA!important;
  font-weight:900; letter-spacing:.25em; padding:.7rem 1.2rem; animation:nm-pulse-m 1.6s ease-in-out infinite;}
.nm-hint{font-family:var(--f-mono); font-size:.66rem; color:var(--muted); text-align:center; margin-top:-.3rem; letter-spacing:.1em;}

/* ---------- sidebar ---------- */
.nm-side-title{font-family:var(--f-display); font-size:.8rem; letter-spacing:.24em; color:var(--cyan); padding:.2rem 0 .4rem;
  border-bottom:1px solid var(--line); margin-bottom:.5rem;}
.nm-side-label{font-family:var(--f-display); font-size:.62rem; letter-spacing:.2em; color:var(--muted); margin:.6rem 0 .1rem;}
.st-key-missionbox [role="radiogroup"] > label{background:var(--panel); border:1px solid var(--line); border-radius:4px; padding:.35rem .6rem;
  margin-bottom:.3rem; width:100%; transition:border-color .15s, box-shadow .15s;}
.st-key-missionbox [role="radiogroup"] > label:hover{border-color:var(--cyan);}
.st-key-missionbox [role="radiogroup"] > label:has(input:checked){border-color:var(--mag); box-shadow:0 0 10px rgba(255,43,214,.4);}
.st-key-missionbox [role="radiogroup"] p{font-family:var(--f-mono); font-size:.74rem; letter-spacing:.03em;}

/* ---------- panels ---------- */
.nm-panel{background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:.8rem 1rem; margin-bottom:.7rem; animation:nm-up .35s ease both;}
.nm-panel h4{margin:0 0 .5rem; font-size:.82rem; letter-spacing:.2em; color:var(--cyan);}
.nm-panel p,.nm-panel li{font-size:.85rem; color:#D5D9EE;}
.nm-kv{display:grid; grid-template-columns:auto 1fr; gap:.15rem .8rem; font-family:var(--f-mono); font-size:.74rem;}
.nm-kv .k{color:var(--muted); letter-spacing:.06em;} .nm-kv .v{color:var(--text); word-break:break-word;}
.nm-note{font-size:.72rem; color:var(--muted); margin-top:.5rem; border-top:1px dashed var(--line); padding-top:.4rem;}
.st-key-mapframe{border:1px solid #3A4180; border-radius:10px; padding:.5rem; background:#06080F;
  box-shadow:0 0 18px rgba(0,245,255,.12),inset 0 0 0 1px #05060C;}
.nm-ticker{display:flex; flex-wrap:wrap; gap:1.1rem; font-family:var(--f-mono); font-size:.68rem; color:var(--muted); padding:.1rem .3rem .4rem;}
.nm-ticker b{color:var(--cyan); font-weight:600;}

/* ---------- mission cards ---------- */
.nm-mcard{background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:.8rem 1rem; height:100%; transition:border-color .2s, box-shadow .2s;}
.nm-mcard.sel{border-color:var(--mag); box-shadow:0 0 16px rgba(255,43,214,.35),inset 0 0 12px rgba(255,43,214,.08);}
.nm-mcard .code{font-family:var(--f-mono); font-size:.68rem; letter-spacing:.14em; color:var(--mag);}
.nm-mcard .name{font-family:var(--f-display); font-size:1rem; letter-spacing:.1em; margin:.15rem 0 .35rem;}
.nm-mcard .obj{font-size:.8rem; color:#C3C9E3; min-height:2.6rem;}
.nm-mcard .meta{font-family:var(--f-mono); font-size:.68rem; color:var(--muted); margin-top:.4rem;}
.nm-mcard .state{font-family:var(--f-display); font-size:.62rem; letter-spacing:.2em; margin-top:.35rem; color:var(--muted);}
.nm-mcard.sel .state{color:var(--mag);}

/* ---------- badges / tables ---------- */
.nm-badge{display:inline-block; font-family:var(--f-mono); font-size:.66rem; letter-spacing:.08em; padding:.08rem .5rem; border-radius:3px; border:1px solid;}
.nm-badge.green{color:var(--green); border-color:rgba(182,255,59,.5); background:rgba(182,255,59,.08);}
.nm-badge.amber{color:var(--amber); border-color:rgba(255,176,0,.55); background:rgba(255,176,0,.08);}
.nm-badge.red{color:var(--red); border-color:rgba(255,64,87,.6); background:rgba(255,64,87,.1);}
.nm-badge.muted{color:var(--muted); border-color:var(--line); background:rgba(147,155,184,.06);}
.nm-badge.cyan{color:var(--cyan); border-color:rgba(0,245,255,.5); background:rgba(0,245,255,.07);}
table.nm-diag{width:100%; border-collapse:collapse; font-size:.78rem;}
table.nm-diag th{font-family:var(--f-display); font-size:.62rem; letter-spacing:.16em; color:var(--muted); text-align:left; padding:.3rem .5rem; border-bottom:1px solid var(--line);}
table.nm-diag td{padding:.3rem .5rem; border-bottom:1px solid rgba(42,47,90,.5); vertical-align:top; color:#D5D9EE;}
table.nm-diag td.mono{font-family:var(--f-mono); font-size:.72rem; color:var(--text);}
table.nm-diag tr:hover td{background:rgba(0,245,255,.05);}
pre.nm-log{background:#05070D; border:1px solid var(--line); border-radius:6px; padding:.6rem .8rem; font-size:.7rem; line-height:1.55;
  color:#B8C0E0; max-height:260px; overflow:auto; white-space:pre-wrap;}
pre.nm-log .ok{color:var(--green);} pre.nm-log .wa{color:var(--amber);} pre.nm-log .er{color:var(--red);} pre.nm-log .in{color:var(--cyan);}

/* ---------- debrief ---------- */
.nm-debrief{border:1px solid rgba(182,255,59,.45); background:linear-gradient(180deg,rgba(182,255,59,.07),rgba(17,20,38,.9));
  border-radius:10px; padding:.9rem 1.1rem; margin-bottom:.8rem; animation:nm-up .4s ease both;}
.nm-debrief .big{font-family:var(--f-display); font-weight:900; font-size:1.5rem; letter-spacing:.2em; color:var(--green); text-shadow:0 0 10px rgba(182,255,59,.4);}
.nm-debrief .small{font-family:var(--f-mono); font-size:.72rem; letter-spacing:.16em; color:var(--muted);}
.nm-banner{display:flex; gap:.8rem; align-items:center; flex-wrap:wrap; padding:.5rem .9rem; border-radius:6px; margin-top:.5rem;
  border:1px solid rgba(182,255,59,.45); background:rgba(182,255,59,.06); font-family:var(--f-mono); font-size:.76rem; animation:nm-up .35s ease both;}
.nm-banner.amber{border-color:rgba(255,176,0,.55); background:rgba(255,176,0,.06);}
.nm-lock{border:1px solid var(--cyan); box-shadow:0 0 14px rgba(0,245,255,.25); background:linear-gradient(180deg,#0B1B26,#0E1122);}
.nm-lock .hd{font-family:var(--f-display); font-weight:900; letter-spacing:.2em; color:var(--cyan); font-size:.9rem;}
.nm-lock .hd.red{color:var(--red);} .nm-lock .hd.amber{color:var(--amber);} .nm-lock .hd.green{color:var(--green);}

/* ---------- boot ---------- */
.nm-boot{max-width:760px; margin:2.2rem auto 1rem; padding:1.4rem 1.6rem; background:#05070D; border:1px solid #3A4180; border-radius:12px;
  box-shadow:0 0 30px rgba(0,245,255,.15),inset 0 0 40px rgba(0,0,0,.6); position:relative; overflow:hidden;}
.nm-boot::after{content:""; position:absolute; inset:0; pointer-events:none; opacity:.3;
  background:repeating-linear-gradient(0deg,rgba(0,0,0,.45) 0,rgba(0,0,0,.45) 1px,transparent 1px,transparent 3px);}
.nm-boot .logo{font-family:var(--f-display); font-weight:900; font-size:2.4rem; letter-spacing:.24em; color:var(--text);
  text-shadow:0 0 8px rgba(0,245,255,.8),3px 0 0 rgba(255,43,214,.55); margin-bottom:.8rem;}
.nm-boot .ln{font-family:var(--f-mono); font-size:.9rem; color:var(--cyan); opacity:0; animation:nm-line .3s ease forwards; margin:.25rem 0;}
.nm-boot .ln.ok{color:var(--green);} .nm-boot .ln.wa{color:var(--amber);} .nm-boot .ln.er{color:var(--red);}
.nm-boot .ln span{color:var(--muted); font-size:.74rem;}

/* ---------- animation ---------- */
@keyframes nm-up{from{opacity:0; transform:translateY(6px);}to{opacity:1; transform:none;}}
@keyframes nm-line{from{opacity:0; transform:translateX(-6px);}to{opacity:1; transform:none;}}
@keyframes nm-pulse{0%,100%{box-shadow:0 0 8px rgba(0,245,255,.4),0 4px 0 #006E76;}50%{box-shadow:0 0 20px rgba(0,245,255,.85),0 4px 0 #006E76;}}
@keyframes nm-pulse-m{0%,100%{box-shadow:0 0 8px rgba(255,43,214,.4);}50%{box-shadow:0 0 22px rgba(255,43,214,.9);}}
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{animation:none!important; transition:none!important; opacity:1!important;}
}
@media (max-width:900px){
  .nm-hud{grid-template-columns:repeat(2,1fr);} .nm-wordmark{font-size:1.5rem;} .nm-chips{justify-content:flex-start;}
  [data-testid="stMainBlockContainer"]{padding:2.6rem .6rem 1rem .6rem; border-radius:10px;}
}
</style>
"""


def badge(text: str, tone: str = "muted") -> str:
    return f'<span class="nm-badge {tone}">{esc(text)}</span>'


SYNTH_BANNER = ("<b>Synthetic sample data.</b> Every parcel, building, road and corridor is artificially generated;\n"
                "IDs are not real government records. A detected intersection is a <b>potential conflict requiring review</b> - not proof of\n"
                "encroachment, ownership or a planning decision.")


def header_html(status: str, status_tone: str, mission: str, version: str, lights: list[tuple[str, str]],
                data_chip: tuple[str, str] = ("SYNTHETIC DEMO DATA", "amber"), banner_html: str = SYNTH_BANNER) -> str:
    lamps = "".join(f'<i class="{tone}" title="{esc(name)}" aria-label="{esc(name)}: {esc(tone)}"></i>'
                    for name, tone in lights)
    return f"""
<div class="nm-bezel">
  <div class="nm-lights" role="img" aria-label="Status lights">{lamps}</div>
  <div class="nm-title">
    <div class="nm-wordmark">NAGARMAP</div>
    <div class="nm-sub">URBAN INTELLIGENCE SYSTEM</div>
    <div class="nm-div">SMART CITY &amp; MOBILITY DIVISION</div>
  </div>
  <div class="nm-chips">
    <span class="nm-chip {status_tone}">SYSTEM: <b>{esc(status)}</b></span>
    <span class="nm-chip mag">MISSION: <b>{esc(mission)}</b></span>
    <span class="nm-chip {esc(data_chip[1])}">{esc(data_chip[0])}</span>
    <span class="nm-chip">v{esc(version)}</span>
  </div>
</div>
<div class="nm-synth">{banner_html}</div>
"""


def hud_html(mods: list[dict]) -> str:
    cells = []
    for m in mods:
        bar = ""
        if m.get("progress") is not None:
            bar = f'<div class="nm-bar" role="progressbar" aria-valuenow="{int(m["progress"] * 100)}" aria-valuemin="0" aria-valuemax="100"><i style="width:{m["progress"] * 100:.0f}%"></i></div>'
        cells.append(f'<div class="nm-mod {esc(m.get("tone", ""))}"><div class="lab">{esc(m["label"])}</div>'
                     f'<div class="val">{esc(m["value"])}</div>{bar}<div class="sub">{esc(m.get("sub", ""))}</div></div>')
    return '<div class="nm-hud">' + "".join(cells) + "</div>"


def boot_html(lines: list[tuple[str, str, str]]) -> str:
    """lines: (tone, text, detail). Staggered CSS reveal of real, already-completed steps."""
    rows = "".join(
        f'<div class="ln {esc(tone)}" style="animation-delay:{0.25 + i * 0.35:.2f}s">&gt; {esc(text)} <span>{esc(detail)}</span></div>'
        for i, (tone, text, detail) in enumerate(lines))
    return f'<div class="nm-boot"><div class="logo">NAGARMAP</div>{rows}</div>'


def mission_card_html(code: str, name: str, objective: str, meta: str, selected: bool) -> str:
    state = "◉ SELECTED" if selected else "○ AVAILABLE"
    return (f'<div class="nm-mcard {"sel" if selected else ""}"><div class="code">{esc(code)}</div>'
            f'<div class="name">{esc(name)}</div><div class="obj">{esc(objective)}</div>'
            f'<div class="meta">{esc(meta)}</div><div class="state">{state}</div></div>')


def diag_table_html(rows: list[tuple[str, str, str, str]]) -> str:
    """rows: (layer, check, status, detail)."""
    body = "".join(
        f'<tr><td class="mono">{esc(layer)}</td><td>{esc(name)}</td>'
        f'<td>{badge(status, STATUS_TONE.get(status, "muted"))}</td><td>{esc(detail)}</td></tr>'
        for layer, name, status, detail in rows)
    return ('<table class="nm-diag"><thead><tr><th>LAYER</th><th>CHECK</th><th>STATUS</th><th>DETAIL</th></tr></thead>'
            f"<tbody>{body}</tbody></table>")


def log_html(lines: list[tuple[str, str]]) -> str:
    """lines: (level, text) with level in ok / wa / er / in."""
    tags = {"ok": "[ OK ]", "wa": "[ !! ]", "er": "[FAIL]", "in": "[INFO]"}
    body = "\n".join(f'<span class="{lvl}">{tags.get(lvl, "[    ]")}</span> {esc(text)}' for lvl, text in lines)
    return f'<pre class="nm-log" tabindex="0">{body}</pre>'


def kv_html(pairs: list[tuple[str, str]]) -> str:
    return '<div class="nm-kv">' + "".join(f'<div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>' for k, v in pairs) + "</div>"
