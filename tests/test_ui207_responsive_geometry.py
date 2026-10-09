"""Exact PR203 baseline vs UI207 browser-geometry and PNG screenshots.

Only local HTML/CSS used: no live backend, authentication, JS polling or API.
Chrome/Chromium must exist. Running browser tests is mandatory, not a skip.
"""
import hashlib
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
let result={viewport:[innerWidth,innerHeight],overlap,boxes,
pageWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,
mainScrollWidth:main.scrollWidth,mainClientWidth:main.clientWidth,
sidebarVisible:document.getElementById('sidebar').getBoundingClientRect().width>0,
gridCols:getComputedStyle(main).gridTemplateColumns,tableHeight:table.clientHeight};
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

@pytest.mark.parametrize("size",SIZES)
def test_no_overlapping_cards_with_sidebar_and_table_scroll(tmp_path,size):
 browser=next((shutil.which(x) for x in ("google-chrome","chromium","chromium-browser","google-chrome-stable") if shutil.which(x)),None)
 assert browser,"Browser missing: visual regression is BLOCKED, never silently skipped"
 original=(FRONT/"index.html").read_text(encoding="utf8")
 red_css=subprocess.check_output(["git","show",BASE+":senecio_polymarket/frontend/styles.css"],cwd=ROOT)
 current=(FRONT/"styles.css").read_bytes()
 red=geometry(browser,tmp_path/"red",original,red_css,size)
 green=geometry(browser,tmp_path/"green",original,current,size)
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
