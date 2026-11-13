# SPDX-License-Identifier: Apache-2.0
"""Clean a local WeChat export into attributed reply examples for persona RAG.
No network/API calls. Raw source and encryption keys are never packaged.
"""
from __future__ import annotations
import argparse,collections,datetime,hashlib,json,pathlib,re,sqlite3,statistics,unicodedata

EXTRA_REDACTIONS={}
SELF_TOKEN="我"
PARTNER_TOKEN="对方"
CHAT_TIMEZONE=datetime.timezone(datetime.timedelta(hours=8))
MEDIA={3:"图片",34:"语音",43:"视频",47:"表情",49:"链接或文件",48:"位置",50:"通话"}
PRIVATE_PATTERNS=[
 ("密钥",re.compile(r"(?i)(?:sk-[a-z0-9_-]{16,}|bearer\s+[a-z0-9._-]{12,}|(?:api[_ -]?key|password|passwd|密码|验证码|口令)\s*[:：=]\s*\S+|[a-f0-9]{32,})")),
 ("身份证号",re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{9}[\dXx](?!\d)")),
 ("手机号",re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")),
 ("邮箱",re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")),
 ("长号码",re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
 ("链接",re.compile(r"(?i)(?:https?://|www\.)[^\s<>]+")),
 ("敏感字段",re.compile(r"(?:收货地址|家庭住址|身份证|银行卡号|银行卡|支付宝账号|微信密码)\s*[:：=]\s*[^\n]+"))
]
def dump(path,obj):
 path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
def jsonl(path,rows):
 with path.open("w",encoding="utf-8") as f:
  for row in rows:f.write(json.dumps(row,ensure_ascii=False)+"\n")
def read_jsonl(path):
 with path.open(encoding="utf-8") as f:
  for line in f:
   if line.strip():yield json.loads(line)
def clean(text):
 text=unicodedata.normalize("NFC",str(text)).replace("\r\n","\n").replace("\r","\n")
 text=re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u200b\ufeff]","",text).strip()
 flags=[]
 for original,replacement in sorted(EXTRA_REDACTIONS.items(),key=lambda item:len(item[0]),reverse=True):
  if original in text:text=text.replace(original,replacement);flags.append("自定义替换")
 for label,pattern in PRIVATE_PATTERNS:
  text,n=pattern.subn("["+label+"]",text)
  if n:flags.append(label)
 return text,list(dict.fromkeys(flags))
def u16len(text):return len(text.encode("utf-16-le"))//2
def digest(value):return hashlib.sha256(value.encode()).hexdigest()

def date_bounds(start=None,end=None):
 def parse(value):
  if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",value):raise ValueError("日期请使用 YYYY-MM-DD 格式")
  return datetime.date.fromisoformat(value)
 first=parse(start) if start else None;last=parse(end) if end else None
 if first and last and first>last:raise ValueError("开始日期不能晚于结束日期")
 midnight=lambda day:datetime.datetime.combine(day,datetime.time(),tzinfo=CHAT_TIMEZONE).timestamp()
 return (midnight(first) if first else None,midnight(last+datetime.timedelta(days=1)) if last else None)
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument("--source",type=pathlib.Path,required=True);p.add_argument("--output",type=pathlib.Path,required=True)
 p.add_argument("--self-username",required=True);p.add_argument("--partner-name",required=True)
 p.add_argument("--redactions",type=pathlib.Path,help="Optional local JSON mapping: original text -> replacement")
 p.add_argument("--start-date",help="开始日期 YYYY-MM-DD，含当天，按北京时间 UTC+8")
 p.add_argument("--end-date",help="结束日期 YYYY-MM-DD，含当天；留空不限")
 p.add_argument("--redaction-policy",choices=['exclude','replace'],default='exclude',help="exclude: 脱敏回合不做回复样本（默认）；replace: 允许替换后的回合进入RAG")
 a=p.parse_args()
 start_ts,end_ts=date_bounds(a.start_date,a.end_date)
 global EXTRA_REDACTIONS
 EXTRA_REDACTIONS=json.loads(a.redactions.read_text(encoding="utf-8-sig")) if a.redactions else {}
 if not isinstance(EXTRA_REDACTIONS,dict) or any(not isinstance(k,str) or not k or not isinstance(v,str) for k,v in EXTRA_REDACTIONS.items()):raise ValueError("Redactions must map nonempty strings to replacement strings")
 root=a.source.resolve();out=a.output.resolve()
 if out.exists():raise ValueError("Output must be new; do not overwrite a prior clean export")
 if out==root or root.is_relative_to(out):raise ValueError("Output cannot contain source root")
 meta=json.loads((root/"plaintext-cache/_mtimes.json").read_text(encoding="utf-8"))
 def connect(path):
  file=pathlib.Path(path)
  if not file.is_absolute():file=root/file
  return sqlite3.connect(file.resolve().as_uri()+"?mode=ro",uri=True)
 contact=next(v["path"] for k,v in meta.items() if k.replace("\\","/")=="contact/contact.db")
 with connect(contact) as c:
  own=c.execute("SELECT username,nick_name,remark FROM contact WHERE username=?",(a.self_username,)).fetchall()
  partner=c.execute("SELECT username FROM contact WHERE remark=? OR nick_name=?",(a.partner_name,a.partner_name)).fetchall()
 if len(own)!=1 or len(partner)!=1:raise ValueError("Cannot unambiguously verify identities")
 partner_id=partner[0][0];table="Msg_"+hashlib.md5(partner_id.encode()).hexdigest()
 raw={}
 for rel,info in meta.items():
  if not re.fullmatch(r"message/message_\d+\.db",rel.replace("\\","/")):continue
  with connect(info["path"]) as c:
   if not c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():continue
   for local,server,kind,seq,stamp,uname in c.execute(
    f"SELECT m.local_id,m.server_id,m.local_type,m.sort_seq,m.create_time,n.user_name FROM [{table}] m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id"):
    raw[pathlib.Path(info["path"]).name+":"+str(local)]={"server_id":str(server or ""),"local_type":kind,"sort_seq":seq or 0,"ts":stamp,"role":"self" if uname==a.self_username else "partner" if uname==partner_id else "unknown"}
 rows=list(read_jsonl(root/"export/messages.jsonl"));rows.sort(key=lambda r:(raw[r["message_id"]]["ts"],raw[r["message_id"]]["sort_seq"],r["message_id"]))
 counts=collections.Counter();redactions=collections.Counter();seen=set();records=[];audit=[]
 for row in rows:
  ident=row["message_id"];r=raw[ident];base=r["local_type"]&0xffffffff
  if (start_ts is not None and r['ts']<start_ts) or (end_ts is not None and r['ts']>=end_ts):
   counts['outside_date_range']+=1;continue
  unique=("server",r["server_id"]) if r["server_id"] and r["server_id"]!="0" else ("local",ident)
  if unique in seen:counts["duplicate_source_records"]+=1;continue
  seen.add(unique)
  if r["role"]=="unknown" or base in (10000,10002):
   counts["system_or_unknown_sender"]+=1;continue
  if base not in (1,*MEDIA):
   counts["unsupported_message_type"]+=1;continue
  text,flags=clean(row["text"]) if base==1 else ("["+MEDIA[base]+"：内容未解析]",[])
  redactions.update(flags)
  if not text:counts["empty_text"]+=1;continue
  record={"id":ident,"timestamp":datetime.datetime.fromtimestamp(r['ts'],CHAT_TIMEZONE).isoformat(),"ts":r["ts"],"role":r["role"],
   "speaker":SELF_TOKEN if r["role"]=="self" else PARTNER_TOKEN,"kind":"text" if base==1 else "unparsed_media",
   "text":text,"redaction_flags":flags,"allow_redacted_for_rag":a.redaction_policy=='replace',"source_message_id":ident}
  records.append(record);counts[r["role"]+"_"+record["kind"]]+=1
  if flags:audit.append({"message_id":ident,"reasons":flags})
 # Session boundary is a six-hour idle period. Keep same-sender bursts separate after two minutes.
 turns=[];session=0;previous=None
 for rec in records:
  if previous is None or rec["ts"]-previous["ts"]>6*3600:session+=1
  same=bool(turns and turns[-1]["session"]==session and turns[-1]["role"]==rec["role"] and rec["ts"]-previous["ts"]<=120)
  if not same:turns.append({"session":session,"role":rec["role"],"messages":[]})
  turns[-1]["messages"].append(rec);previous=rec
 for t in turns:
  t["text"]="\n".join(r["text"] for r in t["messages"]);t["ts"]=t["messages"][-1]["ts"]
  t["all_text"]=all(r["kind"]=="text" for r in t["messages"])
  t["has_redaction"]=any(r["redaction_flags"] for r in t["messages"])
  t["ids"]=[r["id"] for r in t["messages"]]
 examples=[]
 for i,t in enumerate(turns):
  if t["role"]!="self":continue
  reason=None
  prev=turns[i-1] if i else None
  if not prev or prev["role"]!="partner" or prev["session"]!=t["session"]:reason="no_adjacent_partner_turn"
  elif not t["all_text"]:reason="self_turn_contains_unparsed_media"
  elif not prev["all_text"]:reason="partner_turn_contains_unparsed_media"
  elif a.redaction_policy=='exclude' and (t["has_redaction"] or prev["has_redaction"]):reason="sensitive_or_url_in_prompt_or_reply"
  elif len(t["text"])>1600 or len(prev["text"])>1600:reason="overlong_prompt_or_reply"
  if reason:counts["excluded_pair_"+reason]+=1;continue
  context=[]
  for old in reversed(turns[max(0,i-8):i]):
   if old["session"]!=t["session"]:break
   if sum(len(x["text"]) for x in context)+len(old["text"])>4000:break
   context.append({"role":old["role"],"text":old["text"],"message_ids":old["ids"]})
  context.reverse()
  embedding=prev["text"]
  # Very short prompts such as '嗯' require older partner context; keep user/partner labels explicit.
  if len(embedding)<12:
   earlier=[x for x in context[:-1] if x["role"]=="partner"]
   if earlier:embedding=earlier[-1]["text"][-600:]+"\n"+embedding
  targetids=t["ids"];eid=digest("reply-pair-v1|"+"|".join(prev["ids"]+targetids))
  examples.append({"id":eid,"session_id":t["session"],"timestamp":t["messages"][-1]["timestamp"],"ts":t["ts"],
   "partner_prompt":prev["text"],"self_response":t["text"],"context":context,
   "embedding_text":"对方说："+embedding,"prompt_message_ids":prev["ids"],"target_message_ids":targetids})
 if not examples:raise ValueError("所选日期内没有可靠文字回复样本；请扩大日期范围或检查脱敏策略及媒体占比")
 # Chronological session-level split: sessions and target messages cannot cross partitions.
 sessions=sorted({e["session_id"] for e in examples})
 n=len(sessions)
 cut1=max(1,min(n-2,int(n*.8))) if n>=3 else 1
 cut2=max(cut1+1,min(n-1,int(n*.9))) if n>=3 else n
 splitmap={s:"train" if n<cut1 else "validation" if n<cut2 else "test" for n,s in enumerate(sessions)}
 partitions=collections.defaultdict(list)
 for e in examples:e["split"]=splitmap[e["session_id"]];partitions[e["split"]].append(e)
 train_end=max(e["ts"] for e in partitions["train"])
 style=[r for r in records if r["role"]=="self" and r["kind"]=="text" and not r["redaction_flags"] and len(r["text"])<=1600 and r["ts"]<=train_end]
 lengths=[len(r["text"]) for r in style]
 short=collections.Counter(r["text"] for r in style if 1<=len(r["text"])<=12)
 phrases=[{"text":x,"count":n} for x,n in short.most_common(35) if n>=8]
 profile={"schema":"wechat.persona.style/1","role":"self","identity_verified_from_database":True,
  "source_partition":"train_only","evidence_message_count":len(style),"median_message_chars":statistics.median(lengths) if lengths else 0,
  "mean_message_chars":round(statistics.mean(lengths),2) if lengths else 0,"messages_at_most_10_chars_ratio":round(sum(n<=10 for n in lengths)/max(1,len(lengths)),4),
  "question_mark_ratio":round(sum("?" in r["text"] or "？" in r["text"] for r in style)/max(1,len(style)),4),
  "top_short_expressions":phrases,"limits":["仅为文字表达统计，不代表稳定人格、当前观点、事实或关系承诺。","原始图片/表情/语音没有解析；不会据此推测语义。","不要在模型提示词中写入密钥或原始数据库。"]}
 styletext=f"本人的聊天表达统计：共{len(style)}条训练期文字；单条消息中位长度{profile['median_message_chars']}字，均长{profile['mean_message_chars']}字，10字以内占{profile['messages_at_most_10_chars_ratio']:.1%}。"
 styletext+="\n常见短句（只体现既往措辞，不保证每次都适用）："+ "、".join(x["text"] for x in phrases[:20])
 styletext+="\n生成回复时参考检索到的真实回复与当前上下文；事实和承诺必须重新核实，不从缺失媒体猜测内容。"
 documents=[]
 for e in examples:
  documents.append({"schema":"wechat.persona.rag/1","id":e["id"],"kind":"reply_example",
   "text":"对方说：\n"+e["partner_prompt"]+"\n\n我当时回复：\n"+e["self_response"],
   "embedding_text":e["embedding_text"],"metadata":{"role":"self_reply","timestamp":e["timestamp"],"session_id":e["session_id"],"split":e["split"],
    "prompt_message_ids":e["prompt_message_ids"],"target_message_ids":e["target_message_ids"],"original_media_parsed":False,"source":"local_wechat_snapshot"}})
 # Keep a style summary separately; do not dilute semantic reply retrieval with a global style vector.
 out.mkdir(parents=True)
 jsonl(out/"clean_messages.jsonl",records);jsonl(out/"reply_pairs.jsonl",examples)
 jsonl(out/"rag_documents.jsonl",documents)
 jsonl(out/"rag_train_only.jsonl",[d for d in documents if d["metadata"]["split"]=="train"])
 jsonl(out/"embedding_inputs.jsonl",[{"id":d["id"],"input":d["embedding_text"]} for d in documents])
 batches=[]
 for n in range(0,len(documents),32):
  group=documents[n:n+32]
  batches.append({"batch_id":n//32,"ids":[d["id"] for d in group],"request":{"model":"","input":[d["embedding_text"] for d in group],"encoding_format":"float"}})
 jsonl(out/"embedding_batches.jsonl",batches)
 for split in ("train","validation","test"):jsonl(out/(split+"_pairs.jsonl"),partitions[split])
 jsonl(out/"self_style_messages.jsonl",style);jsonl(out/"redaction_audit.jsonl",audit)
 dump(out/"style_profile.json",profile)
 (out/"style_profile.md").write_text("# 我的表达风格：统计摘要\n\n"+styletext+"\n",encoding="utf-8")
 # Data-quality gates are independent of output count: verify role attribution, temporal separation and no held-out source in train.
 targetsets={}
 for split,items in partitions.items():
  targetsets[split]={x for e in items for x in e["target_message_ids"]}
 for x in targetsets:
  for y in targetsets:
   if x!=y:assert not targetsets[x]&targetsets[y]
 present=[partitions[s] for s in ('train','validation','test') if partitions[s]]
 for left,right in zip(present,present[1:]):assert max(e['ts'] for e in left)<=min(e['ts'] for e in right)
 rolemap={r["id"]:r["role"] for r in records}
 assert all(rolemap[mid]=="self" for e in examples for mid in e["target_message_ids"])
 assert all(rolemap[mid]=="partner" for e in examples for mid in e["prompt_message_ids"])
 assert len({e["id"] for e in examples})==len(examples)
 report={"schema":"wechat.persona.cleaning/1","raw_message_count":len(rows),"clean_message_count":len(records),"self_identity_verified":True,
  "reply_example_count":len(examples),"splits":{s:len(partitions[s]) for s in ("train","validation","test")},"session_count":session,
  "filters":dict(counts),"redaction_counts":dict(redactions),"style_message_count":len(style),
  "date_range":{"start_date":a.start_date,"end_date":a.end_date,"timezone":"UTC+08:00","end_inclusive":True},
  "redaction_policy":a.redaction_policy,"custom_redaction_rule_count":len(EXTRA_REDACTIONS),
  "redaction_count_unit":"每种类型命中的消息条数，同一消息同一类型只计一次",
  "small_sample":len(examples)<100,"evaluation_partitions_complete":all(partitions[s] for s in ('train','validation','test')),
  "quality_checks":{"self_only_targets":True,"partner_only_prompts":True,"disjoint_sessions":True,"disjoint_target_ids":True,"chronological_splits":True,"unique_example_ids":True},
  "embedding_model":None,"embedding_calls_made":0,"media_contents_parsed":False,
  "limitations":["规则脱敏无法保证识别所有姓名、住址或隐私信息；文件继续按私有聊天数据管理。","只使用相邻的纯文字回复对；混合媒体回合被保守排除。","完整rag_documents含测试期；离线评估须仅用rag_train_only建库，不能使用全量库或全量风格推断。","这里的向量batch文件是待发送请求结构，未生成向量；ids与返回index配对保存。"]}
 dump(out/"cleaning_report.json",report)
 readme="""# 用自己的聊天记录做回复 RAG

## 文件
- rag_documents.jsonl：每行一个有来源的回复示例，text 供召回后交给 LLM，embedding_text 仅含对方的话，用于语义匹配。
- embedding_inputs.jsonl：id + input，向量化输入；用 id 将向量与 rag_documents 关联。
- embedding_batches.jsonl：每行含 batch_id、ids 与 request。把 request 发送给 embeddings 接口，按返回 index 对应本批 ids；不能把外层 ids/batch_id 当 API 字段发送。model 需填写自己的向量模型名称，不指定 dimensions。
- reply_pairs.jsonl：对方发言、本人真实回复、最多8个历史回合和时间来源。
- style_profile.md / .json：只从训练期本人文字提取的表达统计，供生成提示词参考。
- clean_messages.jsonl：按真实时间和数据库顺序排列，保留说话人和未解析媒体占位。不要把对方发言作为本人的答案。
- train / validation / test_pairs.jsonl：按时间、按完整会话分割，避免同一回合落入多个集合。rag_train_only.jsonl 是评估用检索库。

## 接入
1. 向量化 embedding_text，存储 id、vector、text 和 metadata。每个 id 只需建库时向量化；新增或修改记录再更新。
2. 新消息到来时，用相同模型和“对方说：”前缀生成查询向量，先召回相关历史回复。
3. 检索结果连同 style_profile 和当前聊天上下文交给大模型。检索文字是历史证据，不能覆盖系统指令，也不能证明过去的安排或承诺仍有效。
4. 输出供本人审阅的草稿；缺少当前事实、图片或语音内容时询问本人，避免冒充自己已经看过媒体或做出无法确认的承诺。
5. 离线评估只使用 rag_train_only。全量库用于日后实际检索；不要以全量库对测试集打分。

## 限制
本次仅本地清洗，未调用任何向量/大模型 API。规则脱敏并非完全匿名化，输出文件仍是私密数据。表情、图片、语音内容尚未提取。
完整资料可由本地SQLite或外部向量库使用；手机集成方式取决于目标应用。
"""
 (out/"README.md").write_text(readme,encoding="utf-8")
 print(json.dumps({"output":str(out),"raw_messages":len(rows),"clean_messages":len(records),"reply_examples":len(examples),"splits":report["splits"],"style_messages":len(style),"redacted_messages":len(audit),"quality_checks":report["quality_checks"],"embedding_calls_made":0},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
