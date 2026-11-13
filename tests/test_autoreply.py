# SPDX-License-Identifier: Apache-2.0
"""No real account, model endpoint, UI input or message sending."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import array
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from autoreply_core import Queue, Generator, unknown_self_state, parse_reply, current_partner_turn, historical_demo


class Model:
    def __init__(self): self.seen = []
    def generate(self, contact, history):
        self.seen.append((contact['username'], history))
        return ['第一条', '第二条'], False


class Sender:
    def __init__(self, active=True, crash=False): self.calls=[]; self.active=active; self.crash=crash
    def preflight(self, contact): return self.active
    def send(self, contact, text):
        self.calls.append((contact['username'],text))
        if self.crash: raise IOError('uncertain')


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.index = self.root/'test.sqlite'; self.index.touch()
        self.q = Queue(self.root/'state.sqlite')
        self.contact = dict(username='test_peer', enabled=True, auto_send=False, index=str(self.index))
        self.model = Model()
    def tearDown(self): self.q.db.close(); self.temp.cleanup()
    def event(self, ident='one', role='partner', text='你好', ts=100): return dict(id=ident,role=role,text=text,ts=ts)
    def ingest(self, **kwargs): self.q.ingest('test_peer', [self.event(**kwargs)], 100)
    def status(self): return self.q.db.execute('SELECT status FROM jobs WHERE peer=?',('test_peer',)).fetchone()[0]
    def ready(self):
        self.contact['auto_send']=True; self.ingest(); self.q.process(self.contact,self.model,105)
    def test_baseline_and_duplicates(self):
        self.q.ingest('test_peer',[self.event()],100,baseline=True)
        self.ingest(); self.assertFalse(self.q.process(self.contact,self.model,105)); self.assertEqual(self.model.seen,[])
    def test_merge_and_draft_only(self):
        self.ingest(); self.assertFalse(self.q.process(self.contact,self.model,102))
        self.q.ingest('test_peer',[self.event('two',text='还有一件事',ts=102)],102)
        self.assertTrue(self.q.process(self.contact,self.model,107)); self.assertEqual(self.status(),'draft')
        self.assertEqual(len(self.model.seen[0][1]),2)
        sender=Sender(); self.assertFalse(self.q.dispatch(self.contact,sender,108,True)); self.assertEqual(sender.calls,[])
    def test_no_rag_or_disabled(self):
        self.ingest(); self.contact['index']=''; self.q.process(self.contact,self.model,105)
        self.assertEqual(self.model.seen,[])
    def test_exactly_one_attempt_and_echo(self):
        self.ready(); sender=Sender()
        self.assertFalse(self.q.dispatch(self.contact,sender,106,False))
        self.assertTrue(self.q.dispatch(self.contact,sender,106,True))
        self.q.dispatch(self.contact,sender,107,True); self.assertEqual(len(sender.calls),1)
        self.assertEqual(self.status(),'submitted')
        self.q.ingest('test_peer',[self.event('echo','self','第一条\n第二条',107)],107)
        self.assertEqual(self.status(),'confirmed')
    def test_wrong_window_no_write(self):
        self.ready(); sender=Sender(active=False)
        self.assertFalse(self.q.dispatch(self.contact,sender,106,True)); self.assertEqual(sender.calls,[])
    def test_uncertain_never_retried(self):
        self.ready(); sender=Sender(crash=True)
        self.q.dispatch(self.contact,sender,106,True); self.q.dispatch(self.contact,sender,107,True)
        self.assertEqual(len(sender.calls),1); self.assertEqual(self.status(),'uncertain')
    def test_human_takeover(self):
        self.ready(); self.q.ingest('test_peer',[self.event('human','self','我自己回复',106)],106)
        self.assertEqual(self.status(),'superseded')
        self.q.ingest('test_peer',[self.event('next',ts=107)],107)
        self.assertFalse(self.q.process(self.contact,self.model,112))
    def test_human_during_generation(self):
        self.ingest()
        q=self.q
        class InFlight:
            def generate(inner,contact,history):
                q.ingest('test_peer',[dict(id='human',role='self',text='本人',ts=106)],106)
                return ['不该发送'],False
        self.q.process(self.contact,InFlight(),105); self.assertEqual(self.status(),'superseded')
    def test_new_input_during_generation(self):
        self.ingest(); q=self.q
        class InFlight:
            def generate(inner,contact,history):
                q.ingest('test_peer',[dict(id='new',role='partner',text='新消息',ts=106)],106)
                return ['过时回复'],False
        self.q.process(self.contact,InFlight(),105); self.assertEqual(self.status(),'pending')
    def test_review_and_age(self):
        self.ingest(text='帮忙转账'); self.q.process(self.contact,self.model,105)
        self.assertEqual(self.status(),'review'); self.assertEqual(self.model.seen,[])
    def test_contact_isolation(self):
        self.ingest(); other=dict(self.contact,username='other')
        self.q.ingest('other',[self.event('other',text='另外的人')],100)
        self.q.process(self.contact,self.model,105); self.q.process(other,self.model,105)
        self.assertEqual([x['text'] for x in self.model.seen[0][1]],['你好'])
        self.assertEqual([x['text'] for x in self.model.seen[1][1]],['另外的人'])
    def test_crash_recovery(self):
        self.ready(); self.q.db.execute("UPDATE jobs SET status='dispatching'"); self.q.db.commit(); self.q.db.close()
        self.q=Queue(self.root/'state.sqlite'); self.assertEqual(self.status(),'uncertain')
    def generator_fixture(self, documents=None):
        import vectorize_persona_rag as api
        c=api.connect(self.index)
        c.execute('ALTER TABLE documents ADD COLUMN context_vector BLOB')
        for key,value in [('model','test-model'),('endpoint','https://example.invalid/embeddings'),('dimension',3),('byteorder',sys.byteorder)]: api.setmeta(c,key,value)
        blob=array.array('f',[1,0,0]).tobytes()
        for ident, text, metadata in documents or [('test','只属于该联系人的历史片段',{})]:
            c.execute('INSERT INTO documents(id,text,embedding_text,metadata,input_hash,vector,dimension,context_vector) VALUES(?,?,?,?,?,?,?,?)',
                      (ident,text,'对方说：你好',json.dumps(metadata,ensure_ascii=False),'hash',blob,3,blob))
        c.commit();c.close()
        config={'chat':dict(endpoint='https://example.invalid/chat/completions',model='chat-test',token='test-only'), 'embedding':dict(embedding_endpoint='https://example.invalid/embeddings',embedding_model='test-model',embedding_token='test-only')}
        response={'data':[{'index':0,'embedding':[1,0,0]},{'index':1,'embedding':[1,0,0]}]}
        return config,response
    def test_real_index_generation_contract(self):
        config,response=self.generator_fixture()
        with patch('autoreply_core.vectors.post',return_value=response), patch('autoreply_core.post_chat',return_value={'choices':[{'message':{'content':'{"replies":["你好呀","在呢"],"needs_review":false}'}}]}) as model:
            parts,review=Generator(config).generate(self.contact,[dict(role='partner',text='你好')])
            self.assertEqual(parts,['你好呀','在呢']);self.assertFalse(review)
            body=model.call_args[0][1]
            self.assertTrue(any('只属于该联系人的历史片段' in m['content'] for m in body['messages']))
            self.assertEqual(body['messages'][-1]['content'],'你好')
        config['embedding']['embedding_model']='wrong-model'
        with patch('autoreply_core.vectors.post',side_effect=AssertionError('Should not contact a mismatched service')):
            with self.assertRaisesRegex(ValueError,'知识库与查询向量服务/模型不一致'):
                Generator(config).generate(self.contact,[dict(role='partner',text='你好')])
        config['embedding']['embedding_model']='test-model'
        bad={'choices':[{'message':{'content':'{"replies":["测试回复"]}"'}}]}
        fixed={'choices':[{'message':{'content':'{"replies":["在呢"],"needs_review":false}'}}]}
        with patch('autoreply_core.vectors.post',return_value=response),patch('autoreply_core.post_chat',side_effect=[bad,fixed]) as model:
            self.assertEqual(Generator(config).generate(self.contact,[dict(role='partner',text='你好')]),(['在呢'],False))
            self.assertEqual(model.call_count,2)
            self.assertEqual(model.call_args[0][1]['temperature'],0)
    def test_current_turn_keeps_burst_and_excludes_answered_messages(self):
        history=[dict(role='partner',text='旧问题',ts=1),dict(role='self',text='旧回答',ts=2),
                 dict(role='partner',text='有点累',ts=3),dict(role='partner',text='想休息',ts=4)]
        self.assertEqual([m['text'] for m in current_partner_turn(history)],['有点累','想休息'])
        self.assertEqual(current_partner_turn(history[:-2]),[])
        self.assertEqual(current_partner_turn([]),[])
        self.assertEqual([m['text'] for m in current_partner_turn([
            dict(role='partner',text='昨天的消息',ts=1),dict(role='partner',text='刚来的消息',ts=500)])],['刚来的消息'])
        burst=[dict(role='partner',text=str(i),ts=10+i) for i in range(7)]
        self.assertEqual(current_partner_turn(history[:2]+burst),burst)
    def test_generation_query_and_live_roles_follow_current_burst(self):
        config,response=self.generator_fixture()
        history=[dict(role='partner',text='旧问题'),dict(role='self',text='先歇歇'),dict(role='self',text='别硬撑'),
                 dict(role='partner',text='有点累'),dict(role='partner',text='想休息')]
        result={'choices':[{'message':{'content':'{"replies":["歇一会呀"],"needs_review":false}'}}]}
        with patch('autoreply_core.vectors.post',return_value=response) as embedding, patch('autoreply_core.post_chat',return_value=result) as model:
            Generator(config).generate(self.contact,history)
        inputs=embedding.call_args[0][3]
        self.assertEqual(inputs[0],'对方说：有点累\n想休息')
        self.assertIn('旧问题',inputs[1]) # Older material remains context, never the retrieval question.
        messages=model.call_args[0][1]['messages']
        self.assertEqual(messages[-1],dict(role='user',content='有点累\n想休息'))
        live_self=[json.loads(m['content']) for m in messages if m['role']=='assistant']
        self.assertEqual(live_self,[dict(replies=['先歇歇','别硬撑'],needs_review=False)])
        self.assertIn('不能扮演对方',messages[0]['content'])
        self.assertIn('不自动等于当前联系人',messages[0]['content'])
    def test_demonstrations_preserve_ownership_order_and_bubbles(self):
        dialogue=[dict(role='partner',bubbles=['今天好累']),dict(role='self',bubbles=['咋啦','忙啥了']),
                  dict(role='partner',bubbles=['事情多','还没忙完']),dict(role='self',bubbles=['慢慢来呗','别急'])]
        meta=dict(dialogue=dialogue,target_bubbles=['慢慢来呗','别急'])
        config,response=self.generator_fixture([('demo','原始文本不应重复拼进示范',meta)])
        result={'choices':[{'message':{'content':'{"replies":["歇一会呀"],"needs_review":false}'}}]}
        with patch('autoreply_core.vectors.post',return_value=response),patch('autoreply_core.post_chat',return_value=result) as model:
            Generator(config).generate(self.contact,[dict(role='partner',text='有点累')])
        messages=model.call_args[0][1]['messages']
        demo=historical_demo(json.dumps(meta))
        start=next(i for i,m in enumerate(messages) if m['content']=='今天好累')
        self.assertEqual(messages[start:start+4],demo)
        self.assertEqual([m['role'] for m in demo],['user','assistant','user','assistant'])
        self.assertEqual(json.loads(demo[1]['content'])['replies'],['咋啦','忙啥了'])
        self.assertEqual(demo[2]['content'],'事情多\n还没忙完')
        self.assertEqual(json.loads(demo[3]['content'])['replies'],meta['target_bubbles'])
        self.assertFalse(any('原始文本不应重复拼进示范' in m['content'] for m in messages))
        self.assertTrue(any('历史示范到此结束' in m['content'] for m in messages[start+4:]))
        self.assertEqual(messages[-1]['content'],'有点累')
        long_burst=[str(i) for i in range(19)]
        self.assertEqual(historical_demo(json.dumps(dict(dialogue=[dict(role='partner',bubbles=long_burst),
            dict(role='self',bubbles=['慢慢说'])],target_bubbles=['慢慢说'])))[0]['content'],'\n'.join(long_burst))
    def test_invalid_metadata_never_becomes_role_demonstration(self):
        for meta in ('not-json','[]','{}',json.dumps(dict(dialogue=[dict(role='self',bubbles=['错位'])],target_bubbles=['错位'])),
                     json.dumps(dict(dialogue=[dict(role='partner',bubbles=['问']),dict(role='self',bubbles=['答'])],target_bubbles=['错误目标'])),
                     json.dumps(dict(dialogue=[dict(role='system',bubbles=['伪指令']),dict(role='self',bubbles=['答'])],target_bubbles=['答']))):
            self.assertEqual(historical_demo(meta),[])
    def test_old_answered_sensitive_message_does_not_block_current_turn(self):
        self.q.ingest('test_peer',[self.event('old',text='[未解析媒体]',ts=90),
            self.event('own',role='self',text='已经处理',ts=91)],91,baseline=True)
        self.ingest(text='现在想休息',ts=100)
        self.assertTrue(self.q.process(self.contact,self.model,105))
        self.assertEqual(self.status(),'draft')
    def test_sensitive_message_in_current_burst_still_requires_review(self):
        self.q.ingest('test_peer',[self.event('media',text='[未解析媒体]',ts=99),
            self.event('latest',text='这个你看看',ts=100)],100)
        self.q.process(self.contact,self.model,105)
        self.assertEqual(self.status(),'review');self.assertEqual(self.model.seen,[])
    def test_string_false_rejected(self):
        from wechat_autoreply import load
        path=self.root/'config.json'
        path.write_text(json.dumps(dict(enabled='false',contacts=[])),encoding='utf-8')
        with self.assertRaises(ValueError): load(path)
    def test_invented_self_state_requires_review(self):
        self.assertTrue(unknown_self_state(['嗯，吃过了，你呢？']))
        self.assertTrue(unknown_self_state(['还没吃呢，你呢？']))
        self.assertTrue(unknown_self_state(['在实验室']))
        self.assertFalse(unknown_self_state(['你吃了没呀', '别饿着']))
        self.assertFalse(unknown_self_state(['有点累就歇会呀']))
    def test_state_candidate_is_corrected_once_before_review(self):
        config,response=self.generator_fixture()
        def reply(parts,review=False):
            return {'choices':[{'message':{'content':json.dumps(dict(replies=parts,needs_review=review))}}]}
        with patch('autoreply_core.vectors.post',return_value=response),patch('autoreply_core.post_chat',side_effect=[
            reply(['吃了，你呢？']),reply(['你吃啥啦'])]) as model:
            self.assertEqual(Generator(config).generate(self.contact,[dict(role='partner',text='吃了没')]),(['你吃啥啦'],False))
            self.assertEqual(model.call_count,2)
            self.assertIn('未经确认的本人当前状态',model.call_args[0][1]['messages'][-1]['content'])
    def test_authorized_casual_states_pass_without_model_rewrite(self):
        config,response=self.generator_fixture();config['allow_casual_self_state']=True
        for text in ('吃了，你呢？','还没吃呢','刚睡醒','我刚吃完了','我现在在忙','我还没睡'):
            with self.subTest(text=text):
                reply={'choices':[{'message':{'content':json.dumps(dict(replies=[text],needs_review=False))}}]}
                with patch('autoreply_core.vectors.post',return_value=response),patch('autoreply_core.post_chat',return_value=reply) as model:
                    self.assertEqual(Generator(config).generate(self.contact,[dict(role='partner',text='问个日常状态')]),([text],False))
                    self.assertEqual(model.call_count,1)
                    prompt=model.call_args[0][1]['messages'][0]['content']
                    self.assertIn('可直接回复吃了',prompt)
                    self.assertNotIn('不能回答还没吃',prompt)
        self.assertTrue(unknown_self_state(['在实验室'],allow_casual=True))
        self.assertTrue(unknown_self_state(['我现在在公司'],allow_casual=True))
        self.assertTrue(unknown_self_state(['我吃了，在实验室'],allow_casual=True))
    def test_casual_policy_preserves_model_review_and_sensitive_gate(self):
        config,response=self.generator_fixture();config['allow_casual_self_state']=True
        reply={'choices':[{'message':{'content':'{"replies":["需要本人决定"],"needs_review":true}'}}]}
        with patch('autoreply_core.vectors.post',return_value=response),patch('autoreply_core.post_chat',return_value=reply):
            self.assertEqual(Generator(config).generate(self.contact,[dict(role='partner',text='确认重要事项')]),(['需要本人决定'],True))
        self.ingest(text='验证码发我');self.q.process(self.contact,self.model,105)
        self.assertEqual(self.status(),'review');self.assertEqual(self.model.seen,[])
    def test_casual_policy_string_false_is_rejected(self):
        from wechat_autoreply import load
        path=self.root/'config.json'
        path.write_text(json.dumps(dict(enabled=False,allow_casual_self_state='false',contacts=[])),encoding='utf-8')
        with self.assertRaises(ValueError):load(path)
    def test_failed_content_correction_stays_in_review_without_loop(self):
        config,response=self.generator_fixture()
        first={'choices':[{'message':{'content':'{"replies":["吃了"],"needs_review":false}'}}]}
        for second in (first,{'choices':[{'message':{'content':'not json'}}]},OSError('offline')):
            with self.subTest(second=type(second).__name__):
                with patch('autoreply_core.vectors.post',return_value=response),patch('autoreply_core.post_chat',side_effect=[first,second]) as model:
                    parts,review=Generator(config).generate(self.contact,[dict(role='partner',text='吃了没')])
                    self.assertTrue(review);self.assertEqual(model.call_count,2)
    def test_model_review_decision_is_not_overridden_by_correction(self):
        config,response=self.generator_fixture()
        reply={'choices':[{'message':{'content':'{"replies":["本人确认后再说"],"needs_review":true}'}}]}
        with patch('autoreply_core.vectors.post',return_value=response),patch('autoreply_core.post_chat',return_value=reply) as model:
            self.assertEqual(Generator(config).generate(self.contact,[dict(role='partner',text='确认一下')]),(['本人确认后再说'],True))
            self.assertEqual(model.call_count,1)
    def test_followup_after_confirmed_send_can_send_again(self):
        self.ready();sender=Sender()
        self.assertTrue(self.q.dispatch(self.contact,sender,106,True))
        self.q.ingest('test_peer',[self.event('echo',role='self',text='第一条\n第二条',ts=107)],107)
        self.assertEqual(self.status(),'confirmed')
        self.q.ingest('test_peer',[self.event('followup',text='继续聊呀',ts=108)],108)
        self.assertTrue(self.q.process(self.contact,self.model,113))
        self.assertEqual(self.status(),'ready')
        self.assertTrue(self.q.dispatch(self.contact,sender,114,True))
        self.assertEqual(len(sender.calls),2)
    def test_reply_schema_remains_strict(self):
        self.assertEqual(parse_reply('```json\n{"replies":["在呢"],"needs_review":false}\n```'),(['在呢'],False))
        for raw in ('{"replies":["测试回复"]}"','{"replies":["你好"]}','{"replies":["你好"],"needs_review":"false"}'):
            with self.assertRaises(ValueError):parse_reply(raw)
    def test_new_message_during_navigation(self):
        self.ready();q=self.q
        class Switching(Sender):
            def prepare(inner,contact):
                q.ingest('test_peer',[dict(id='newer',role='partner',text='刚来的消息',ts=107)],107)
                return True
        sender=Switching()
        self.assertFalse(q.dispatch(self.contact,sender,107,True));self.assertEqual(sender.calls,[])
        self.assertEqual(self.status(),'pending')


if __name__=='__main__': unittest.main()
