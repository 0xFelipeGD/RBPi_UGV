# RBPi_UGV — Bugs Fixed

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
