# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""HTML renderer for the daily, weekly and monthly reports: colour-coded tables, usage bars and
interactive charts (hover shows value, date and time). One self-contained file, no external resources."""
import base64
import json
import os
import time
from html import escape as E

from lshme.reports.periodic import Bar, Cell

LEVEL = {"CRITICAL": ("cr", "#c5221f"), "WARNING": ("wn", "#ef8f00"), "HEALTHY": ("ok", "#1e8e3e")}

CSS = """
:root{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--bg:#eef2f7;--card:#fff;--brand:#1d4ed8;--brand2:#0b2a6f;
--ok:#15803d;--okbg:#ecfdf3;--wn:#b45309;--wnbg:#fffbeb;--cr:#b91c1c;--crbg:#fef2f2}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:64px}
body{font:15px/1.55 Inter,"Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased}
.page{max-width:1120px;margin:0 auto;padding:22px 20px 40px}
.hdr{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 26px;display:flex;justify-content:space-between;align-items:center;gap:20px;
box-shadow:0 1px 2px #0f172a0d,0 8px 24px #0f172a0a;position:relative;overflow:hidden}
.hdr:before{content:"";position:absolute;left:0;right:0;top:0;height:5px;background:linear-gradient(90deg,var(--brand2),var(--brand),#38bdf8)}
.hid{display:flex;align-items:center;gap:18px;min-width:0}
.hid img{height:46px;display:block}
.hid .sep{width:1px;align-self:stretch;background:var(--line)}
h1{margin:0;font-size:26px;font-weight:750;letter-spacing:-.02em;line-height:1.15}
.sub{color:var(--mut);font-size:13.5px;margin-top:4px}
.hr{display:flex;align-items:center;gap:14px;flex-shrink:0}
.slot{min-width:150px;max-width:230px;height:56px;border:1.5px dashed #cbd5e1;border-radius:12px;display:flex;align-items:center;justify-content:center;text-align:center;padding:4px 12px;font-weight:650;font-size:14px;line-height:1.2;color:var(--ink)}
.slot img{height:46px;max-width:210px;object-fit:contain}.slot.has{border:0;padding:0}
.big{padding:9px 20px;border-radius:12px;color:#fff;font-weight:800;font-size:19px;text-align:center;white-space:nowrap;letter-spacing:.02em}
.big small{display:block;font-weight:500;font-size:12px;letter-spacing:0;opacity:.92}
nav{position:sticky;top:0;z-index:20;background:#eef2f7e8;backdrop-filter:blur(8px);margin:14px -4px 0;padding:10px 4px;display:flex;gap:8px;overflow-x:auto;white-space:nowrap}
nav{scrollbar-width:none}nav::-webkit-scrollbar{display:none}
nav a{font-size:13px;font-weight:600;color:#334155;text-decoration:none;background:var(--card);border:1px solid var(--line);padding:6px 13px;border-radius:999px}
nav a:hover{border-color:var(--brand);color:var(--brand)}
.details{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:0;margin:14px 0 4px;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.details div{padding:12px 18px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);margin:0 -1px -1px 0}
.details span{display:block;font-size:11.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.07em;font-weight:600}.details b{font-size:15px;font-weight:650}
section{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 26px 20px;margin-top:18px;box-shadow:0 1px 2px #0f172a08}
h2{font-size:19px;font-weight:720;margin:0 0 14px;display:flex;align-items:center;gap:12px;letter-spacing:-.01em}
h2 .no{width:30px;height:30px;border-radius:9px;background:var(--brand);color:#fff;font-size:14px;display:inline-flex;align-items:center;justify-content:center;font-weight:700}
h3{font-size:15.5px;margin:20px 0 8px;font-weight:700;color:#1e293b}
table{border-collapse:collapse;width:100%;font-size:14.5px;margin-bottom:4px}
th{background:#f8fafc;text-align:left;padding:10px 12px;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:#475569;font-weight:700;border-bottom:1.5px solid var(--line)}
td{padding:11px 12px;border-bottom:1px solid #eef2f7;vertical-align:top}
tbody tr:hover td{background:#f8fafc}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}
td.wrap{white-space:pre-line}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:12px}.tw table{margin:0}.tw tr:last-child td{border-bottom:0}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 11px;border-radius:999px;font-weight:700;font-size:12.5px;white-space:nowrap}
.chip:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.chip-ok{background:var(--okbg);color:var(--ok)}.chip-wn{background:var(--wnbg);color:var(--wn)}.chip-cr{background:var(--crbg);color:var(--cr)}.chip-na{background:#f1f5f9;color:#64748b}
td.ok{background:var(--okbg)!important;color:var(--ok);font-weight:650}
td.wn{background:var(--wnbg)!important;color:var(--wn);font-weight:700}
td.cr{background:var(--crbg)!important;color:var(--cr);font-weight:700}
.up{color:var(--cr);font-weight:650}.dn{color:var(--ok);font-weight:650}.fl{color:var(--mut)}.b{font-weight:650}
.bar{background:#e8edf4;border-radius:999px;height:20px;width:150px;position:relative;display:inline-block;vertical-align:middle;overflow:hidden}
.bar i{display:block;height:20px;border-radius:999px}.bar b{position:absolute;left:0;right:0;top:1.5px;text-align:center;font-size:11.5px;color:#0f172a;font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}
.kpi{border-radius:14px;padding:16px 18px;border:1px solid var(--line);background:#fff;position:relative;overflow:hidden}
.kpi:before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--line)}
.kpi b{font-size:34px;display:block;line-height:1.1;font-weight:760;letter-spacing:-.02em}.kpi span{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.07em;font-weight:600}
.kpi.ok{background:var(--okbg);border-color:#bbf7d0}.kpi.ok:before{background:var(--ok)}
.kpi.wn{background:var(--wnbg);border-color:#fde68a}.kpi.wn:before{background:#f59e0b}
.kpi.cr{background:var(--crbg);border-color:#fecaca}.kpi.cr:before{background:var(--cr)}
.legend{font-size:12.5px;color:var(--mut);margin:6px 0 4px}
.note{background:#f8fafc;border:1px solid var(--line);border-left:4px solid var(--brand);border-radius:10px;padding:12px 16px;margin:8px 0;font-size:14.5px}
.points{display:grid;gap:10px}
.pt{display:flex;gap:14px;align-items:center;border-radius:12px;padding:14px 18px;font-size:16.5px;line-height:1.45;border:1px solid}
.pt.ok{background:var(--okbg);border-color:#bbf7d0}.pt.wn{background:var(--wnbg);border-color:#fde68a}.pt.cr{background:var(--crbg);border-color:#fecaca}
.pt i{font-style:normal;font-weight:800;font-size:15px;width:30px;height:30px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;color:#fff;flex-shrink:0}
.pt.ok i{background:var(--ok)}.pt.wn i{background:#f59e0b}.pt.cr i{background:var(--cr)}
.act{display:flex;gap:16px;align-items:flex-start;border-radius:12px;padding:14px 18px;margin:10px 0;font-size:16px;border:1px solid}
.act.wn{background:var(--wnbg);border-color:#fde68a}.act.cr{background:var(--crbg);border-color:#fecaca}.act.ok{background:var(--okbg);border-color:#bbf7d0}
.act em{font-style:normal;color:var(--mut);display:block;margin-top:3px;font-size:14.5px}
.act .n{font-weight:800;font-size:15px;width:30px;height:30px;border-radius:50%;background:#0f172a;color:#fff;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
.chart{border:1px solid var(--line);border-radius:14px;padding:14px 16px 8px;position:relative;background:#fff;break-inside:avoid}
.chart h4{margin:0 0 6px;font-size:15.5px;display:flex;justify-content:space-between;align-items:baseline;font-weight:700}
.chart h4 span{color:var(--mut);font-weight:500;font-size:13px}
.chart svg{width:100%;height:250px;display:block;overflow:visible}.chart.wide{grid-column:1/-1}.chart.wide svg{height:300px}
.keys{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;margin:2px 0 6px;color:#334155}.keys b{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.tip{position:absolute;pointer-events:none;background:#0f172af2;color:#fff;padding:9px 13px;border-radius:10px;font-size:13.5px;line-height:1.5;white-space:nowrap;display:none;z-index:5;box-shadow:0 8px 24px #0f172a55}
.tip b{font-size:17px}.tip small{display:block;color:#94a3b8;font-size:12px}
.foot{color:var(--mut);font-size:12.5px;margin:22px 4px 0;text-align:center}
@media(max-width:820px){.grid2{grid-template-columns:1fr}.hdr{flex-direction:column;align-items:flex-start}.hr{width:100%;justify-content:space-between}section{padding:16px}}
@media print{body{background:#fff}nav{display:none}.page{max-width:none;padding:0}section,.hdr{box-shadow:none;break-inside:auto}tr{break-inside:avoid}h2{break-after:avoid}}
"""

JS = r"""
(function(){
var DATA=JSON.parse(document.getElementById('chartdata').textContent),OFF=DATA.off*1000;
var MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function p2(n){return (n<10?"0":"")+n}
function fmt(t){var d=new Date(t*1000+OFF);return d.getUTCDate()+" "+MON[d.getUTCMonth()]+" "+d.getUTCFullYear()+", "+p2(d.getUTCHours())+":"+p2(d.getUTCMinutes())}
function lab(t,days){var d=new Date(t*1000+OFF);return days>2?d.getUTCDate()+" "+MON[d.getUTCMonth()]:p2(d.getUTCHours())+":00"}
function ns(tag,a){var e=document.createElementNS("http://www.w3.org/2000/svg",tag);for(var k in a)e.setAttribute(k,a[k]);return e}
var L=46,R=12,T=12,B=28;
document.querySelectorAll(".chart").forEach(function(box){
  var wide=box.classList.contains("wide"),W=wide?1060:520,H=wide?300:250;
  var c=DATA.charts[+box.dataset.i],S=c.series,P=S[0].pts;if(!P.length)return;
  var t0=P[0][0],t1=P[P.length-1][0];if(t1<=t0)t1=t0+1;
  var all=[];S.forEach(function(s){s.pts.forEach(function(p){all.push(p[1])})});
  var mx=c.max||Math.max.apply(null,all)*1.1||1,mn=c.min!==undefined&&c.min!==null?c.min:(c.max?0:Math.min.apply(null,all)-(Math.max.apply(null,all)-Math.min.apply(null,all))*0.1);
  if(mn<0&&c.max)mn=0;if(mx<=mn)mx=mn+1;
  var dec=mx<5?2:mx<50?1:0,days=(t1-t0)/86400;
  var avg=0;P.forEach(function(p){avg+=p[1]});avg/=P.length;var pk=Math.max.apply(null,all);
  if(c.multi){var kd=document.createElement("div");kd.className="keys";kd.innerHTML=S.map(function(se,i){return "<span><b style='background:"+c.colors[i%c.colors.length]+"'></b>"+se.name+"</span>"}).join("");}
  var h=document.createElement("h4");h.innerHTML=c.title+"<span>"+(c.max?"avg "+avg.toFixed(dec)+c.unit+" &middot; peak "+pk.toFixed(dec)+c.unit:"now "+P[P.length-1][1].toFixed(2)+c.unit)+"</span>";box.appendChild(h);if(c.multi)box.appendChild(kd);
  var s=ns("svg",{viewBox:"0 0 "+W+" "+H,preserveAspectRatio:"none"});box.appendChild(s);
  function X(t){return L+(t-t0)*(W-L-R)/(t1-t0)}function Y(v){return T+(1-(v-mn)/(mx-mn))*(H-T-B)}
  for(var g=0;g<=4;g++){var v=mn+(mx-mn)*g/4;s.appendChild(ns("line",{x1:L,x2:W-R,y1:Y(v),y2:Y(v),stroke:"#e3e8ee"}));
    var tx=ns("text",{x:L-4,y:Y(v)+3,"text-anchor":"end","font-size":12,fill:"#5f6b7a"});tx.textContent=v.toFixed(mx-mn<5?1:0);s.appendChild(tx)}
  for(var q=0;q<=6;q++){var tt=t0+(t1-t0)*q/6;var x2=ns("text",{x:X(tt),y:H-7,"text-anchor":q===0?"start":q===6?"end":"middle","font-size":12,fill:"#5f6b7a"});x2.textContent=lab(tt,days);s.appendChild(x2)}
  [[c.warn,"#ef8f00"],[c.crit,"#c5221f"]].forEach(function(a){if(a[0]!==null&&a[0]!==undefined&&a[0]>=mn&&a[0]<=mx)s.appendChild(ns("line",{x1:L,x2:W-R,y1:Y(a[0]),y2:Y(a[0]),stroke:a[1],"stroke-dasharray":"4 3","stroke-width":1}))});
  var cols=c.colors||[c.color,"#9aa7b8"];
  S.slice().reverse().forEach(function(se,ri){var idx=S.length-1-ri;var pts=se.pts.map(function(p){return X(p[0]).toFixed(1)+","+Y(p[1]).toFixed(1)}).join(" ");
    if(c.multi){s.appendChild(ns("polyline",{points:pts,fill:"none",stroke:cols[idx%cols.length],"stroke-width":1.5,"stroke-linejoin":"round"}));return}
    if(idx===0){s.appendChild(ns("polygon",{points:X(se.pts[0][0])+","+Y(mn)+" "+pts+" "+X(se.pts[se.pts.length-1][0])+","+Y(mn),fill:c.color,"fill-opacity":".12"}))}
    s.appendChild(ns("polyline",{points:pts,fill:"none",stroke:idx===0?c.color:"#b8c2cf","stroke-width":idx===0?1.6:1,"stroke-linejoin":"round","stroke-dasharray":idx===0?"":"3 2"}))});
  var vl=ns("line",{y1:T,y2:H-B,stroke:"#5f6b7a","stroke-dasharray":"2 2",visibility:"hidden"}),dot=ns("circle",{r:4,fill:c.color,stroke:"#fff","stroke-width":2,visibility:"hidden"});
  s.appendChild(vl);s.appendChild(dot);
  var tip=document.createElement("div");tip.className="tip";box.appendChild(tip);
  function near(pts,t){var lo=0,hi=pts.length-1;while(hi-lo>1){var m=(lo+hi)>>1;if(pts[m][0]<t)lo=m;else hi=m}return Math.abs(pts[lo][0]-t)<Math.abs(pts[hi][0]-t)?pts[lo]:pts[hi]}
  function move(e){
    var rc=s.getBoundingClientRect(),cx=(e.touches?e.touches[0].clientX:e.clientX),f=(cx-rc.left)/rc.width*W;
    var t=t0+(f-L)/(W-L-R)*(t1-t0);if(t<t0)t=t0;if(t>t1)t=t1;var p=near(P,t);
    vl.setAttribute("x1",X(p[0]));vl.setAttribute("x2",X(p[0]));vl.setAttribute("visibility","visible");
    dot.setAttribute("cx",X(p[0]));dot.setAttribute("cy",Y(p[1]));dot.setAttribute("visibility","visible");
    var st="";if(c.crit!==null&&c.crit!==undefined){st=" &middot; "+(p[1]>=c.crit?"CRITICAL":p[1]>=c.warn?"WARNING":"OK")}
    var body="<b>"+p[1].toFixed(dec)+c.unit+"</b> &nbsp;"+S[0].name+st;
    for(var k=1;k<S.length;k++){var q2=near(S[k].pts,p[0]);body+="<br>"+S[k].name+": <b>"+q2[1].toFixed(dec)+c.unit+"</b>"}
    tip.innerHTML="<small>"+fmt(p[0])+"</small>"+body;tip.style.display="block";
    var bx=box.getBoundingClientRect(),left=cx-bx.left+12;if(left>bx.width-200)left=cx-bx.left-200;
    tip.style.left=left+"px";tip.style.top=(rc.top-bx.top+10)+"px";
  }
  function out(){vl.setAttribute("visibility","hidden");dot.setAttribute("visibility","hidden");tip.style.display="none"}
  s.addEventListener("mousemove",move);s.addEventListener("touchmove",move);s.addEventListener("mouseleave",out);s.addEventListener("touchend",out);
});
})();
"""


def cell_html(c, align=""):
    if isinstance(c, Bar):
        col = {"ok": "#1e8e3e", "wn": "#ef8f00", "cr": "#c5221f"}[c.level()]
        return "<td><span class='bar'><i style='width:%.0f%%;background:%s'></i><b>%.0f%%</b></span></td>" % (min(c.pct, 100), col, c.pct)
    cls = [align] if align else []
    inner = E(str(c))
    if isinstance(c, Cell):
        st = c.style
        if st.startswith("chip-"):
            return "<td><span class='chip %s'>%s</span></td>" % (st, E(str(c.text)))
        if st in ("ok", "wn", "cr"):
            cls.append(st)
        elif st:
            inner = "<span class='%s'>%s</span>" % (st, inner) if st in ("up", "dn", "fl", "b") else inner
            if st == "wrap":
                cls.append("wrap")
    return "<td%s>%s</td>" % ((" class='%s'" % " ".join(cls)) if cls else "", inner)


def block_html(b, charts):
    t = b["type"]
    if b.get("excel_only"):
        return ""
    if t == "points":
        icon = {"ok": "&#10004;", "wn": "&#9888;", "cr": "&#10006;"}
        return "<div class='points'>%s</div>" % "".join("<div class='pt %s'><i>%s</i><div>%s</div></div>" % (lv, icon[lv], E(txt)) for lv, txt in b["items"])
    if t == "wide_charts":
        out = []
        for c in b["items"]:
            charts.append(c)
            out.append("<div class='chart wide' data-i='%d'></div>" % (len(charts) - 1))
        return "<div class='grid2'>%s</div>" % "".join(out)
    if t == "kpis":
        return "<div class='kpis'>%s</div>" % "".join("<div class='kpi %s'><b>%s</b><span>%s</span></div>" % (lv, E(v), E(l)) for v, l, lv in b["items"])
    if t == "table":
        al = b.get("align") or []
        head = "".join("<th%s>%s</th>" % (" class='r'" if i < len(al) and al[i] == "r" else "", E(h)) for i, h in enumerate(b["headers"]))
        rows = "".join("<tr>%s</tr>" % "".join(cell_html(c, al[i] if i < len(al) else "") for i, c in enumerate(r)) for r in b["rows"])
        return "%s<div class='tw'><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>%s" % (
            ("<h3>%s</h3>" % E(b["title"])) if b["title"] else "", head, rows, ("<div class='legend'>%s</div>" % E(b["legend"])) if b.get("legend") else "")
    if t == "charts":
        out = []
        for c in b["items"]:
            charts.append(c)
            out.append("<div class='chart' data-i='%d'></div>" % (len(charts) - 1))
        return "<div class='grid2'>%s</div><div class='legend'>Hover (or tap) a chart for the exact value, date and time. Dashed lines are the warning and critical limits.</div>" % "".join(out)
    if t == "note":
        return "<div class='note'>%s</div>" % E(b["text"])
    if t == "actions":
        return "".join("<div class='act %s'><span class='n'>%d</span><div><b>%s</b><em>Why it matters: %s</em></div></div>" % (lv, i, E(msg), E(rec)) for i, (lv, msg, rec) in enumerate(b["items"], 1))
    if t == "kv":
        rows = "".join("<tr><th style='width:130px'>%s</th><td>%s</td><th style='width:130px'>%s</th><td>%s</td></tr>" % tuple(E(str(x)) for x in r) for r in b["rows"])
        return "<table><tbody>%s</tbody></table>" % rows
    return ""


def render_string(doc, logo=""):
    charts = []
    parts = []
    for n, (title, blocks) in enumerate(doc.sections, 1):
        parts.append("<section id='s%d'><h2><span class='no'>%d</span>%s</h2>%s</section>" % (n, n, E(title), "".join(block_html(b, charts) for b in blocks)))
    nav = "<nav>%s</nav>" % "".join("<a href='#s%d'>%s</a>" % (n, E(t)) for n, (t, _) in enumerate(doc.sections, 1))
    lv, col = LEVEL.get(doc.status, ("wn", "#6d6d6d"))
    img = ""
    path = logo or doc.brand_logo
    if path and os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                img = "<img alt='' src='data:image/png;base64,%s'>" % base64.b64encode(f.read()).decode("ascii")
        except OSError:
            img = ""
    attention = doc.counts.get("CRITICAL", 0) + doc.counts.get("WARNING", 0)
    banner = "<div class='note' style='border-color:#ef8f00'>%s</div>" % E(doc.banner) if doc.banner else ""
    cust = ""
    cpath = getattr(doc, "customer_logo", "")
    if cpath and os.path.isfile(cpath):
        try:
            with open(cpath, "rb") as f:
                ext = os.path.splitext(cpath)[1].lower().strip(".")
                cust = "<div class='slot has'><img alt='' src='data:image/%s;base64,%s'></div>" % ("jpeg" if ext in ("jpg", "jpeg") else "png", base64.b64encode(f.read()).decode("ascii"))
        except OSError:
            cust = ""
    if not cust:
        cust = "<div class='slot'>%s</div>" % (E(doc.customer) if getattr(doc, "customer", "") else "Customer logo")
    det = "<div class='details'>%s</div>" % "".join("<div><span>%s</span><b>%s</b></div>" % (E(k), E(v)) for k, v in getattr(doc, "details", []))
    data = json.dumps({"off": doc.tz_offset, "charts": charts}, separators=(",", ":")).replace("</", "<\\/")
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>%s - %s</title><style>%s</style></head><body><div class='page'>"
            "<div class='hdr'><div class='hid'>%s<div><h1>%s</h1><div class='sub'>%s</div></div></div>"
            "<div class='hr'>%s<div class='big' style='background:%s'>%s<small>%s</small></div></div></div>%s%s%s%s"
            "<div class='foot'>Generated by %s &middot; %s &middot; %s</div></div>"
            "<script type='application/json' id='chartdata'>%s</script><script>%s</script></body></html>") % (
        E(doc.title), E(doc.host), CSS.replace("\\\\", "\\"), img, E(doc.title), E(doc.subtitle), cust, col, E(doc.status),
        ("%d item%s need attention" % (attention, "" if attention == 1 else "s")) if attention else "all clear",
        nav, det, banner, "".join(parts), E(dict(doc.meta).get("Generated by", "LRS Vitals")), E(time.strftime("%Y-%m-%d %H:%M", time.localtime(doc.generated))),
        E(doc.copyright), data, JS)


def render(doc, path, logo=""):
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_string(doc, logo))
    return path


def email_body(doc, report_files=()):
    """Compact, inline-styled HTML for the email body (mail clients ignore most CSS)."""
    lv, col = LEVEL.get(doc.status, ("wn", "#6d6d6d"))
    bg = {"ok": "#e6f4ea", "wn": "#fff3d6", "cr": "#fce8e6"}
    fg = {"ok": "#1e8e3e", "wn": "#b26a00", "cr": "#c5221f"}
    td = "style='padding:4px 8px;border:1px solid #dfe5ec;font-size:13px'"
    out = ["<html><body style='font-family:Segoe UI,Arial,sans-serif;color:#1b2430'>",
           "<h2 style='margin:0'>%s</h2><p style='margin:2px 0 10px;color:#5f6b7a'>%s</p>" % (E(doc.title), E(doc.subtitle)),
           "<p style='display:inline-block;background:%s;color:#fff;padding:6px 16px;border-radius:5px;font-weight:bold;font-size:16px'>%s</p>" % (col, E(doc.status))]
    for title, blocks in doc.sections:
        for b in blocks:
            if b["type"] == "kpis":
                out.append("<table style='border-collapse:separate;border-spacing:6px'><tr>%s</tr></table>" % "".join(
                    "<td style='background:%s;border-left:4px solid %s;padding:6px 12px'><b style='font-size:18px'>%s</b><br><span style='font-size:11px;color:#5f6b7a'>%s</span></td>" % (
                        bg[l], fg[l], E(v), E(lbl)) for v, lbl, l in b["items"]))
            elif b["type"] == "table" and title.lower().startswith(("daily checklist", "checklist")):
                rows = []
                for r in b["rows"]:
                    c0 = r[0]
                    l = c0.style[-2:] if isinstance(c0, Cell) else "ok"
                    rows.append("<tr><td style='padding:4px 8px;border:1px solid #dfe5ec;background:%s;color:%s;font-weight:bold;font-size:12px'>%s</td>%s</tr>" % (
                        bg.get(l, "#fff"), fg.get(l, "#000"), E(str(c0)), "".join("<td %s>%s</td>" % (td, E(str(x))) for x in (r[1], r[2], r[5]))))
                out.append("<h3 style='margin:14px 0 4px'>Checklist</h3><table style='border-collapse:collapse'>%s</table>" % "".join(rows))
            elif b["type"] == "actions":
                out.append("<h3 style='margin:14px 0 4px'>What needs attention</h3>" + "".join(
                    "<p style='margin:4px 0;padding:6px 10px;background:%s'><b>%s</b><br>%s</p>" % (bg[l], E(m), E(r)) for l, m, r in b["items"]))
    out.append("<p>Attached: %s</p><p style='color:#888;font-size:12px'>%s &middot; %s</p></body></html>" % (
        E(", ".join(os.path.basename(p) for p in report_files) or "none"), E(dict(doc.meta).get("Generated by", "LRS Vitals")), E(doc.copyright)))
    return "".join(out)
