#!/usr/bin/env python3
"""
UGV Monitor — standalone MQTT subscriber + browser UI
Connects directly to the broker, displays live joystick and telemetry.

Usage:
    python3 monitor.py
    # open http://<PI_IP>:8080 in browser
"""
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
try:
    import yaml
except ImportError:
    print("PyYAML not found — activate the venv first: source venv/bin/activate")
    sys.exit(1)

ROOT = Path(__file__).parent


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_config() -> dict:
    with open(ROOT / "config" / "default_config.yaml") as f:
        cfg = yaml.safe_load(f) or {}
    user = ROOT / "config" / "config.yaml"
    if user.exists():
        with open(user) as f:
            cfg = _deep_merge(cfg, yaml.safe_load(f) or {})
    return cfg


cfg = _load_config()

# ── Shared state ──────────────────────────────────────────────────────────────
_state: dict = {"connected": False, "rx_count": 0}
_state_lock = threading.Lock()
_sse_queues: list[queue.Queue] = []
_sse_lock = threading.Lock()
_ping_times: dict[int, float] = {}          # seq → monotonic ms at ping arrival


def _push(data: str) -> None:
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)


# ── MQTT ──────────────────────────────────────────────────────────────────────
import paho.mqtt.client as mqtt


def _on_connect(client, userdata, flags, rc, properties=None):
    """Connect callback for ugv_client — subscribes to joystick and ping only."""
    ok = str(rc) in ("Success", "0") or rc == 0
    with _state_lock:
        _state["connected"] = ok
    topics = cfg.get("topics", {})
    client.subscribe(topics.get("joystick_control", "ugv/joystick"), qos=0)
    client.subscribe(topics.get("latency_ping", "ugv/ping"), qos=0)
    _push(json.dumps({"type": "status", "connected": ok}))


def _on_connect_rcs(client, userdata, flags, rc, properties=None):
    """Connect callback for rcs_operator — subscribes to pong and telemetry."""
    topics = cfg.get("topics", {})
    client.subscribe(topics.get("latency_pong", "ugv/pong"), qos=0)
    client.subscribe(topics.get("telemetry", "ugv/telemetry"), qos=0)


def _on_disconnect(client, userdata, flags=None, rc=None, properties=None):
    with _state_lock:
        _state["connected"] = False
    _push(json.dumps({"type": "status", "connected": False}))


def _on_message(client, userdata, msg):
    topics = cfg.get("topics", {})
    try:
        payload = json.loads(msg.payload)
        with _state_lock:
            _state["rx_count"] += 1
            count = _state["rx_count"]
        if msg.topic == topics.get("joystick_control", "ugv/joystick"):
            _push(json.dumps({"type": "joystick", "data": payload, "n": count}))
        elif msg.topic == topics.get("telemetry", "ugv/telemetry"):
            _push(json.dumps({"type": "telemetry", "data": payload}))
        elif msg.topic == topics.get("latency_ping", "ugv/ping"):
            # Record arrival time of ping keyed by seq; used to compute RTT on pong.
            seq = int(payload["seq"])
            _ping_times[seq] = time.monotonic() * 1000
            # Prune stale entries older than 10 s to avoid unbounded growth.
            now_ms = time.monotonic() * 1000
            stale = [k for k, v in _ping_times.items() if now_ms - v > 10_000]
            for k in stale:
                _ping_times.pop(k, None)
        elif msg.topic == topics.get("latency_pong", "ugv/pong"):
            seq = int(payload["seq"])
            t_sent = _ping_times.pop(seq, None)
            if t_sent is not None:
                rtt_ms = time.monotonic() * 1000 - t_sent
                _push(json.dumps({"type": "latency", "rtt": round(rtt_ms, 1)}))
            # If ping was never seen (missed), skip — no cross-clock RTT computation.
    except Exception:
        pass


def _start_mqtt() -> mqtt.Client:
    """Start the ugv_client MQTT connection — subscribes to joystick and ping."""
    mc = cfg.get("mqtt", {})
    client = mqtt.Client(client_id="ugv-monitor", clean_session=True)
    client.username_pw_set(mc.get("username", ""), mc.get("password", ""))
    tls = mc.get("tls", {})
    if tls.get("enabled", False):
        ca = tls.get("ca_certs", "")
        client.tls_set(ca_certs=ca if ca else None)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect_async(mc.get("host", "localhost"), mc.get("port", 8883), keepalive=30)
    client.loop_start()
    return client


def _start_rcs_mqtt() -> mqtt.Client | None:
    """Start a second MQTT connection as rcs_operator — subscribes to pong and telemetry.

    Returns None if rcs_password is not configured (ACL would block pong/telemetry for ugv_client).
    """
    mc = cfg.get("mqtt", {})
    rcs_cfg = mc.get("rcs", {})
    rcs_user = rcs_cfg.get("username") or mc.get("rcs_username", "rcs_operator")
    rcs_pass = rcs_cfg.get("password") or mc.get("rcs_password", "")

    if not rcs_pass:
        print("[WARN] rcs_password not set in config — pong/telemetry monitoring disabled")
        return None

    client = mqtt.Client(client_id="ugv-monitor-rcs", clean_session=True)
    client.username_pw_set(rcs_user, rcs_pass)
    tls = mc.get("tls", {})
    if tls.get("enabled", False):
        ca = tls.get("ca_certs", "")
        client.tls_set(ca_certs=ca if ca else None)
    client.on_connect = _on_connect_rcs
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect_async(mc.get("host", "localhost"), mc.get("port", 8883), keepalive=30)
    client.loop_start()
    return client


# ── HTML ──────────────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UGV Monitor</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{max-width:100vw;overflow-x:hidden}
body{background:#0a0a0a;color:#eaeaea;font-family:'JetBrains Mono',monospace;font-size:13px;
     background-image:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.15) 2px,rgba(0,0,0,.15) 4px)}
header{background:#111;border-bottom:1px solid #2a2a2a;padding:8px 16px;display:flex;
       align-items:center;gap:12px;flex-wrap:wrap;overflow:hidden}
.brand{color:#c8a84b;font-weight:600;font-size:1rem;border:1px solid #c8a84b;padding:2px 8px;
       white-space:nowrap;flex-shrink:0}
.pill{display:flex;align-items:center;gap:6px;font-size:.8rem;color:#666;white-space:nowrap;flex-shrink:0}
.sq{width:10px;height:10px;background:#e61919;flex-shrink:0}
.sq.on{background:#4af626}
.cnt{color:#c8a84b;font-size:.8rem;margin-left:auto;white-space:nowrap;flex-shrink:0}
/* 2-column grid — columns share available width equally, never overflow */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#2a2a2a;margin:1px}
.panel{background:#111;padding:12px;min-width:0;overflow:hidden}
.ph{color:#555;font-size:.7rem;letter-spacing:.1em;border-bottom:1px solid #1e1e1e;
    padding-bottom:5px;margin-bottom:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row{display:flex;justify-content:space-between;align-items:center;padding:3px 0;
     border-bottom:1px solid #161616;min-width:0}
.lbl{color:#555;font-size:.75rem;white-space:nowrap;flex-shrink:0;margin-right:6px}
.val{font-size:.85rem;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;
     text-overflow:ellipsis}
.gold{color:#c8a84b} .grn{color:#4af626} .red{color:#e61919} .warn{color:#e6a817}
/* XY pad — scales with panel width, capped at 180px */
#xy{position:relative;width:min(180px,100%);aspect-ratio:1/1;background:#080808;
    border:1px solid #222;margin:8px auto}
#xydot{position:absolute;width:6px;height:6px;background:#c8a84b;left:50%;top:50%;
       transform:translate(-50%,-50%)}
.gl{position:absolute;background:#1e1e1e}
.glh{left:0;right:0;top:50%;height:1px}
.glv{top:0;bottom:0;left:50%;width:1px}
.bw{background:#161616;height:7px;margin:3px 0}
.bf{height:100%;background:#c8a84b;transition:width .06s}
.sep{margin:8px 0 6px;border-top:1px solid #1e1e1e}
.btns{display:flex;flex-wrap:wrap;gap:3px;margin-top:6px}
.btn{width:40px;height:40px;background:#181818;border:1px solid #222;color:#444;font-size:.6rem;
     display:flex;align-items:center;justify-content:center;text-align:center;line-height:1.2;
     flex-shrink:0}
.btn.on{background:#4af626;color:#000;border-color:#4af626}
/* latency bar */
.rtt-val{font-size:1.1rem;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:.05em}
.rtt-stats{display:flex;gap:16px;margin-top:6px}
.rtt-stat{display:flex;flex-direction:column;align-items:center;gap:2px}
.rtt-stat-lbl{color:#444;font-size:.65rem}
.rtt-stat-val{font-size:.8rem;font-variant-numeric:tabular-nums;color:#888}
footer{background:#0d0d0d;border-top:1px solid #1e1e1e;padding:5px 16px;font-size:.7rem;color:#333;
       display:flex;gap:16px;font-variant-numeric:tabular-nums;overflow:hidden}
</style>
</head>
<body>
<header>
  <span class="brand">[ UGV MONITOR ]</span>
  <div class="pill"><div class="sq" id="mdot"></div><span id="mlbl">MQTT OFFLINE</span></div>
  <div class="pill"><div class="sq on"></div><span>MONITOR ACTIVE</span></div>
  <span class="cnt" id="rxc">RX 0 &nbsp;|&nbsp; 0.0 Hz</span>
</header>

<div class="grid">

  <!-- TOP-LEFT: Stick XY pad + axis values -->
  <div class="panel">
    <div class="ph">[ STICK &mdash; ROLL / PITCH ]</div>
    <div id="xy">
      <div class="gl glh"></div><div class="gl glv"></div>
      <div id="xydot"></div>
    </div>
    <div class="row"><span class="lbl">ROLL  X</span><span class="val gold" id="sx">+0.0000</span></div>
    <div class="row"><span class="lbl">PITCH Y</span><span class="val gold" id="sy">+0.0000</span></div>
    <div class="sep"></div>
    <div class="ph">[ H1 TRIM ]</div>
    <div id="h1hat" style="display:grid;grid-template-columns:repeat(3,28px);grid-template-rows:repeat(3,28px);gap:3px;margin:4px auto;width:max-content">
      <!-- row 0 -->
      <div></div>
      <div class="hat-cell" id="h1U" style="display:flex;align-items:center;justify-content:center;background:#161616;border:1px solid #222;font-size:.9rem;color:#444">&#x2191;</div>
      <div></div>
      <!-- row 1 -->
      <div class="hat-cell" id="h1L" style="display:flex;align-items:center;justify-content:center;background:#161616;border:1px solid #222;font-size:.9rem;color:#444">&#x2190;</div>
      <div style="display:flex;align-items:center;justify-content:center;background:#161616;border:1px solid #222;font-size:.75rem;color:#333">&#x25A0;</div>
      <div class="hat-cell" id="h1R" style="display:flex;align-items:center;justify-content:center;background:#161616;border:1px solid #222;font-size:.9rem;color:#444">&#x2192;</div>
      <!-- row 2 -->
      <div></div>
      <div class="hat-cell" id="h1D" style="display:flex;align-items:center;justify-content:center;background:#161616;border:1px solid #222;font-size:.9rem;color:#444">&#x2193;</div>
      <div></div>
    </div>
  </div>

  <!-- TOP-RIGHT: Throttle bars + Telemetry + Latency -->
  <div class="panel">
    <div class="ph">[ THROTTLE ]</div>
    <div class="row"><span class="lbl">LEFT  (ax 2)</span><span class="val gold" id="tl">0.0000</span></div>
    <div class="bw"><div class="bf" id="tlb" style="width:0%"></div></div>
    <div class="row"><span class="lbl">RIGHT (ax 5)</span><span class="val gold" id="tr">0.0000</span></div>
    <div class="bw"><div class="bf" id="trb" style="width:0%"></div></div>
    <div class="row"><span class="lbl">FRICTION (ax 6)</span><span class="val gold" id="tf">0.0000</span></div>
    <div class="bw"><div class="bf" id="tfb" style="width:0%"></div></div>

    <div class="sep"></div>
    <div class="ph">[ TELEMETRY ]</div>
    <div class="row"><span class="lbl">ARMED</span><span class="val" id="tel-armed">&#x2014;</span></div>
    <div class="row"><span class="lbl">BATTERY</span><span class="val gold" id="tel-bat">&#x2014;</span></div>
    <div class="row"><span class="lbl">SPEED</span><span class="val gold" id="tel-spd">&#x2014;</span></div>
    <div class="row"><span class="lbl">HB AGE</span><span class="val" id="tel-hb">&#x2014;</span></div>

    <div class="sep"></div>
    <div class="ph">[ LATENCY ]</div>
    <div class="row">
      <span class="lbl">RTT</span>
      <span class="rtt-val gold" id="rtt-cur">&#x2014;</span>
    </div>
    <div class="rtt-stats">
      <div class="rtt-stat"><span class="rtt-stat-lbl">MIN</span><span class="rtt-stat-val" id="rtt-min">&#x2014;</span></div>
      <div class="rtt-stat"><span class="rtt-stat-lbl">AVG</span><span class="rtt-stat-val" id="rtt-avg">&#x2014;</span></div>
      <div class="rtt-stat"><span class="rtt-stat-lbl">MAX</span><span class="rtt-stat-val" id="rtt-max">&#x2014;</span></div>
      <div class="rtt-stat"><span class="rtt-stat-lbl">SAMPLES</span><span class="rtt-stat-val" id="rtt-n">0/20</span></div>
    </div>
  </div>

  <!-- BOTTOM-LEFT: Stick buttons -->
  <div class="panel">
    <div class="ph">[ STICK BUTTONS ]</div>
    <div class="btns" id="sbw"></div>
  </div>

  <!-- BOTTOM-RIGHT: Throttle buttons -->
  <div class="panel">
    <div class="ph">[ THROTTLE BUTTONS ]</div>
    <div class="btns" id="tbw"></div>
  </div>

</div>
<footer>
  <span id="lrx">no data yet</span>
  <span id="raw" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
</footer>

<script>
// Button maps — ASCII labels only, max 4 chars
const SB=[
  {c:'288',l:'TG1'},{c:'289',l:'WR'},{c:'290',l:'NS'},{c:'291',l:'S4'},
  {c:'292',l:'S1'},{c:'293',l:'TG2'},
  {c:'294',l:'H2U'},{c:'295',l:'H2R'},{c:'296',l:'H2D'},{c:'297',l:'H2L'},
  {c:'298',l:'H3U'},{c:'299',l:'H3R'},{c:'300',l:'H3D'},{c:'301',l:'H3L'},
  {c:'302',l:'H4U'},{c:'303',l:'H4R'},
  {c:'704',l:'H4D'},{c:'705',l:'H4L'},{c:'706',l:'H4P'}
];
const TB=[
  {c:'288',l:'SC'},{c:'289',l:'MSP'},
  {c:'290',l:'MS-U'},{c:'291',l:'MS-R'},{c:'292',l:'MS-D'},{c:'293',l:'MS-L'},
  {c:'294',l:'SB-U'},{c:'295',l:'SB-D'},
  {c:'296',l:'BS-U'},{c:'297',l:'BS-D'},
  {c:'298',l:'CH-U'},{c:'299',l:'CH-D'},
  {c:'704',l:'PSF'},{c:'705',l:'PSB'},{c:'706',l:'LTB'}
];

function mkBtns(list, id) {
  document.getElementById(id).innerHTML =
    list.map(b=>`<div class="btn" id="b${id}${b.c}">${b.l}</div>`).join('');
}
mkBtns(SB,'sbw');
mkBtns(TB,'tbw');

// fmt: bipolar signed (+/-)  — for stick axes
function fmt(v,d=4){return(v>=0?'+':'')+Number(v).toFixed(d);}
// fmtU: unipolar, no sign    — for throttle axes
function fmtU(v,d=4){return Number(v).toFixed(d);}
function pct(v){return Math.max(0,Math.min(100,v*100))+'%';}

// hatActive: returns true if hat [x,y] is in direction dir
function hatActive(hat,dir){
  if(!hat)return false;
  const x=hat[0],y=hat[1];
  if(dir==='up')    return y===-1;
  if(dir==='down')  return y===1;
  if(dir==='left')  return x===-1;
  if(dir==='right') return x===1;
  return false;
}
function applyHat(hat){
  const dirs=['up','down','left','right'];
  const cells={up:'h1U',down:'h1D',left:'h1L',right:'h1R'};
  dirs.forEach(dir=>{
    const el=document.getElementById(cells[dir]);
    if(!el)return;
    if(hatActive(hat,dir)){
      el.style.background='#c8a84b';el.style.color='#000';el.style.borderColor='#c8a84b';
    } else {
      el.style.background='#161616';el.style.color='#444';el.style.borderColor='#222';
    }
  });
}

// RTT color thresholds
function rttClass(ms){
  if(ms<100) return 'grn';
  if(ms<=300) return 'warn';
  return 'red';
}

let rxTimes=[], rxTotal=0;
let rttHistory=[];          // last 20 RTT samples

function onJoystick(d,n) {
  rxTotal=n;
  const now=Date.now();
  rxTimes.push(now);
  rxTimes=rxTimes.filter(t=>now-t<2000);
  const hz=(rxTimes.length/2).toFixed(1);
  document.getElementById('rxc').textContent='RX '+n+' | '+hz+' Hz';
  document.getElementById('lrx').textContent='last rx: '+new Date().toLocaleTimeString();
  document.getElementById('raw').textContent=JSON.stringify(d).slice(0,160);

  const sa=d.sa||{}, ta=d.ta||{}, sb=d.sb||{}, tb=d.tb||{}, sh=d.sh||{}, th=d.th||{};
  const sx=sa['0']??0, sy=sa['1']??0;
  const tl=ta['2']??0, tr=ta['5']??0, tf=ta['6']??0;

  document.getElementById('sx').textContent=fmt(sx);
  document.getElementById('sy').textContent=fmt(sy);

  // Unipolar throttle values — no sign prefix
  document.getElementById('tl').textContent=fmtU(tl);
  document.getElementById('tlb').style.width=pct(tl);
  document.getElementById('tr').textContent=fmtU(tr);
  document.getElementById('trb').style.width=pct(tr);
  document.getElementById('tf').textContent=fmtU(tf);
  document.getElementById('tfb').style.width=pct(tf);

  const dot=document.getElementById('xydot');
  dot.style.left=(50+sx*50)+'%';
  dot.style.top=(50+sy*50)+'%';

  SB.forEach(b=>{
    const el=document.getElementById('bsbw'+b.c);
    if(el) el.className='btn'+(sb[b.c]?' on':'');
  });
  TB.forEach(b=>{
    const el=document.getElementById('btbw'+b.c);
    if(el) el.className='btn'+(tb[b.c]?' on':'');
  });

  applyHat(sh['H1']||null);
}

function onTelemetry(d) {
  const arm=d.armed;
  const ta=document.getElementById('tel-armed');
  ta.textContent=arm?'ARMED':'DISARMED';
  ta.className='val '+(arm?'grn':'red');
  document.getElementById('tel-bat').textContent=
    d.bat_pct!=null?d.bat_pct.toFixed(1)+'%':'—';
  document.getElementById('tel-spd').textContent=
    d.speed!=null?d.speed.toFixed(2)+' m/s':'—';
  const hbEl=document.getElementById('tel-hb');
  if(d.hb_age!=null){
    const age=d.hb_age.toFixed(0);
    hbEl.textContent=age+' ms';
    hbEl.className='val '+(d.hb_age>2000?'red':d.hb_age>1200?'warn':'grn');
  } else {
    hbEl.textContent='—';
    hbEl.className='val';
  }
}

function onLatency(rtt) {
  rttHistory.push(rtt);
  if(rttHistory.length>20) rttHistory.shift();

  const min=Math.min(...rttHistory);
  const max=Math.max(...rttHistory);
  const avg=Math.round(rttHistory.reduce((a,b)=>a+b,0)/rttHistory.length);
  const cls=rttClass(rtt);

  const curEl=document.getElementById('rtt-cur');
  curEl.textContent=rtt+' ms';
  curEl.className='rtt-val '+cls;

  document.getElementById('rtt-min').textContent=min+' ms';
  document.getElementById('rtt-avg').textContent=avg+' ms';
  document.getElementById('rtt-max').textContent=max+' ms';
  document.getElementById('rtt-n').textContent=rttHistory.length+'/20';
}

function connect() {
  const es=new EventSource('/events');
  es.onmessage=e=>{
    const m=JSON.parse(e.data);
    if(m.type==='status'){
      document.getElementById('mdot').className='sq'+(m.connected?' on':'');
      document.getElementById('mlbl').textContent=
        m.connected?'MQTT CONNECTED':'MQTT OFFLINE';
    } else if(m.type==='joystick'){
      onJoystick(m.data,m.n);
    } else if(m.type==='telemetry'){
      onTelemetry(m.data);
    } else if(m.type==='latency'){
      onLatency(m.rtt);
    }
  };
  es.onerror=()=>{es.close();setTimeout(connect,2000);};
}
connect();
</script>
</body>
</html>"""


# ── HTTP server ───────────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            body = _HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q: queue.Queue = queue.Queue(maxsize=64)
            with _sse_lock:
                _sse_queues.append(q)

            # Send current MQTT status immediately
            with _state_lock:
                init = {"type": "status", "connected": _state["connected"]}
            try:
                self.wfile.write(f"data: {json.dumps(init)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ka\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                with _sse_lock:
                    if q in _sse_queues:
                        _sse_queues.remove(q)
        else:
            self.send_response(404)
            self.end_headers()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("MONITOR_PORT", "8080"))
    mqtt_client = _start_mqtt()
    rcs_client = _start_rcs_mqtt()
    print(f"UGV Monitor → http://0.0.0.0:{port}")
    print(f"Open in browser: http://<PI_IP>:{port}")
    server = HTTPServer(("0.0.0.0", port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        if rcs_client is not None:
            rcs_client.loop_stop()
            rcs_client.disconnect()
