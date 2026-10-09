"""Exact PR203 baseline vs UI207 browser-geometry and PNG screenshots.

Only local HTML/CSS used: no live backend, authentication, JS polling or API.
Chrome/Chromium must exist. Running browser tests is mandatory, not a skip.
"""
import hashlib
import os
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
FRONT=ROOT/"senecio_polymarket"/"frontend"
BASE="ee60891e5f403914a6e9958f581c81e3192fa264"
SIZES=[(852,1009),(1440,900),(1366,768),(1280,800),(1024,768),(900,900),(850,900),(390,844)]
JS=r"""<script>window.addEventListener('load',()=>{
let names=['score-panel','predictions-panel','poly-panel','kalshi-panel'];
let boxes={};for(let n of names){let r=document.getElementById(n).getBoundingClientRect();
boxes[n]={x:r.x,y:r.y,right:r.right,bottom:r.bottom,h:r.height,w:r.width};}
let overlap=[];for(let i=0;i<names.length;i++)for(let j=i+1;j<names.length;j++){
let a=boxes[names[i]],b=boxes[names[j]];
if(Math.min(a.right,b.right)-Math.max(a.x,b.x)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1)
overlap.push(names[i]+' / '+names[j]);}
let main=document.querySelector('main'),table=document.querySelector('#predictions-panel .table-wrap');
let result={viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,overlap,boxes,
pageWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,
mainScrollWidth:main.scrollWidth,mainClientWidth:main.clientWidth,
sidebarVisible:document.getElementById('sidebar').getBoundingClientRect().width>0,
gridCols:getComputedStyle(main).gridTemplateColumns,tableHeight:table.clientHeight,
tableScrollHeight:table.scrollHeight,tableOverflowY:getComputedStyle(table).overflowY};
let pre=document.createElement('pre');pre.id='ui207-geometry';pre.textContent=JSON.stringify(result);
pre.style.display='none';document.body.append(pre);
});</script>"""

class Extract(HTMLParser):
 def __init__(self):super().__init__();self.inside=False;self.chunks=[]
 def handle_starttag(self,tag,attrs):
  if tag=="pre" and dict(attrs).get("id")=="ui207-geometry":self.inside=True
 def handle_endtag(self,tag):
  if tag=="pre":self.inside=False
 def handle_data(self,t):
  if self.inside:self.chunks.append(t)

def geometry(browser,folder,html,css,size):
 folder.mkdir()
 (folder/"styles.css").write_bytes(css)
 page=re.sub(r'<link[^>]+href="/static/styles.css[^>]*>','<link rel="stylesheet" href="styles.css">',html)
 page=re.sub(r'<script[^>]+src="/static/[^>]+></script>','',page)
 (folder/"index.html").write_text(page.replace("</body>",JS+"</body>"),encoding="utf8")
 w,h=size
 flags=["--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage",
        "--no-first-run","--force-device-scale-factor=1",f"--window-size={w},{h}"]
 url=(folder/"index.html").resolve().as_uri()
 r=subprocess.run([browser,*flags,"--dump-dom",url],capture_output=True,text=True,timeout=45)
 assert r.returncode==0,r.stderr[-600:]
 parser=Extract();parser.feed(r.stdout)
 assert parser.chunks,"Browser geometry probe did not run"
 data=json.loads("".join(parser.chunks))
 png=folder/"screenshot.png"
 shot=subprocess.run([browser,*flags,f"--screenshot={png}",url],capture_output=True,timeout=45)
 assert shot.returncode==0 and png.exists(),shot.stderr[-400:]
 data["png_sha256"]=hashlib.sha256(png.read_bytes()).hexdigest()
 return data

def _persist_visual_evidence(folder, size, red, green, variant="normal"):
 evidence_dir=os.environ.get("UI207_EVIDENCE_DIR")
 if not evidence_dir:
  return
 out=Path(evidence_dir)
 out.mkdir(parents=True,exist_ok=True)
 stem=f"{variant}_{size[0]}x{size[1]}"
 for name in ("red","green"):
  src=folder/name/"screenshot.png"
  dst=out/f"{stem}_{name}.png"
  shutil.copyfile(src,dst)
  assert hashlib.sha256(dst.read_bytes()).hexdigest()==(red if name=="red" else green)["png_sha256"]
 (out/f"{stem}_geometry.json").write_text(
  json.dumps({"size":list(size),"variant":variant,"baseline":BASE,
              "red":red,"green":green},sort_keys=True,indent=2)+"\n",encoding="utf8")


@pytest.mark.parametrize("size",SIZES)
def test_no_overlapping_cards_with_sidebar_and_table_scroll(tmp_path,size):
 browser=next((shutil.which(x) for x in ("google-chrome","chromium","chromium-browser","google-chrome-stable") if shutil.which(x)),None)
 assert browser,"Browser missing: visual regression is BLOCKED, never silently skipped"
 original=(FRONT/"index.html").read_text(encoding="utf8")
 red_css=subprocess.check_output(["git","show",BASE+":senecio_polymarket/frontend/styles.css"],cwd=ROOT)
 current=(FRONT/"styles.css").read_bytes()
 red=geometry(browser,tmp_path/"red",original,red_css,size)
 green=geometry(browser,tmp_path/"green",original,current,size)
 _persist_visual_evidence(tmp_path,size,red,green)
 print("UI207",size,"RED",red["overlap"],"GREEN",green["overlap"],
       "PNG_SHA_RED",red["png_sha256"],"PNG_SHA_GREEN",green["png_sha256"])
 assert green["sidebarVisible"],green
 assert not green["overlap"],green
 assert green["pageWidth"]<=green["viewport"][0]+2,green
 assert green["bodyWidth"]<=green["viewport"][0]+2,green
 assert green["mainScrollWidth"]<=green["mainClientWidth"]+2,green
 assert green["tableHeight"]<=370,green
 if size==(852,1009):
  assert red["overlap"],"Baseline did not reproduce screenshot-overlap; investigate zoom/DPR"
  assert len(green["gridCols"].split())==1,green

def test_oracle_table_many_rows_scrolls_with_no_card_collision(tmp_path):
 browser=next((shutil.which(x) for x in ("google-chrome","chromium","chromium-browser","google-chrome-stable") if shutil.which(x)),None)
 assert browser,"Browser missing: visual regression is BLOCKED, never silently skipped"
 original=(FRONT/"index.html").read_text(encoding="utf8")
 rows="".join(
  "<tr><td>2026-10-09T00:00:00Z</td><td>BTC</td><td>UP</td>"
  "<td>0.512</td><td>synthetic</td><td>unknown</td><td>—</td></tr>"
  for _ in range(72)
 )
 placeholder='<tbody><tr><td colspan="7" class="placeholder">loading…</td></tr></tbody>'
 assert original.count(placeholder)==1, "Supabase tbody fixture selector changed"
 crowded=original.replace(placeholder,"<tbody>"+rows+"</tbody>")
 base_css=subprocess.check_output(["git","show",BASE+":senecio_polymarket/frontend/styles.css"],cwd=ROOT)
 latest=(FRONT/"styles.css").read_bytes()
 size=(852,1009)
 red=geometry(browser,tmp_path/"red",crowded,base_css,size)
 green=geometry(browser,tmp_path/"green",crowded,latest,size)
 _persist_visual_evidence(tmp_path,size,red,green,"72rows")
 assert red["overlap"],"Baseline should demonstrate collision even with crowded table"
 assert not green["overlap"],green
 assert green["sidebarVisible"],green
 assert green["tableScrollHeight"]>green["tableHeight"],green
 assert green["tableOverflowY"] in ("auto","scroll"),green
 assert green["tableHeight"]<=370,green
 assert green["pageWidth"]<=green["viewport"][0]+2,green
 assert green["mainScrollWidth"]<=green["mainClientWidth"]+2,green


# Real 390 CSS px browser viewport: Chrome --window-size clamps narrow widths,
# so this test MUST use CDP Emulation.setDeviceMetricsOverride instead.
import base64 as _base64
import os as _os
import socket as _socket
import struct as _struct
import tempfile as _tempfile
import time as _time
import urllib.request as _urlrequest


class _CDPWire:
    def __init__(self, endpoint):
        from urllib.parse import urlparse
        u=urlparse(endpoint)
        self.conn=_socket.create_connection((u.hostname,u.port),timeout=10)
        self.conn.settimeout(12)
        self.buf=b""
        self.counter=0
        seed=_base64.b64encode(_os.urandom(16)).decode("ascii")
        target=u.path+("?" + u.query if u.query else "")
        wire=(f"GET {target} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
              f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
              f"Sec-WebSocket-Key: {seed}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode()
        self.conn.sendall(wire)
        response=b""
        while b"\r\n\r\n" not in response:
            chunk=self.conn.recv(4096)
            assert chunk, "CDP handshake terminated"
            response+=chunk
        head,self.buf=response.split(b"\r\n\r\n",1)
        assert b" 101 " in head,head[:150]
    def _take(self,n):
        while len(self.buf)<n:
            chunk=self.conn.recv(max(n-len(self.buf),4096))
            assert chunk, "CDP socket disconnected"
            self.buf+=chunk
        ret,self.buf=self.buf[:n],self.buf[n:]
        return ret
    def _send(self,contents,opcode=1):
        secret=_os.urandom(4)
        n=len(contents)
        hdr=bytes([0x80 | opcode])
        if n<126: hdr+=bytes([0x80 | n])
        elif n<65536: hdr+=bytes([0xfe])+_struct.pack("!H",n)
        else: hdr+=bytes([0xff])+_struct.pack("!Q",n)
        self.conn.sendall(hdr+secret+bytes(x ^ secret[i%4] for i,x in enumerate(contents)))
    def _recv(self):
        while True:
            hdr=self._take(2)
            op=hdr[0]&15
            n=hdr[1]&127
            if n==126:n=_struct.unpack("!H",self._take(2))[0]
            elif n==127:n=_struct.unpack("!Q",self._take(8))[0]
            mask=self._take(4) if hdr[1]&128 else b""
            blob=self._take(n)
            if mask:blob=bytes(x^mask[i%4] for i,x in enumerate(blob))
            if op==9:self._send(blob,10);continue
            if op==8:raise AssertionError("CDP websocket closed")
            if op==1:return json.loads(blob)
    def command(self,method,params=None):
        self.counter+=1
        target=self.counter
        self._send(json.dumps({"id":target,"method":method,"params":params or {}}).encode())
        for unused in range(200):
            msg=self._recv()
            if msg.get("id")==target:
                assert "error" not in msg,(method,msg.get("error"))
                return msg.get("result",{})
        raise AssertionError(f"no response to {method}")


def test_exact_css_390_mobile_geometry_and_archived_screenshot(tmp_path):
    browser=next((shutil.which(x) for x in
       ("google-chrome","chromium","chromium-browser","google-chrome-stable")
       if shutil.which(x)),None)
    assert browser, "UI390_UNVERIFIED: Chromium not available"
    page=tmp_path/"mobile390"
    page.mkdir()
    (page/"styles.css").write_bytes((FRONT/"styles.css").read_bytes())
    html=(FRONT/"index.html").read_text(encoding="utf8")
    html=re.sub(r'<link[^>]+href="/static/styles.css[^>]*>',
                '<link rel="stylesheet" href="styles.css">',html)
    html=re.sub(r'<script[^>]+src="/static/[^>]+></script>','',html)
    (page/"index.html").write_text(html.replace("</body>",JS+"</body>"),encoding="utf8")
    with _tempfile.TemporaryDirectory() as tmp:
        profile=Path(tmp)/"profile"
        profile.mkdir()
        process=subprocess.Popen(
           [browser,"--headless=new","--no-sandbox","--disable-gpu",
            "--disable-background-networking","--disable-dev-shm-usage",
            "--no-first-run","--no-default-browser-check",
            "--remote-allow-origins=*","--remote-debugging-port=0",
            "--user-data-dir="+str(profile),"about:blank"],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        wire=None
        try:
            active=profile/"DevToolsActivePort"
            for unused in range(100):
                if active.exists():break
                assert process.poll() is None,"Chromium exited before CDP could start"
                _time.sleep(0.15)
            assert active.exists(),"Chromium remote port unavailable"
            port=int(active.read_text().splitlines()[0])
            pages=json.load(_urlrequest.urlopen(f"http://127.0.0.1:{port}/json/list",timeout=6))
            url=next(x["webSocketDebuggerUrl"] for x in pages if x["type"]=="page")
            wire=_CDPWire(url)
            wire.command("Page.enable")
            wire.command("Runtime.enable")
            wire.command("Emulation.setDeviceMetricsOverride",
              {"width":390,"height":844,"deviceScaleFactor":1,"mobile":True})
            wire.command("Page.navigate",{"url":(page/"index.html").resolve().as_uri()})
            actual=None
            for unused in range(75):
                ev=wire.command("Runtime.evaluate",{
                    "expression":'''(() => {
                      const p=document.getElementById("ui207-geometry");
                      return p ? JSON.parse(p.textContent) : null;
                    })()''',"returnByValue":True})
                actual=ev.get("result",{}).get("value")
                if actual is not None:break
                _time.sleep(0.12)
            assert actual is not None,"390px geometry JS did not execute"
            # Headless --window-size clamps to 500px; this check prevents false green.
            assert actual["viewport"]==[390,844],actual
            assert actual["dpr"]==1,actual
            assert not actual["overlap"],actual
            assert actual["pageWidth"]<=390+2,actual
            assert actual["bodyWidth"]<=390+2,actual
            assert actual["mainScrollWidth"]<=actual["mainClientWidth"]+2,actual
            assert actual["tableHeight"]<=370,actual
            shot=wire.command("Page.captureScreenshot",{
                  "format":"png","captureBeyondViewport":False})
            png=_base64.b64decode(shot["data"])
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            pixels=_struct.unpack("!II",png[16:24])
            assert pixels==(390,844),pixels
            checksum=hashlib.sha256(png).hexdigest()
            folder=_os.environ.get("UI207_EVIDENCE_DIR")
            if folder:
                out=Path(folder)
                out.mkdir(parents=True,exist_ok=True)
                filename=out/"cdp_effective_390x844_green.png"
                filename.write_bytes(png)
                (out/"cdp_effective_390x844_geometry.json").write_text(
                  json.dumps({"head":_os.environ.get("EXPECTED_SHA","UNKNOWN"),
                              "device_metrics":{"width":390,"height":844,"dpr":1},
                              "actual":actual,"screenshot_sha256":checksum,
                              "png_pixels":pixels,"source":"OFFLINE_STATIC_CHROMIUM_CDP"},
                             indent=2,sort_keys=True)+"\n",encoding="utf8")
            print("UI390_CDP=PASS", "effective_css_viewport="+str(actual["viewport"]),
                  "dpr="+str(actual["dpr"]),"png_sha256="+checksum)
        finally:
            if wire:
                wire.conn.close()
            process.terminate()
            try:process.wait(timeout=6)
            except subprocess.TimeoutExpired:process.kill()
