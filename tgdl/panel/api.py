from __future__ import annotations
import asyncio
from datetime import datetime, timedelta
from typing import Annotated

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

import re, json
from zoneinfo import ZoneInfo

from tgdl.config.settings import settings
from tgdl.core.db import (
    db_get_flag,
    db_get_queue,
    db_get_progress_rows,
    db_retry_errors,
    db_purge_finished,
    db_clear_all,
    db_migrate_add_ext_id,
    db_add,
    db_clear_progress,
    _connect,
)

app = FastAPI(title="TG Super Downloader Panel", version="0.2.0")


def auth(x_panel_token: Annotated[str | None, Header()] = None):
    if settings.PANEL_TOKEN and x_panel_token != settings.PANEL_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


CONTROL_URL = "http://127.0.0.1:8765"


@app.on_event("startup")
async def _startup():
    # asegurar columna ext_id en DB
    db_migrate_add_ext_id()


# ---------- API JSON ----------


@app.get("/health")
async def health():
    return {"ok": True, "time": datetime.now().isoformat()}


@app.get("/status")
async def status(_: Annotated[None, Depends(auth)] = None):
    paused = db_get_flag("PAUSED", "0") == "1"
    return {"paused": paused}


@app.get("/queue")
async def queue(_: Annotated[None, Depends(auth)] = None):
    rows = []
    for pid, kind, payload, status, sched, ext_id in db_get_queue():
        rows.append(
            {
                "id": pid,
                "kind": kind,
                "payload": payload,
                "status": status,
                "scheduled_at": sched,
                "ext_id": ext_id,
            }
        )
    return {"rows": rows}


@app.get("/progress")
async def progress(_: Annotated[None, Depends(auth)] = None):
    return {"rows": db_get_progress_rows()}


@app.post("/pause")
async def pause(_: Annotated[None, Depends(auth)] = None):
    async with httpx.AsyncClient(timeout=5.0) as cli:
        r = await cli.post(f"{CONTROL_URL}/pause")
        return JSONResponse(r.json())


@app.post("/resume")
async def resume(_: Annotated[None, Depends(auth)] = None):
    async with httpx.AsyncClient(timeout=5.0) as cli:
        r = await cli.post(f"{CONTROL_URL}/resume")
        return JSONResponse(r.json())


@app.post("/run")
async def run(_: Annotated[None, Depends(auth)] = None):
    async with httpx.AsyncClient(timeout=5.0) as cli:
        r = await cli.post(f"{CONTROL_URL}/run")
        return JSONResponse(r.json())


@app.post("/retry")
async def retry(_: Annotated[None, Depends(auth)] = None):
    db_retry_errors()
    return {"ok": True}


@app.post("/purge")
async def purge(_: Annotated[None, Depends(auth)] = None):
    n = db_purge_finished()
    return {"ok": True, "deleted": n}


@app.post("/clear")
async def clear(_: Annotated[None, Depends(auth)] = None):
    # pausamos primero
    try:
        async with httpx.AsyncClient(timeout=5.0) as cli:
            await cli.post(f"{CONTROL_URL}/pause")
    except Exception:
        pass
    db_clear_all()
    return {"ok": True, "cleared": True}


@app.post("/cancel/{qid}")
async def cancel(qid: int, _: Annotated[None, Depends(auth)] = None):
    async with httpx.AsyncClient(timeout=5.0) as cli:
        r = await cli.post(f"{CONTROL_URL}/cancel/{qid}")
        return JSONResponse(r.json())


# ---------- Utilidades para encolado desde Panel ----------
URL_RE = re.compile(r"(https?://\S+|magnet:\?xt=urn:btih:[A-Za-z0-9]+[^ \n]*)", re.IGNORECASE)
TG_LINK_RE = re.compile(r"https?://t\.me/[^\s]+", re.IGNORECASE)
TZ = ZoneInfo(settings.TIMEZONE)


def _extract_urls(text: str) -> list[str]:
    if not text:
        return []
    return URL_RE.findall(text)


def _extract_tg_links(text: str) -> list[str]:
    if not text:
        return []
    return TG_LINK_RE.findall(text)


def _next_schedule_datetime():
    now = datetime.now(tz=TZ)
    scheduled_at = now.replace(hour=settings.SCHEDULE_HOUR, minute=0, second=0, microsecond=0)
    if scheduled_at <= now:
        scheduled_at += timedelta(days=1)
    return scheduled_at


@app.post("/enqueue")
async def enqueue(data: dict, _: Annotated[None, Depends(auth)] = None):
    """
    Encola enlaces desde el panel. Body: {"text": "url1\nurl2 ..."}
    Detecta t.me -> tg_link, otros -> url. Programa a la próxima hora.
    """
    text = (data or {}).get("text", "") or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Texto vacío")

    sched = _next_schedule_datetime()
    n_added = 0

    # 1) Links Telegram (t.me)
    for u in _extract_tg_links(text):
        db_add("tg_link", {"url": u}, sched)
        n_added += 1

    # 2) URLs/magnets (excluye t.me)
    urls = [u for u in _extract_urls(text) if not u.lower().startswith("https://t.me/")]
    for u in urls:
        db_add("url", {"url": u}, sched)
        n_added += 1

    return {"ok": True, "added": n_added, "scheduled_at": sched.isoformat()}


@app.post("/delete/{qid}")
async def delete_item(qid: int, _: Annotated[None, Depends(auth)] = None):
    """
    Elimina definitivamente un elemento de la cola:
    1) Intenta cancelarlo (detiene aria2/yt-dlp si corresponde)
    2) Limpia progreso
    3) Borra de la tabla queue
    """
    # 1) cancel cooperativo
    try:
        async with httpx.AsyncClient(timeout=5.0) as cli:
            await cli.post(f"{CONTROL_URL}/cancel/{qid}")
    except Exception:
        pass  # aunque falle, intentamos limpiar DB

    # 2) limpiar progreso + 3) borrar fila
    try:
        db_clear_progress(qid)
    except Exception:
        pass
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM queue WHERE id=?", (qid,))
            conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB delete error: {e!r}")

    return {"ok": True, "deleted": True, "id": qid}


# ---------- WebSocket broadcast (poll DB) ----------

clients: set[WebSocket] = set()


async def broadcaster():
    while True:
        if clients:
            # Construimos una "queue" con título amistoso
            q_rows = []
            for pid, kind, payload, status, sched, _ in db_get_queue(100):
                title = ""
                try:
                    import json as _json

                    d = _json.loads(payload or "{}")
                    title = d.get("suggested_name") or d.get("url") or ""
                except Exception:
                    pass
                # Fallback amigable
                if not title:
                    title = (payload[:60] + "…") if payload else "-"
                q_rows.append(
                    {
                        "id": pid,
                        "kind": kind,
                        "status": status,
                        "scheduled_at": sched,
                        "title": title,
                    }
                )

            payload = {
                "type": "snapshot",
                "status": {"paused": (db_get_flag("PAUSED", "0") == "1")},
                "queue": q_rows,
                "progress": db_get_progress_rows(100),
            }

            dead = []
            for ws in clients:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    clients.remove(ws)
                except Exception:
                    pass
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def _bg():
    asyncio.create_task(broadcaster())


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            # opcional: recibir mensajes del cliente (no usado)
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            clients.remove(ws)
        except Exception:
            pass


# ---------- UI HTML ----------

HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>TG Super Downloader Panel</title>
  <style>
    :root{
      --bg:#0f1115;--card:#151821;--fg:#e8eaf0;--muted:#a7b0c0;--accent:#5aa2ff;
      --ok:#35c46a;--err:#ff5d6c;--warn:#ffc857;--border:#222838;--chip:#1f2430;
      --shadow:0 4px 14px rgba(0,0,0,.25);
    }
    @media (prefers-color-scheme: light){
      :root{
        --bg:#f7f8fb;--card:#ffffff;--fg:#0f1115;--muted:#5a667a;--accent:#0b6bff;
        --ok:#1ea65a;--err:#d93451;--warn:#c68a0a;--border:#e5e9f2;--chip:#f2f5fb;
        --shadow:0 8px 24px rgba(16,24,40,.08);
      }
    }
    *{box-sizing:border-box}
    body{font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;margin:0;background:var(--bg);color:var(--fg)}
    .topbar{
      position:sticky;top:0;z-index:10;background:linear-gradient(180deg,var(--bg),rgba(0,0,0,0));
      padding:12px 16px;border-bottom:1px solid var(--border);backdrop-filter:saturate(160%) blur(6px);
    }
    .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
    .title{margin:0;font-size:18px;font-weight:700;letter-spacing:.2px}
    .spacer{flex:1}
    .btn{padding:8px 12px;border:none;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer;box-shadow:var(--shadow)}
    .btn.ghost{background:transparent;border:1px solid var(--accent);color:var(--accent);box-shadow:none}
    .btn.red{background:var(--err)}
    .btn.ok{background:var(--ok)}
    .btn.warn{background:var(--warn);color:#000}
    .surface{padding:16px}
    .card{background:var(--card);padding:14px;border-radius:14px;box-shadow:var(--shadow);border:1px solid var(--border)}
    .grid{display:grid;gap:12px;grid-template-columns:1fr}
    @media(min-width:960px){ .grid{grid-template-columns:1fr} }
    .filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
    .chip{background:var(--chip);border:1px solid var(--border);border-radius:10px;padding:8px 10px;color:var(--muted)}
    .chip input, .chip select{
      background:transparent;border:none;color:#aab;outline:none;min-width:200px
    }
    table{border-collapse:collapse;width:100%;font-size:14px}
    th,td{padding:10px;border-bottom:1px solid var(--border);vertical-align:middle}
    th{font-weight:700;text-align:left;color:var(--muted)}
    .muted{color:var(--muted)}
    .badge{padding:3px 10px;border-radius:999px;font-size:12px;background:var(--chip);border:1px solid var(--border)}
    .b-ok{background:rgba(53,196,106,.15);color:var(--ok);border-color:rgba(53,196,106,.35)}
    .b-err{background:rgba(255,93,108,.15);color:var(--err);border-color:rgba(255,93,108,.35)}
    .b-queued{background:rgba(90,162,255,.15);color:var(--accent);border-color:rgba(90,162,255,.35)}
    .b-paused{background:rgba(255,200,87,.15);color:var(--warn);border-color:rgba(255,200,87,.35)}
    .b-running{background:rgba(160, 160, 255,.18);color:#aab; border-color:rgba(160,160,255,.35)}
    .progress{height:10px;background:rgba(255,255,255,.06);border:1px solid var(--border);border-radius:8px;overflow:hidden}
    .bar{height:100%;width:0%;background:linear-gradient(90deg,#6aa6ff,#79ffa7)}
    .top-actions .btn{white-space:nowrap}
    .toast{position:fixed;bottom:14px;right:14px;background:var(--card);color:var(--fg);padding:10px 12px;border-radius:10px;border:1px solid var(--border);opacity:0;transform:translateY(10px);transition:all .2s;box-shadow:var(--shadow)}
    .toast.show{opacity:1;transform:translateY(0)}
    .mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
    .nowrap{white-space:nowrap}
  </style>
</head>
<body>

  <div class="topbar">
    <div class="row top-actions">
      <h2 class="title">TG Super Downloader — Panel</h2>
      <div class="spacer"></div>
      <button class="btn" onclick="call('/pause')">⏸️ Pausa</button>
      <button class="btn ok" onclick="call('/resume')">▶️ Reanudar</button>
      <button class="btn" onclick="call('/run')">🚀 Ejecutar ahora</button>
      <button class="btn" onclick="call('/retry')">🔁 Reintentar</button>
      <button class="btn warn" onclick="call('/purge')">🧹 Purga</button>
      <button class="btn red" onclick="confirmClear()">🗑️ Limpiar TODO</button>
      <button class="btn ghost" onclick="logout()">Logout</button>
    </div>
  </div>

  <div class="surface grid">
    <div class="card">
      <div class="row" style="justify-content:space-between;align-items:center;gap:8px">
        <div id="status" class="muted">Cargando estado…</div>
        <div class="filters">
          <div class="chip">
            <select id="fltStatus" onchange="renderLastSnap()">
              <option value="">Estado: Todos</option>
              <option value="queued">Estado: queued</option>
              <option value="running">Estado: running</option>
              <option value="paused">Estado: paused</option>
              <option value="done">Estado: done</option>
              <option value="error">Estado: error</option>
            </select>
          </div>
          <div class="chip">
            <input id="fltQuery" type="search" placeholder="Buscar por título/ID/tipo…" oninput="renderLastSnap()"/>
          </div>
          <div class="chip" style="display:flex;gap:6px;align-items:center">
            <input id="enqText" type="text" placeholder="Pega enlaces aquí (t.me, http, magnet)..." style="min-width:320px"/>
            <button class="btn ok" onclick="enqueueLinks()">➕ Añadir</button>
          </div>
        </div>
      </div>      
    </div>

    <div class="card">
      <h3 style="margin:0 0 8px 0">Cola</h3>
      <table>
        <thead>
          <tr>
            <th class="nowrap">ID</th>
            <th>Título</th>
            <th class="nowrap">Tipo</th>
            <th class="nowrap">Programado</th>
            <th class="nowrap">Estado</th>
            <th class="nowrap">Acciones</th>
          </tr>
        </thead>
        <tbody id="queue"></tbody>
      </table>
    </div>

    <div class="card">
      <h3 style="margin:0 0 8px 0">Progreso</h3>
      <table>
        <thead>
          <tr>
            <th class="nowrap">QID</th>
            <th>Progreso</th>
            <th class="nowrap">Bytes</th>
            <th class="nowrap">%</th>
            <th class="nowrap">Vel.</th>
            <th class="nowrap">ETA</th>
            <th class="nowrap">Actualizado</th>
          </tr>
        </thead>
        <tbody id="progress"></tbody>
      </table>
    </div>
  </div>

  <div id="toast" class="toast">OK</div>

<script>
const token = (localStorage.getItem('PANEL_TOKEN') || prompt("Panel token:") || "").trim();
localStorage.setItem('PANEL_TOKEN', token);

function logout(){
  localStorage.removeItem('PANEL_TOKEN');
  location.reload();
}

function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 1200);
}

async function call(path){
  const r = await fetch(path,{method:'POST',headers:{'x-panel-token':token}});
  const j = await r.json().catch(()=>({}));
  toast(j.ok ? 'Hecho' : ('Error: ' + (j.detail || j.error || '')));
  console.log(path,j);
}

async function cancelItem(id){
  const r = await fetch('/cancel/'+id,{method:'POST',headers:{'x-panel-token':token}});
  const j = await r.json().catch(()=>({}));
  toast(j.ok ? 'Cancelado' : ('Error al cancelar: ' + (j.detail || j.error || '')));
  console.log('cancel',id,j);
}

// ===== Render helpers
function fmt(n){
  if(!n||n<=0) return '-';
  const u=['B','KB','MB','GB','TB']; let i=0,x=n;
  while(x>=1024&&i<u.length-1){x/=1024;i++}
  return x.toFixed(1)+u[i];
}
function pct(d,t){ if(!t||t<=0) return '-'; return (d/t*100).toFixed(2)+'%'; }

function badge(status){
  const s = String(status||'').toLowerCase();
  if(s==='done')   return '<span class="badge b-ok">done</span>';
  if(s==='error')  return '<span class="badge b-err">error</span>';
  if(s==='paused') return '<span class="badge b-paused">paused</span>';
  if(s==='queued') return '<span class="badge b-queued">queued</span>';
  if(s==='running')return '<span class="badge b-running">running</span>';
  return `<span class="badge">${status||'-'}</span>`;
}

const qtbody = document.getElementById('queue');
const ptbody = document.getElementById('progress');
const sdiv   = document.getElementById('status');
const fltStatus = document.getElementById('fltStatus');
const fltQuery  = document.getElementById('fltQuery');

const prev = new Map(); // qid -> {bytes,time}
let lastSnap = null;

function renderQueue(rows){
  qtbody.innerHTML = '';
  rows.forEach(r=>{
    const tr=document.createElement('tr');
    const safeTitle = (r.title || '').toString();
    tr.innerHTML = `
      <td class="mono">${r.id}</td>
      <td>${safeTitle.replace(/</g,'&lt;')}</td>
      <td class="nowrap">${r.kind}</td>
      <td class="nowrap"><span class="muted">${r.scheduled_at||'-'}</span></td>
      <td>${badge(r.status)}</td>
      <td style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn red" onclick="cancelItem(${r.id})">❌ Cancelar</button>
        <button class="btn ghost" onclick="deleteItem(${r.id})">🗑️ Eliminar</button>
      </td>`;
    qtbody.appendChild(tr);
  });
}

function renderProgress(rows){
  ptbody.innerHTML='';
  rows.forEach(r=>{
    const tr=document.createElement('tr');

    // velocidad/eta (cliente)
    const now = performance.now()/1000;
    const key = r.qid;
    const prevEntry = prev.get(key);
    let speed = null, eta = null;
    if(prevEntry && now - prevEntry.time > 0.5 && r.downloaded > 0){
      speed = (r.downloaded - prevEntry.bytes) / (now - prevEntry.time);
      if(r.total>0 && speed>0) eta = (r.total - r.downloaded) / speed;
    }
    prev.set(key, {bytes: r.downloaded, time: now});

    const pcent = (r.total>0) ? (r.downloaded/r.total*100) : 0;
    const barCls = 'bar';

    tr.innerHTML = `
      <td class="mono">${r.qid}</td>
      <td><div class="progress"><div class="${barCls}" style="width:${pcent.toFixed(2)}%"></div></div></td>
      <td class="nowrap">${fmt(r.downloaded)} / ${fmt(r.total)}</td>
      <td class="nowrap">${pct(r.downloaded,r.total)}</td>
      <td class="nowrap">${speed ? fmt(speed) + '/s' : '-'}</td>
      <td class="nowrap">${eta ? (eta>86400 ? '>' : '') + new Date(eta*1000).toISOString().substr(11,8) : '-'}</td>
      <td class="muted nowrap">${r.updated_at}</td>`;
    ptbody.appendChild(tr);
  })
}

function applyFilters(rows){
  const s = fltStatus.value.trim().toLowerCase();
  const q = fltQuery.value.trim().toLowerCase();
  return rows.filter(r=>{
    const statusOk = !s || String(r.status||'').toLowerCase()===s;
    if(!statusOk) return false;
    if(!q) return true;
    const haystack = (r.title+' '+r.kind+' '+r.id).toLowerCase();
    return haystack.includes(q);
  });
}

function renderSnap(snap){
  sdiv.innerHTML = 'Estado: ' + (snap.status.paused ? '<span class="badge b-paused">PAUSADO</span>' : '<span class="badge b-ok">ACTIVO</span>');
  const q = Array.isArray(snap.queue) ? snap.queue : [];
  const p = Array.isArray(snap.progress) ? snap.progress : [];
  // Filtros en cliente
  renderQueue(applyFilters(q));
  renderProgress(p);
}

function renderLastSnap(){
  if(lastSnap) renderSnap(lastSnap);
}

function connectWS(){
  const proto = (location.protocol==='https:'?'wss':'ws');
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = ()=>{ setInterval(()=>{ try{ ws.send('ping') }catch(e){} }, 10000); };
  ws.onmessage = (ev)=>{ try{ lastSnap = JSON.parse(ev.data); renderSnap(lastSnap);}catch(e){} };
  ws.onclose = ()=>{ setTimeout(connectWS, 1500); };
}
connectWS();

function confirmClear(){
  if(confirm('Esto pausará y limpiará toda la cola y progreso. ¿Continuar?')){
    call('/clear');
  }
}

async function enqueueLinks(){
  const el = document.getElementById('enqText');
  const text = (el.value || '').trim();
  if(!text){ toast('Nada que encolar'); return; }
  const r = await fetch('/enqueue',{
    method:'POST',
    headers:{'x-panel-token':token,'Content-Type':'application/json'},
    body: JSON.stringify({text})
  });
  const j = await r.json().catch(()=>({}));
  if(j.ok){
    toast(`Encolados: ${j.added} (para ${j.scheduled_at || ''})`);
    el.value = '';
  }else{
    toast('Error al encolar');
  }
}

async function deleteItem(id){
  if(!confirm(`Eliminar definitivamente el #${id}?`)) return;
  const r = await fetch('/delete/'+id,{method:'POST',headers:{'x-panel-token':token}});
  const j = await r.json().catch(()=>({}));
  toast(j.ok ? 'Eliminado' : ('Error al eliminar: ' + (j.detail || j.error || '')));
}

</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML
