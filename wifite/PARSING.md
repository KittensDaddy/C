# wifite2 output parsing — reference

Ground-truth notes for `output.py`, taken from the **pinned** kimocoder/wifite2
commit `cc3c82f8f2f674b4e355a58f15555a63e25f175a` (what `install.sh` installs).
The local `~/wifite2` checkout may be a *different* commit — always verify against
the pinned one.

## The one contract that matters: `Color.pattack()`

Nearly every live attack line — WPA, WPA3, WPS, PMKID, OWE, WEP, Evil Twin —
is printed by `Color.pattack(attack_type, target, attack_name, progress)`
(`wifite/util/color.py:138`). After ANSI strip it renders as:

```
[+] <ESSID> (<POWER>db) <TYPE> <ATTACK_NAME>: <PROGRESS>
```

- Leading marker is `[+]` (from `{+}` → ` [W][D][[W][G]+...` → `[+]`). **Must be
  consumed**, or it gets swallowed into the ESSID.
- Power is `db` (lowercase) and can be the literal `??` when unknown.
- ESSID renders as `unknown` when `essid_known` is false.
- `TYPE` ∈ `{WPA, WPA3, WPS, WEP, PMKID, OWE, Evil Twin}` — **not just WPS/WPA/WEP**.

`output.py` matches this first via `OutputParser.PATTACK` → `_parse_pattack()`.

### ATTACK_NAME / PROGRESS values seen per type

| TYPE  | ATTACK_NAME              | PROGRESS examples |
|-------|-------------------------|-------------------|
| WPA   | `Handshake capture`     | `Waiting for target to appear...`, `Listening. (clients:2, deauth:yes, timeout:03:42)`, `Captured handshake` |
| WPS   | `Pixie-Dust` / `PIN Attack` / `NULL PIN` | `[MM:SS] Sending M4 message`, `[MM:SS] PINs:120 Trying 12345670`, `[MM:SS] Cracked WPS PIN: <pin> PSK: <psk>`, `Failed: Target timeout: ...` |
| PMKID | `CAPTURE` / `CRACK` / `CRACKED` | `Waiting for PMKID (MM:SS)`, `Captured PMKID`, `Cracking PMKID using <wl> ...`, `Key: <psk>` |
| WPA3  | `SAE Capture` / `Downgrade` / `Passive Capture` | `Capturing...`, `Waiting for target to appear...` |
| OWE   | `Capture` / `Passive` / `Downgrade` | `Deauthing clients...`, `Capturing OWE key exchange...` |
| WEP   | (varies)                | IV counts etc. |

Sources: `attack/wpa.py`, `attack/pmkid.py`, `tools/reaver.py:464`,
`attack/wps.py`, `attack/wpa3.py`, `attack/owe.py`.

## Non-pattack lines we also parse

| What | Real format (post-ANSI) | Source | Event |
|------|-------------------------|--------|-------|
| Attack start | `[+] (N/M) Starting attacks against <bssid> (<essid>)` — essid may be `ESSID unknown` | `attack/all.py:72` | `TARGET` (current/total) |
| Target found | `[+] found target <bssid> (<essid>)` | `util/scanner.py:209` | `TARGET` |
| Bare PMKID | `[+] Captured PMKID` | `attack/pmkid.py` | `PMKID` |
| WPA fail | `[!] WPA handshake capture FAILED: Timed out after N seconds` | `attack/wpa.py:803/1212` | `FAILED` |
| Deauth (native) | `[+] Scapy: sent N deauth packets to <bssid>` | `tools/aireplay.py:457` | `DEAUTH` (current=N) |
| Final summary keys | `PSK (password): <psk>`, `Key: <psk>`, `PIN: <pin>` under `Cracked WPA/WPS/WEP:` blocks | `model/*result*.py`, `model/result.py:232` | `CRACKED` |

## Cracked-key detection (three paths, all covered)

1. WPS inline: `Cracked WPS PIN: <pin> PSK: <psk>` → credential = PSK (fallback PIN).
2. PMKID/WPA/SAE inline via pattack: `... CRACKED: Key: <psk>` or any progress with `Key: <psk>`.
3. Final-summary block: standalone `Key:` / `PSK (password):` line.

## Gotchas / deliberate choices

- **Don't treat `timeout:MM:SS` as failure.** WPA "Listening" progress contains
  `timeout:03:42` (a benign countdown). Failure detection keys on
  `FAILED` / `Failed:` / `Target timeout` only — never a bare `timeout`.
- The `[MM:SS]` / `timeout:MM:SS` in progress is what the LCD countdown
  (`ui/screens.py:_timeout`) reads live.
- No reliable single "Found N targets" scan-count line exists in this commit —
  scanning is a redrawn airodump-style table. `SCAN` events rarely fire; the
  attack screen relies on per-target `TARGET`/phase events instead.
- Attack types `WPA3`, `OWE`, `Evil Twin`, `pmkid_passive` exist in the pinned
  commit but **not** in the older local `~/wifite2` checkout.

## How to re-verify after a wifite2 bump

Render real lines through wifite's own `Color`, strip ANSI, feed to `OutputParser`:

```python
import importlib.util
def load(n,p):
    s=importlib.util.spec_from_file_location(n,p); m=importlib.util.module_from_spec(s)
    s.loader.exec_module(m); return m
Color = load("wc","<wifite2>/wifite/util/color.py").Color
OP    = load("wo","/home/sun/C/wifite/output.py").OutputParser
# build "[+] <essid> (<pow>db) <TYPE> <name>: <progress>" lines, then OP()._parse(line)
```
A 20-case matrix covering every type currently passes 20/20.
