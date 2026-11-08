"""Post-benchmark server-only isolated plotting tools; binary wheels, no inference changes."""
import argparse,datetime,hashlib,importlib.metadata as im,json,os,platform,subprocess,sys,urllib.request
from pathlib import Path
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from common import dump,utcnow
from budget import Ledger
if platform.system()!='Linux':raise SystemExit('Do not install on the laptop')
run=BASE/'runs/self'
if not (run/'STOP_SUBMISSIONS').exists():raise SystemExit('Finish GPU measurements and stop submissions first')
for p in (run/'processes').glob('server-*.json'):
 d=json.loads(p.read_text());cmd=Path('/proc')/str(d['pid'])/'cmdline'
 if cmd.exists() and b'vllm' in cmd.read_bytes() and b'/data/models/MiniMax-H3/FL2VA' in cmd.read_bytes():raise SystemExit('Inference process still running; keep benchmark environment unchanged')
Ledger(BASE/'private/ledger.json').guard(next_gpu_seconds=600)
target=Path('/data/h3-report-tools');target.mkdir(exist_ok=True)
version='3.10.7';url='https://pypi.org/pypi/matplotlib/'+version+'/json'
with urllib.request.urlopen(url,timeout=20) as response:raw=response.read(2*1024*1024)
metadata=json.loads(raw)
if metadata['info']['version']!=version:raise SystemExit('Unexpected plot package version')
dump(run/'analysis/plot-registry-metadata.json',{'utc':utcnow(),'source':url,'version':metadata['info']['version'],'requires_python':metadata['info'].get('requires_python'),'response_sha256':hashlib.sha256(raw).hexdigest(),'purpose':'Post-measurement CPU figure authoring only'})
cmd=[sys.executable,'-m','pip','--isolated','install','--index-url','https://pypi.org/simple','--only-binary=:all:','--target',str(target),'matplotlib=='+version]
with (run/'analysis/plot-tools-install.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
font_url='https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf'
with urllib.request.urlopen(font_url,timeout=30) as response:data=response.read(25*1024*1024+1)
if len(data)>25*1024*1024 or data[:4]!=b'OTTO':raise SystemExit('Invalid or unexpectedly large font artifact')
font=target/'NotoSansCJKsc-Regular.otf';font.write_bytes(data)
license_url='https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/LICENSE'
with urllib.request.urlopen(license_url,timeout=20) as response:license_text=response.read(100000).decode()
(run/'analysis/NotoSansCJK-font-license.txt').write_text(license_text)
packages=sorted([{'name':d.metadata['Name'],'version':d.version} for d in im.distributions(path=[str(target)])],key=lambda d:d['name'].lower())
dump(run/'analysis/plot-tools-environment.json',{'utc':utcnow(),'target':str(target),'packages':packages,'inference_environment_modified':False,'font_source':font_url,'font_sha256':hashlib.sha256(data).hexdigest(),'font_license_source':license_url,'install_command':cmd})
environment=dict(os.environ);environment['PYTHONPATH']=str(target)
subprocess.run([sys.executable,str(BASE/'control/plots.py')],env=environment,check=True,timeout=180)
print('Post-benchmark figures generated on server. No laptop environment installed.')
