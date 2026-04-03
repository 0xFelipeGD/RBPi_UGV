# RBPi_UGV — Bugs Fixed

## 2026-04-03 — monitor.py Hat Direction Re-fix + SB CMS Button Labels

### BUG-014: H1 TRIM hat y-axis still inverted after BUG-013 patch
- **File:** `monitor.py` (`_HTML` — JS `hatActive` function, line ~355)
- **Severity:** Low (cosmetic — UP arrow lit when hat is pressed DOWN and vice versa)
- **Problem:** BUG-013 applied the wrong polarity. Linux evdev ABS_HAT0Y sends **-1 when the hat is pushed UP** and **+1 when pushed DOWN** (screen-coordinate Y-down convention). The RCS `input_node.py` stores raw evdev values with no inversion, so `sh.H1=[0,-1]` means hat pushed UP. The JS `hatActive` function had `up = y===1` (wrong) and `down = y===-1` (wrong), so pressing UP lit the DOWN cell.
- **Fix:** Corrected to `up = y===-1` and `down = y===1`, matching the evdev/INTERFACE_CONTRACT convention where y=-1 is UP.

### BUG-015: SB array missing H4D / H4L / H4P, and wrong labels for H4U / H4R
- **File:** `monitor.py` (`_HTML` — JS `const SB` array, line ~327)
- **Severity:** Low (cosmetic — CMS Down/Left/Press buttons never lit; Up/Right buttons showed wrong labels 'CMS'/'CM+')
- **Problem:** The last two entries in `SB` were `{c:'302',l:'CMS'}` and `{c:'303',l:'CM+'}`. Per `hotas_mappings.py`, code 302 = H4U (CMS Up) and 303 = H4R (CMS Right). Codes 704 (H4D), 705 (H4L), and 706 (H4P) were entirely absent, so those button presses were received but never highlighted.
- **Fix:** Corrected labels for codes 302 and 303 to `H4U` and `H4R`, and added the three missing entries: `{c:'704',l:'H4D'}`, `{c:'705',l:'H4L'}`, `{c:'706',l:'H4P'}`.

## 2026-04-03 — monitor.py Dual MQTT Client + Hat Direction Fix

### BUG-012: RTT always 0 samples due to ACL — ugv_client cannot read ugv/pong
- **File:** `monitor.py` (Python backend)
- **Severity:** High (RTT panel always showed 0/20 samples; latency monitoring non-functional)
- **Problem:** The monitor connected to the broker as `ugv_client`. Per the broker ACL, `ugv_client` has write-only access to `ugv/pong` and no read access. Subscribing to `ugv/pong` was silently rejected by the broker, so pong messages never arrived and `_ping_times` entries were never consumed, keeping the RTT sample count at zero permanently. The same ACL issue applied to `ugv/telemetry` (write-only for `ugv_client`), though telemetry was already wired up — it just never received data.
- **Fix:** Split the single MQTT connection into two independent paho clients within the same monitor process:
  - `client_ugv` (`ugv-monitor`, credentials from `mqtt.username`/`mqtt.password`): subscribes to `ugv/joystick` and `ugv/ping` — topics `ugv_client` is permitted to read.
  - `client_rcs` (`ugv-monitor-rcs`, credentials from `mqtt.rcs_username`/`mqtt.rcs_password` or `mqtt.rcs.username`/`mqtt.rcs.password`): subscribes to `ugv/pong` and `ugv/telemetry` — topics `rcs_operator` is permitted to read.
  - Added `_on_connect_rcs()` callback that subscribes the rcs client to the correct topics on connect.
  - Added `_start_rcs_mqtt()` factory function that reads `rcs_username`/`rcs_password` from config. If `rcs_password` is empty or absent, prints `[WARN] rcs_password not set in config — pong/telemetry monitoring disabled` and returns `None` (graceful degradation — ugv joystick monitoring still works).
  - Both clients share the same `_on_disconnect` and `_on_message` handlers; topic routing inside `_on_message` is unchanged since it matches on topic string.
  - `__main__` starts both clients and disconnects both on shutdown.

### BUG-013: H1 TRIM hat y-axis inverted (up arrow lit for y=-1 instead of y=+1)
- **File:** `monitor.py` (`_HTML` — JS `hatActive` function)
- **Severity:** Low (cosmetic — directional arrows showed opposite direction to physical hat press)
- **Problem:** The `hatActive` JS function had `up = y === -1` and `down = y === 1`. Per the INTERFACE_CONTRACT, `sh.H1 = [0, -1]` means y=-1 which the RCS displays as DOWN (bottom arrow active). The monitor was lighting the UP arrow for that same value.
- **Fix:** Changed to `up = y === 1` (positive y = up direction) and `down = y === -1` (negative y = down direction), matching the RCS convention documented in INTERFACE_CONTRACT.md.

## 2026-04-03 — monitor.py Three Bug Fixes

### BUG-009: XY pad Y axis inverted
- **File:** `monitor.py` (`_HTML` — JS `onJoystick`)
- **Severity:** Medium (cosmetic/UX — dot moved opposite to stick)
- **Problem:** `dot.style.top = (50 - sy * 50) + '%'` placed the dot at the top when `sy` was positive (stick pushed forward). In screen coordinates, `top: 0%` is the visual top, so the formula was correct for a "Y-up = screen-up" convention. However the RCS MAPPING tab displays Y = -0.657 (stick pulled back) in the upper portion of its XY pad — meaning negative Y maps to the top on screen. The formula was inverted relative to that convention.
- **Fix:** Changed to `dot.style.top = (50 + sy * 50) + '%'` so that negative sy (pulled back) moves the dot toward the top of the pad, matching the RCS display.

### BUG-010: H1 TRIM hat missing from stick panel
- **File:** `monitor.py` (`_HTML` — HTML + JS)
- **Severity:** Low (missing feature — trim hat inputs invisible)
- **Problem:** The top-left panel only showed the XY pad and axis value rows. The H1 TRIM hat (`d.sh["H1"]`) was received in every joystick message but never displayed.
- **Fix:** Added a `[ H1 TRIM ]` section inside the top-left panel (after the PITCH Y row, before the panel closing `</div>`). Uses a 3x3 CSS grid with corner cells empty, forming a cross: up (h1U), left (h1L), center (static `■`), right (h1R), down (h1D). Added `hatActive(hat, dir)` helper (maps x,y ∈ {-1,0,1} to direction booleans) and `applyHat(hat)` which updates background/color of the four directional cells to gold (`#c8a84b`) when active, dark when inactive. Called as `applyHat(sh['H1']||null)` at the end of `onJoystick`.

### BUG-011: RTT calculation used mismatched wall clocks (RCS vs Pi)
- **File:** `monitor.py` (Python backend)
- **Severity:** High (incorrect data — RTT could be negative or absurdly large without NTP)
- **Problem:** `rtt_ms = int(time.time() * 1000) - payload["t"]` subtracted the RCS's Unix timestamp from the Pi's Unix timestamp. Without NTP synchronization the clocks can differ by seconds or more, producing meaningless RTT values.
- **Fix:** Switched to a round-trip measurement using only the Pi's monotonic clock:
  1. Added module-level `_ping_times: dict[int, float] = {}` to store ping arrival times.
  2. `_on_connect` now subscribes to `cfg["topics"]["latency_ping"]` (default `"ugv/ping"`) in addition to pong.
  3. When a ping arrives, `_ping_times[seq] = time.monotonic() * 1000` is stored; stale entries older than 10 s are pruned.
  4. When a pong arrives, `t_sent = _ping_times.pop(seq, None)` is looked up; if found, `rtt_ms = time.monotonic()*1000 - t_sent` is pushed as `{"type":"latency","rtt":round(rtt_ms,1)}`. If not found (ping missed), RTT is skipped rather than pushing a garbage value.

## 2026-04-03 — monitor.py UI Fixes and Latency Panel

### BUG-004: Panel headers cut off due to grid overflow
- **File:** `monitor.py` (`_HTML`)
- **Severity:** Medium (cosmetic — headers showed "K - ROLL / PITCH ]" instead of full text)
- **Problem:** The `.grid` children had no `min-width: 0` constraint so flex/grid blowout caused panels to overflow the viewport and clip their content.
- **Fix:** Added `min-width: 0; overflow: hidden` to `.panel`; added `max-width: 100vw; overflow-x: hidden` to `html, body`; added `white-space: nowrap; overflow: hidden; text-overflow: ellipsis` to `.ph` headers. Used `&mdash;` HTML entity for the em-dash in the header to avoid raw Unicode issues.

### BUG-005: Throttle values displayed with bipolar sign prefix
- **File:** `monitor.py` (`_HTML` — JS section)
- **Severity:** Low (incorrect display — unipolar 0.0..1.0 values showed "+0.0000")
- **Problem:** The single `fmt(v)` function unconditionally prepended `+` for non-negative values. LEFT/RIGHT throttle and FRICTION are unipolar axes (0.0..1.0) and should not show a sign.
- **Fix:** Added `fmtU(v)` function that returns `Number(v).toFixed(4)` without any sign prefix. Applied `fmtU` to `tl`, `tr`, `tf` display values; kept `fmt` (signed) for stick axes `sx`, `sy` only.

### BUG-006: Button labels used Unicode arrow characters
- **File:** `monitor.py` (`_HTML` — JS button arrays)
- **Severity:** Low (rendering — Unicode arrows may not render on all Pi/browser combos)
- **Problem:** SB and TB button arrays used Unicode arrows (↑→↓←) which can fail to render on embedded/minimal browser setups, showing boxes or garbled characters.
- **Fix:** Replaced all Unicode arrows with explicit ASCII direction codes: H2U/H2R/H2D/H2L, H3U/H3R/H3D/H3L, MS-U/MS-R/MS-D/MS-L, SB-U/SB-D, BS-U/BS-D, CH-U/CH-D. All labels are max 4 characters to fit 40x40px buttons.

### FEAT-001: Added latency (RTT) panel and pong MQTT subscription
- **File:** `monitor.py` (Python backend + `_HTML`)
- **Problem:** No RTT visibility in the monitor — operator had no way to see link latency.
- **Implementation:**
  - Backend: `_on_connect` now subscribes to `cfg["topics"]["latency_pong"]` (default `"ugv/pong"`, QoS 0).
  - Backend: `_on_message` handles pong topic by computing `rtt_ms = int(time.time()*1000) - payload["t"]` and pushing `{"type":"latency","rtt":rtt_ms}` via SSE.
  - HTML: Added `[ LATENCY ]` panel inside the top-right panel (below telemetry). Shows current RTT in ms with color coding: green < 100 ms, gold 100-300 ms, red > 300 ms. Shows MIN/AVG/MAX/sample-count over a rolling window of the last 20 pong samples (`rttHistory` array in JS).
  - JS: `onLatency(rtt)` function maintains `rttHistory` (max 20 entries), updates all RTT display elements on each latency SSE event.

## 2026-04-03 — First Deployment Bugs

### BUG-007: clean_session=True caused rapid disconnect loop with persistent broker
- **File:** `mqtt/mqtt_bridge.py`
- **Severity:** High (MQTT connect/disconnect loop every ~1–2 s, vehicle inoperable)
- **Problem:** `mqtt.Client()` was constructed with `clean_session=True` while the broker has persistence enabled and the UGV uses a fixed client_id (`ugv-onboard`) with QoS 1 telemetry. On every reconnect the broker destroyed and immediately recreated the persistent session, causing queued QoS 1 messages to trigger a session-takeover cycle that disconnected the client within seconds.
- **Fix:** Changed to `clean_session=False` so the broker preserves the QoS 1 session across reconnects. Also added `reconnect_delay_set(min_delay=1, max_delay=30)` to prevent broker hammering during rapid reconnects.

### BUG-008: Double instance (systemd service + manual run) causes session takeover
- **File:** `setup.sh` (installs and enables the systemd service by default)
- **Severity:** High (both processes share client_id `ugv-onboard`; broker disconnects one immediately, causing a reconnect storm)
- **Problem:** `setup.sh` runs `systemctl enable --now ugv`, so the service is already active after first setup. If the user then also runs `bash run.sh` manually, two processes connect with the same `client_id`, triggering continuous broker-side session takeover and disconnect loops.
- **Fix/Workaround:** Stop the systemd service before running manually: `sudo systemctl stop ugv`, then `bash run.sh`. Alternatively, disable auto-start: `sudo systemctl disable ugv`.

## 2026-04-03 — MQTT Session and Reconnect Fixes

### BUG-002: clean_session=True destroyed QoS 1 telemetry queue on every reconnect
- **File:** `mqtt/mqtt_bridge.py`
- **Severity:** High (root cause of rapid disconnect/reconnect loop)
- **Problem:** `mqtt.Client()` was constructed with `clean_session=True`. The Pi uses a fixed client ID (`ugv-onboard`) and publishes telemetry at QoS 1. On every reconnect the broker discarded the persistent session and re-created it clean, which caused queued QoS 1 messages to be lost and triggered another cycle.
- **Fix:** Changed to `clean_session=False` so the broker preserves the session and its QoS 1 queues across reconnects.

### BUG-003: Missing reconnect backoff caused broker hammering
- **File:** `mqtt/mqtt_bridge.py`
- **Severity:** High (amplified disconnect loop)
- **Problem:** Without an explicit reconnect delay, paho's default behaviour is to retry immediately and very rapidly, saturating the broker with connection attempts and making the disconnect loop worse.
- **Fix:** Added `self._client.reconnect_delay_set(min_delay=1, max_delay=30)` immediately after the client constructor so paho uses exponential backoff (1 s → 30 s max).

## 2026-04-02 — Integration Audit

### BUG-001: config.yaml.example wrong MQTT username
- **File:** `config/config.yaml.example`
- **Severity:** Low (documentation only)
- **Problem:** Example config had `username: "ugv_user"` but the broker setup (`Mosquitto-Broker-RBPi/setup.sh`) creates the user as `ugv_client`. New users following the example would fail to authenticate.
- **Fix:** Changed to `username: "ugv_client"` with note "(must match broker)".

### Known Issues (not bugs — design limitations):

1. **RSSI hardcoded to 0** — `telemetry/telemetry_node.py` always publishes `rssi: 0` because no WiFi/cellular signal reader is implemented.
2. **Modbus backend stub** — `serial_plc` backend config allows `protocol: "modbus"` but only JSON protocol is implemented. Modbus requires PLC firmware integration.
3. **PCA9685 frequency** — If `ServoKit` import fails and low-level API is used, PWM frequency may not apply correctly.
