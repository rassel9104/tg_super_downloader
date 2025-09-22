from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from tgdl.config.settings import settings
from tgdl.core.db import (
    db_get_flag, db_get_queue, db_get_progress_rows, db_retry_errors,
    db_purge_finished, db_clear_all, db_migrate_add_ext_id
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
    paused = (db_get_flag("PAUSED", "0") == "1")
    return {"paused": paused}

@app.get("/queue")
async def queue(_: Annotated[None, Depends(auth)] = None):
    rows = []
    for (pid, kind, payload, status, sched, ext_id) in db_get_queue():
        rows.append({
            "id": pid, "kind": kind, "payload": payload, "status": status,
            "scheduled_at": sched, "ext_id": ext_id
        })
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

# ---------- WebSocket broadcast (poll DB) ----------

clients: set[WebSocket] = set()

async def broadcaster():
    while True:
        if clients:
            payload = {
                "type": "snapshot",
                "status": {"paused": (db_get_flag("PAUSED","0")=="1")},
                "queue": [{"id":pid, "kind":kind, "status":status, "scheduled_at":sched}
                          for (pid, kind, _, status, sched, _) in db_get_queue(100)],
                "progress": db_get_progress_rows(100)
            }
            dead = []
            for ws in clients:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try: clients.remove(ws)
                except Exception: pass
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
        try: clients.remove(ws)
        except Exception: pass

# ---------- UI HTML ----------

HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>TG Downloader Panel</title>
  <style>
  body{font-family:system-ui,Segoe UI,Arial;margin:16px}
  .btn{padding:8px 12px;margin-right:8px;background:#222;color:#fff;border:none;border-radius:8px;cursor:pointer}
  .row{margin:8px 0}
  table{border-collapse:collapse;width:100%;margin-top:8px}
  th,td{padding:8px;border-bottom:1px solid #ddd;font-size:14px}
  .muted{color:#666}
  </style>
</head>
<body>
  <h2>TG Super Downloader — Panel</h2>
  <div class="row">
    <button class="btn" onclick="call('/pause')">⏸️ Pause</button>
    <button class="btn" onclick="call('/resume')">▶️ Resume</button>
    <button class="btn" onclick="call('/run')">🚀 Run now</button>
    <button class="btn" onclick="call('/retry')">🔁 Retry errors</button>
    <button class="btn" onclick="call('/purge')">🧹 Purge done/error</button>
    <button class="btn" onclick="call('/clear')">🗑️ Clear all</button>
  </div>

  <div id="status" class="muted">status…</div>

  <h3>Queue</h3>
  <table>
    <thead><tr><th>ID</th><th>Kind</th><th>Sched</th><th>Status</th><th>Actions</th></tr></thead>
    <tbody id="queue"></tbody>
  </table>

  <h3>Progress</h3>
  <table>
    <thead><tr><th>QID</th><th>Downloaded</th><th>Total</th><th>%</th><th>Updated</th></tr></thead>
    <tbody id="progress"></tbody>
  </table>

<script>
const token = localStorage.getItem('PANEL_TOKEN') || prompt("Panel token:");
localStorage.setItem('PANEL_TOKEN', token);

async function call(path){
  const r = await fetch(path,{method:'POST',headers:{'x-panel-token':token}});
  const j = await r.json();
  console.log(path,j);
}

async function cancelItem(id){
  const r = await fetch('/cancel/'+id,{method:'POST',headers:{'x-panel-token':token}});
  const j = await r.json();
  console.log('cancel',id,j);
}

function fmt(n){
  if(!n||n<=0) return '-';
  const u=['B','KB','MB','GB','TB']; let i=0,x=n;
  while(x>=1024&&i<u.length-1){x/=1024;i++}
  return x.toFixed(1)+u[i];
}

function pct(d,t){ if(!t||t<=0) return '-'; return (d/t*100).toFixed(2)+'%'; }

const qtbody = document.getElementById('queue');
const ptbody = document.getElementById('progress');
const sdiv   = document.getElementById('status');

function renderSnap(snap){
  sdiv.textContent = 'Estado: ' + (snap.status.paused ? 'PAUSADO' : 'ACTIVO');
  qtbody.innerHTML = '';
  snap.queue.forEach(r=>{
    const tr=document.createElement('tr');
    tr.innerHTML = `<td>${r.id}</td><td>${r.kind}</td><td>${r.scheduled_at}</td><td>${r.status}</td>
    <td><button class="btn" onclick="cancelItem(${r.id})">❌ Cancel</button></td>`;
    qtbody.appendChild(tr);
  });
  ptbody.innerHTML='';
  snap.progress.forEach(r=>{
    const tr=document.createElement('tr');
    tr.innerHTML = `<td>${r.qid}</td><td>${fmt(r.downloaded)}</td><td>${fmt(r.total)}</td><td>${pct(r.downloaded,r.total)}</td><td>${r.updated_at}</td>`;
    ptbody.appendChild(tr);
  })
}

function connectWS(){
  const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/ws');
  ws.onopen=()=>{ console.log('ws open'); setInterval(()=>ws.send('ping'), 10000); }
  ws.onmessage=(ev)=>{ try{ const snap=JSON.parse(ev.data); renderSnap(snap);}catch(e){} }
  ws.onclose=()=>{ console.log('ws close, retry in 2s'); setTimeout(connectWS, 2000); }
}
connectWS();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML
