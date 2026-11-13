# SPDX-License-Identifier: Apache-2.0
"""Build attributed multi-turn windows, preserve bubble boundaries, and index dialogue context.
No chat-model calls: topic boundaries are explicit cues + idle gaps, not semantic certainty.
"""
import argparse,collections,hashlib,json,math,re,sqlite3,statistics,sys,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
import vectorize_persona_rag as api
SHIFT=re.compile(r'^(?:对了|话说|说起来|换个话题|还有个事|突然想起|另外一件|先不说这个)')
ACK=re.compile(r'^[啊哈呵嘿嘻喔哦嗯哎呀额诶噢好的是对我去了耶斯笑死了hHaA!！?？~～。，、\s]+$')
def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def text(turn):return '\n'.join(turn['bubbles'])

def usable(r):return r['kind']=='text' and (not r['redaction_flags'] or r.get('allow_redacted_for_rag',False))
def u16(s,limit):return s.encode('utf-16-le')[:limit*2].decode('utf-16-le',errors='ignore')
def context_input(turns):
 selected=turns[-6:];body='\n'.join(('对方：' if t['role']=='partner' else '我：')+u16(text(t),400) for t in selected)
 return '对话语境：\n'+u16(body,1800)
def intent(q,r):
 if re.search('晚安|先睡|睡觉',q):return 'goodnight'
 if re.search('想你|爱你|亲亲|贴贴|抱抱|么么',q):return 'affection'
 if re.search('难过|不开心|累|疲惫|烦死',q):return 'care'
 if re.search('出去玩|出去走走|一起去|一起.*玩|想不想|要不要.*去|约|多出去|见面',q):return 'invitation'
 if re.search('好吃|美味|吃饭|吃点|饿|外卖|喝',q):return 'food'
 if ACK.fullmatch(q):return 'continuation' if not ACK.fullmatch(r) else 'acknowledgement'
 return 'question' if re.search('[?？]|怎么|为什么|有没有|是不是|吗|多少',q) else 'daily_share'
def prepare(a):
 if a.output.exists():raise ValueError('Use a fresh output directory')
 rows=list(api.lines(a.source/'clean_messages.jsonl'));splits={}
 previous=a.previous or a.source
 for d in api.lines(previous/'rag_documents.jsonl'):splits[d['metadata']['session_id']]=d['metadata']['split']
 episodes=[];session=0;episode=0;last=None;turns=[];boundaries=collections.Counter()
 for r in rows:
  session_break=last is None or r['ts']-last['ts']>21600
  if session_break:session+=1
  explicit=r['kind']=='text' and SHIFT.search(r['text']) and len(r['text'])>=6
  new=session_break or r['ts']-last['ts']>1200 or explicit
  if new:
   if turns:episodes.append(turns)
   turns=[];episode+=1;boundaries['idle_or_session' if not explicit else 'explicit_topic_cue']+=1
  if not turns or turns[-1]['role']!=r['role'] or r['ts']-last['ts']>120:
   turns.append({'role':r['role'],'session':session,'episode':episode,'messages':[]})
  turns[-1]['messages'].append(r);last=r
 if turns:episodes.append(turns)
 for turns in episodes:
  for t in turns:
   t['bubbles']=[r['text'] for r in t['messages'] if usable(r)]
   t['ids']=[r['id'] for r in t['messages']];t['text_ids']=[r['id'] for r in t['messages'] if usable(r)]
   t['media']=sum(r['kind']!='text' for r in t['messages']);t['redacted']=any(r['redaction_flags'] and not r.get('allow_redacted_for_rag',False) for r in t['messages'])
 docs=[];seen=set();filters=collections.Counter();bubble_counts=[]
 for turns in episodes:
  anchor=0
  for i,t in enumerate(turns):
   if i==0 or t['role']!='self' or turns[i-1]['role']!='partner':continue
   anchor+=1;prev=turns[i-1];q,r=text(prev),text(t)
   if not q or not r or len(q)>800 or len(r)>1400 or len(t['bubbles'])>12 or prev['redacted'] or t['redacted']:filters['empty_sensitive_or_oversized']+=1;continue
   # Overlapping dialogue windows: every third exchange, plus longer bursts and segment ends.
   if anchor%3 and len(t['bubbles'])<3 and i!=len(turns)-1 and anchor>1:continue
   if (prev['media'] or t['media']) and re.search('这张|那张|图里|图上|照片|这个图|看图|视频里|表情包|拍得|拍的',q+r):filters['media_dependent']+=1;continue
   context=[];size=0
   for old in reversed(turns[max(0,i-7):i]):
    if old['redacted']:break
    content=text(old)
    if not content:break # Do not bridge an unknown image/voice-only turn.
    if size+len(content)>2200:break
    context.append(old);size+=len(content)
   context.reverse()
   if not context:continue
   category=intent(q,r);requires=category=='continuation' or (len(q.strip())<=4 and category not in ('acknowledgement','goodnight')) or any(re.match(r'然后|不过|所以|因为|接着|这几天|我这|我妈|我以前|我刚|我现在|我在|我当时|我今天|明天我|我明天',b) for b in t['bubbles'])
   if requires and len(context)<3:filters['missing_continuation_context']+=1;continue
   if sum(old['media'] for old in context)+t['media']>3 and size<40:filters['mostly_unknown_media']+=1;continue
   public=lambda old:{'role':old['role'],'bubbles':old['bubbles'],'message_ids':old['ids'],'text_message_ids':old['text_ids'],'unparsed_media_count':old['media']}
   dialog=[public(x) for x in context]+[public(t)]
   identity=sha('episode-v3|'+'|'.join(mid for x in dialog for mid in x['message_ids']))
   if identity in seen:continue
   seen.add(identity)
   lines=['历史对话片段（只参考表达和承接，不代表现在的事实）：']
   for old in context[:-1]:lines.append(('对方：' if old['role']=='partner' else '我：')+'\n'.join(old['bubbles'])+(' [这一轮还有未解析媒体]' if old['media'] else ''))
   visible='\n'.join(lines)+'\n\n对方说：\n'+q+'\n\n我当时回复：\n'+r
   m={'role':'self_reply','timestamp':t['messages'][-1]['timestamp'],'session_id':t['session'],'episode_id':t['episode'],'split':splits.get(t['session'],'heldout'),'prompt_message_ids':prev['text_ids'],'target_message_ids':t['text_ids'],'source_message_ids':[mid for old in dialog for mid in old['message_ids']],'dialogue':dialog,'target_bubbles':t['bubbles'],'intent':category,'requires_context':requires,'original_media_parsed':False,'unparsed_media_count':sum(old['unparsed_media_count'] for old in dialog),'boundary_method':'20min idle / explicit topic cue / bounded overlap; heuristic'}
   docs.append({'schema':'wechat.persona.episode/3','id':identity,'text':visible,'embedding_text':'对方说：'+q,'context_text':context_input(context),'metadata':m});bubble_counts.append(len(t['bubbles']))
 if not docs:raise ValueError('No reliable multi-turn windows remain; inspect the cleaning report or use more dialogue')
 a.output.mkdir(parents=True);api.write(a.output/'report.json',{'documents':len(docs),'source_episodes':len(episodes),'boundaries':dict(boundaries),'filters':dict(filters),'multi_bubble_target_examples':sum(n>1 for n in bubble_counts),'max_target_bubbles':max(bubble_counts),'mean_target_bubbles':round(statistics.mean(bubble_counts),2),'multi_turn_examples':sum(len(d['metadata']['dialogue'])>=4 for d in docs),'requires_context':sum(d['metadata']['requires_context'] for d in docs),'intent_counts':dict(collections.Counter(d['metadata']['intent'] for d in docs)),'segmentation':'heuristic, not guaranteed semantically complete','chat_model_calls':0})
 with (a.output/'rag_documents.jsonl').open('w',encoding='utf-8') as f:
  for d in docs:f.write(json.dumps(d,ensure_ascii=False)+'\n')
 # Prepared only, not trained: all creator replies keep their actual bubble boundaries.
 with (a.output/'multiturn-train.jsonl').open('w',encoding='utf-8') as f:
  for d in docs:
   if d['metadata']['split']!='train':continue
   messages=[]
   for t in d['metadata']['dialogue']:
    messages.append({'role':'assistant' if t['role']=='self' else 'user','content':json.dumps({'replies':t['bubbles']},ensure_ascii=False) if t['role']=='self' else '\n'.join(t['bubbles'])})
   f.write(json.dumps({'messages':messages,'source_ids':d['metadata']['source_message_ids']},ensure_ascii=False)+'\n')
 profile=json.loads((a.source/'style_profile.json').read_text(encoding='utf-8'))
 cleaning_report=a.source/'cleaning_report.json'
 if cleaning_report.exists():
  preprocessing=json.loads(cleaning_report.read_text(encoding='utf-8'))
  report=json.loads((a.output/'report.json').read_text(encoding='utf-8'))
  report['preprocessing']={k:preprocessing[k] for k in ('date_range','redaction_policy','redaction_counts','small_sample','evaluation_partitions_complete') if k in preprocessing}
  api.write(a.output/'report.json',report)
 phrases='、'.join(x['text'] for x in profile['top_short_expressions'][:20])
 (a.output/'style_profile.md').write_text('# 本人的聊天节奏\n\n历史单条气泡常短，但一轮可能连发多条，不等于每轮只能说一句。根据当前话题和上下文决定长度、追问、解释、调侃或继续讲述；不要规定5字、10字或强制精简。\n常见既往措辞：'+phrases+'。仅在语境合适时参考，不机械堆砌。\n对话样例保存双方多轮顺序及本人气泡边界；未解析媒体不能猜，历史事件不能当作现在。\n',encoding='utf-8')
 print((a.output/'report.json').read_text(encoding='utf-8'),flush=True)
def embed(a):
 endpoint,model,key=api.credentials(a.config)
 documents_path=a.output/'rag_documents.jsonl'
 docs=list(api.lines(documents_path))
 if not docs:raise ValueError('没有可以向量化的片段，请先完成切片')
 db=api.connect(a.output/'persona-rag.sqlite')
 try:
  expected={'model':model,'endpoint':endpoint,'byteorder':sys.byteorder,'source_sha256':hashlib.sha256(documents_path.read_bytes()).hexdigest()}
  for name,value in expected.items():
   stored=api.getmeta(db,name)
   if stored is not None and stored!=value:raise ValueError('服务、模型或资料已经改变，请使用新的输出目录：'+name)
   api.setmeta(db,name,value)
  dimension=api.getmeta(db,'dimension')
  for col in ['context_text TEXT','context_vector BLOB']:
   try:db.execute('ALTER TABLE documents ADD COLUMN '+col)
   except sqlite3.OperationalError:pass
  db.execute('CREATE TABLE IF NOT EXISTS embedding_cache(input TEXT PRIMARY KEY,vector BLOB,norm REAL)')
  if dimension is not None:
   db.executemany('INSERT OR IGNORE INTO embedding_cache VALUES(?,?,?)',db.execute('SELECT embedding_text,vector,original_norm FROM documents WHERE vector IS NOT NULL AND dimension=?',(dimension,)).fetchall())
   db.executemany('INSERT OR IGNORE INTO embedding_cache VALUES(?,?,?)',db.execute('SELECT context_text,context_vector,1.0 FROM documents WHERE context_vector IS NOT NULL AND dimension=?',(dimension,)).fetchall())
  if a.cache:
   old=sqlite3.connect(a.cache.resolve().as_uri()+'?mode=ro',uri=True)
   try:
    if api.getmeta(old,'model')!=model or api.getmeta(old,'endpoint')!=endpoint or api.getmeta(old,'byteorder',sys.byteorder)!=sys.byteorder:raise ValueError('旧缓存的服务或模型不同，不能混用')
    cached_dimension=api.getmeta(old,'dimension')
    if type(cached_dimension)!=int or not 1<=cached_dimension<=16384:raise ValueError('旧缓存未记录有效维度')
    if dimension is not None and dimension!=cached_dimension:raise ValueError('旧缓存的向量维度不同')
    dimension=cached_dimension
    db.executemany('INSERT OR IGNORE INTO embedding_cache VALUES(?,?,?)',old.execute('SELECT embedding_text,vector,original_norm FROM documents WHERE vector IS NOT NULL AND dimension=?',(dimension,)))
   finally:old.close()
  for d in docs:
   db.execute('INSERT OR IGNORE INTO documents(id,text,embedding_text,metadata,input_hash,context_text) VALUES(?,?,?,?,?,?)',(d['id'],d['text'],d['embedding_text'],json.dumps(d['metadata'],ensure_ascii=False),sha(d['embedding_text']),d['context_text']))
  needed=set(x for d in docs for x in (d['embedding_text'],d['context_text']))
  known={r[0] for r in db.execute('SELECT input FROM embedding_cache')}
  todo=sorted(needed-known)
  if dimension is not None:api.setmeta(db,'dimension',dimension)
  db.commit();start=time.monotonic();done=0
  batches=iter([todo[i:i+24] for i in range(0,len(todo),24)])
  with ThreadPoolExecutor(max_workers=3) as pool:
   pending={}
   def submit():
    batch=next(batches,None)
    if batch:pending[pool.submit(api.post,endpoint,key,model,batch)]=batch
   for _ in range(3):submit()
   while pending:
    finished,_=wait(pending,return_when=FIRST_COMPLETED)
    for future in finished:
     batch=pending.pop(future)
     vs,dimension=api.validate(future.result(),len(batch),dimension)
     for text_value,(vector,norm) in zip(batch,vs):db.execute('INSERT OR REPLACE INTO embedding_cache VALUES(?,?,?)',(text_value,vector.tobytes(),norm))
     api.setmeta(db,'dimension',dimension);db.commit();done+=len(batch)
     api.write(a.output/'progress.json',{'embedded_new_inputs':done,'total_new_inputs':len(todo),'reused_inputs':len(needed&known),'documents':len(docs),'dimension':dimension,'elapsed_seconds':round(time.monotonic()-start,1)})
     if done%240<24:print(json.dumps({'embedded':done,'total':len(todo),'elapsed':round(time.monotonic()-start)}),flush=True)
     submit()
  if type(dimension)!=int:raise ValueError('尚未获得向量维度')
  db.execute('UPDATE documents SET vector=(SELECT vector FROM embedding_cache WHERE input=embedding_text),original_norm=(SELECT norm FROM embedding_cache WHERE input=embedding_text),context_vector=(SELECT vector FROM embedding_cache WHERE input=context_text),dimension=?',(dimension,))
  for row in db.execute('SELECT vector,context_vector FROM documents'):
   for blob in row:
    import array
    if blob is None:raise ValueError('仍有片段缺少向量，可重试同一命令')
    v=array.array('f');v.frombytes(blob)
    if len(v)!=dimension or any(not math.isfinite(x) for x in v) or abs(sum(x*x for x in v)-1)>1e-4:raise ValueError('向量长度或数值校验失败')
  db.execute('DROP TABLE embedding_cache');api.setmeta(db,'dimension',dimension);api.setmeta(db,'schema','dialogue-episode/3');db.commit();db.execute('VACUUM')
  if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('向量库完整性检查失败')
 finally:db.close()
 report=json.loads((a.output/'report.json').read_text(encoding='utf-8'))
 report.update({'vectors_complete':True,'model':model,'dimension':dimension,'new_embedding_inputs':len(todo),'reused_embedding_inputs':len(needed&known)})
 api.write(a.output/'report.json',report);print(json.dumps({'complete':True,'documents':len(docs),'dimension':dimension}),flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','embed']);p.add_argument('--source',type=Path,required=True);p.add_argument('--previous',type=Path,default=None);p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,default=None);p.add_argument('--config',type=Path,default=Path('credentials.json'));a=p.parse_args();prepare(a) if a.command=='prepare' else embed(a)
if __name__=='__main__':main()
