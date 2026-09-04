#!/usr/bin/env python3
"""Secret hygiene audit that never prints secret values."""
from __future__ import annotations
import json,os,re,stat,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PATTERNS={"github_pat":re.compile(r'ghp_[A-Za-z0-9]{20,}'),"aws_key":re.compile(r'AKIA[0-9A-Z]{16}'),"private_key":re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')}
def tracked():return set(subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines())
def main():
 files=tracked();findings=[]
 for rel in sorted(files):
  p=ROOT/rel
  if not p.is_file():continue
  if rel.endswith(('.pyc','.p12','.pem')) or Path(rel).name=='.env':findings.append({'file':rel,'type':'sensitive_file_tracked'})
  try:data=p.read_bytes()
  except:continue
  if len(data)>5_000_000:continue
  text=data.decode(errors='ignore')
  for name,pattern in PATTERNS.items():
   if pattern.search(text):findings.append({'file':rel,'type':name})
 for rel in ('.env','hs_trade_lab/.env','config.py','config.py.save'):
  p=ROOT/rel
  if p.exists() and stat.S_IMODE(p.stat().st_mode)&0o077:findings.append({'file':rel,'type':'permissions_too_open'})
 result={'tracked_files':len(files),'findings':findings,'finding_count':len(findings),'audited_at':__import__('datetime').datetime.now().isoformat(timespec='seconds')}
 out=ROOT/'research_outputs/security_audit_20260712.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
 raise SystemExit(1 if findings else 0)
if __name__=='__main__':main()
