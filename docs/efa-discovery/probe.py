"""Targeted, cached research requests. No batch discovery; one explicit query per run."""
import argparse
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

p=argparse.ArgumentParser()
p.add_argument('kind',choices=['stopfinder','board'])
p.add_argument('query')
p.add_argument('label')
a=p.parse_args()
root=Path(__file__).resolve().parent
path=root/(a.label+'.json')
if path.exists():
    print('Reusing',path)
    raise SystemExit()
log=root/'requests.jsonl'
history=[json.loads(s) for s in log.read_text().splitlines()] if log.exists() else []
if history:time.sleep(max(0,1.1-(time.time()-history[-1]['finished'])))
if a.kind=='stopfinder':
    endpoint='XML_STOPFINDER_REQUEST'
    params=dict(name_sf=a.query,type_sf='stop',locationServerActive='1',outputFormat='rapidJSON',coordOutputFormat='WGS84[dd.ddddd]')
else:
    endpoint='XML_DM_REQUEST'
    params=dict(name_dm=a.query,type_dm='stop',useRealtime='1',limit='20',outputFormat='rapidJSON',mode='direct')
url='https://bsvg.efa.de/vrbstd_relaunch/'+endpoint+'?'+urllib.parse.urlencode(params)
r=dict(url=url,started=time.time())
try:
    with urllib.request.urlopen(url,timeout=20) as response:
        body=response.read();r.update(status=response.status,bytes=len(body))
    json.loads(body);path.write_bytes(body)
except Exception as error:r['error']=str(error)
r['finished']=time.time()
with log.open('a') as f:f.write(json.dumps(r)+'\n')
print(json.dumps(r))
