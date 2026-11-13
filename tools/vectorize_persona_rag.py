# SPDX-License-Identifier: Apache-2.0
"""Resumable OpenAI-compatible embeddings; provider and model are configured locally."""
from __future__ import annotations
import argparse,array,json,math,pathlib,sqlite3,sys,time,urllib.error,urllib.parse,urllib.request
def lines(p):
 with p.open(encoding="utf-8") as f:
  for line in f:
   if line.strip():yield json.loads(line)
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
def credentials(path):
 c=json.loads(path.read_text(encoding="utf-8-sig"))
 return credentials_dict(c)

def credentials_dict(c):
 for field,label in [('embedding_endpoint','向量接口完整 URL'),('embedding_model','向量模型名称'),('embedding_token','向量服务 key')]:
  if not isinstance(c.get(field),str) or not c[field].strip():raise ValueError('请先在 credentials.json 填写'+label)
 endpoint=c["embedding_endpoint"].strip();model=c["embedding_model"].strip()
 u=urllib.parse.urlsplit(endpoint)
 if u.scheme!="https" or not u.hostname or u.username is not None or u.password is not None or u.fragment:raise ValueError('向量接口需填写不含账号密码或片段的 HTTPS 完整地址')
 return endpoint,model,c["embedding_token"].strip()
def post(endpoint,key,model,texts):
 req=urllib.request.Request(endpoint,data=json.dumps({"model":model,"input":texts,"encoding_format":"float"},ensure_ascii=False).encode("utf-8"),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
 opener=urllib.request.build_opener(NoRedirect())
 for attempt in range(5):
  try:
   with opener.open(req,timeout=45) as r:return json.load(r)
  except urllib.error.HTTPError as e:
   if e.code in (429,500,502,503,504) and attempt<4:
    time.sleep(min(2**(attempt+1),16));continue
   raise RuntimeError("embedding provider HTTP "+str(e.code)) from None
  except (urllib.error.URLError,TimeoutError):
   if attempt<4:time.sleep(min(2**(attempt+1),16));continue
   raise RuntimeError("embedding network request failed after retries") from None
def validate(response,count,dim=None):
 data=response.get("data")
 if not isinstance(data,list) or len(data)!=count:raise ValueError("Embedding count mismatch")
 result=[None]*count
 for row in data:
  at=row["index"];v=row["embedding"]
  if type(at)!=int or not 0<=at<count or result[at] is not None:raise ValueError("Invalid embedding index")
  if not isinstance(v,list) or not 1<=len(v)<=16384:raise ValueError("Invalid vector")
  if dim is None:dim=len(v)
  if len(v)!=dim or any(type(x) not in (int,float) or not math.isfinite(x) for x in v):raise ValueError("Invalid vector dimensions or values")
  norm=math.sqrt(sum(x*x for x in v))
  if norm<=0:raise ValueError("Zero vector")
  packed=array.array("f",(x/norm for x in v))
  if any(not math.isfinite(x) for x in packed):raise ValueError("Float32 overflow")
  result[at]=(packed, norm)
 return result,dim
def connect(index):
 c=sqlite3.connect(index);c.execute("PRAGMA foreign_keys=ON")
 c.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
 c.execute("CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,text TEXT NOT NULL,embedding_text TEXT NOT NULL,metadata TEXT NOT NULL,input_hash TEXT NOT NULL,vector BLOB,dimension INTEGER,original_norm REAL)")
 return c
def getmeta(c,key,default=None):
 row=c.execute("SELECT value FROM metadata WHERE key=?",(key,)).fetchone()
 return json.loads(row[0]) if row else default
def setmeta(c,key,value):c.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)",(key,json.dumps(value,ensure_ascii=False)))
def query(args):
 endpoint,model,key=credentials(args.config)
 with sqlite3.connect(args.index.resolve().as_uri()+"?mode=ro",uri=True) as c:
  if getmeta(c,"model")!=model or getmeta(c,"endpoint")!=endpoint or getmeta(c,"byteorder")!=sys.byteorder:raise ValueError("Index/provider mismatch")
  q="对方说："+args.query
  vectors,dim=validate(post(endpoint,key,model,[q]),1,getmeta(c,"dimension"));query_vector=vectors[0][0]
  results=[]
  for ident,text,meta,blob in c.execute("SELECT id,text,metadata,vector FROM documents WHERE vector IS NOT NULL"):
   metadata=json.loads(meta)
   if args.train_only and metadata["split"]!="train":continue
   vector=array.array("f");vector.frombytes(blob)
   score=sum(a*b for a,b in zip(vector,query_vector))
   results.append({"id":ident,"score":score,"text":text,"metadata":metadata})
  results.sort(key=lambda x:x["score"],reverse=True)
  payload={"query":args.query,"model":model,"dimension":dim,"results":results[:args.top_k]}
  write(args.result,payload)
  print(json.dumps({"status":"ok","hits":min(args.top_k,len(results)),"top_score":results[0]["score"] if results else None,"result":str(args.result)},ensure_ascii=False))
def main():
 sys.stdout.reconfigure(encoding="utf-8")
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest="command",required=True)
 q=sub.add_parser("query");q.add_argument("--index",type=pathlib.Path,required=True);q.add_argument("--query",required=True);q.add_argument("--result",type=pathlib.Path,required=True);q.add_argument("--top-k",type=int,default=5);q.add_argument("--train-only",action="store_true")
 for item in [q]:item.add_argument("--config",type=pathlib.Path,default=pathlib.Path("credentials.json"))
 args=p.parse_args()
 try:
  query(args)
 except Exception as exc:
  print(json.dumps({"status":"failed","error_type":type(exc).__name__,"reason":str(exc) if isinstance(exc,(ValueError,RuntimeError)) else "See local inputs or connectivity; credentials are not logged."},ensure_ascii=False),flush=True);sys.exit(1)
if __name__=="__main__":main()
