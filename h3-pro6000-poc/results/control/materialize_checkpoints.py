"""Recover exact frozen raw evidence bytes from SHA-verified gzip checkpoints.
Only paths listed by the server's frozen archive manifest are materialized.
"""
import argparse,gzip,hashlib,json,os
from pathlib import Path

def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def materialize(run):
 run=Path(run);manifest=json.loads((run/'cost/archive-manifest.json').read_text());files={x['path']:x for x in manifest['files']};done=[];skipped=[]
 for rel,row in files.items():
  path=Path(rel)
  if path.is_absolute() or '..' in path.parts:raise RuntimeError('Unsafe manifest path')
  if path.parts[0]=='checkpoints' or not (path.suffix in ('.log','.jsonl') or 'console' in path.name):continue
  gzrel='checkpoints/'+rel.replace('/','__')+'.gz';gz=run/gzrel;gzrow=files.get(gzrel)
  if not gzrow or not gz.is_file() or gz.stat().st_size!=gzrow['size'] or digest(gz)!=gzrow['sha256']:skipped.append({'path':rel,'reason':'matching gzip checkpoint absent or checksum mismatch'});continue
  target=run/rel
  if target.exists() and target.stat().st_size==row['size'] and digest(target)==row['sha256']:continue
  if row['size']>512*1024*1024:skipped.append({'path':rel,'reason':'raw file exceeds bounded local materialization size'});continue
  target.parent.mkdir(parents=True,exist_ok=True);tmp=target.with_name('.'+target.name+'.materializing');total=0;h=hashlib.sha256()
  try:
   with gzip.open(gz,'rb') as source,tmp.open('wb') as output:
    while True:
     block=source.read(1024*1024)
     if not block:break
     total+=len(block)
     if total>row['size']:raise ValueError('Raw size exceeds manifest')
     h.update(block);output.write(block)
   if total!=row['size'] or h.hexdigest()!=row['sha256']:raise ValueError('Checkpoint does not match frozen raw source')
   os.replace(tmp,target);done.append({'path':rel,'size':total,'sha256':h.hexdigest(),'verified_checkpoint':gzrel})
  except (OSError,ValueError,EOFError) as exc:
   tmp.unlink(missing_ok=True);skipped.append({'path':rel,'reason':type(exc).__name__})
 return {'method':'gzip exact-byte restoration followed by raw SHA256 equality to server archive manifest','materialized':done,'skipped_fallback_to_original_transfer':skipped,'restored_bytes':sum(x['size'] for x in done)}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--proof',type=Path,required=True);a=p.parse_args();r=materialize(a.run_dir);a.proof.parent.mkdir(parents=True,exist_ok=True);a.proof.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'materialized_files':len(r['materialized']),'restored_bytes':r['restored_bytes'],'fallback_files':len(r['skipped_fallback_to_original_transfer'])}))
