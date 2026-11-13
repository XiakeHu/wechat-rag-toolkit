# SPDX-License-Identifier: Apache-2.0
"""Independent Windows WeChat RAG service. No sending on import or startup."""
import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from bootstrap_vendor import activate
activate()
from autoreply_core import Generator, Queue
from autoreply_metrics import Timings
from autoreply_runtime import GenerationPool

def make_sender(config):
    from native_uia_sender import NativeUIASender
    return NativeUIASender()


class DatabaseSource:
    def __init__(self, config, work):
        from wechat_cli.core.context import AppContext
        from wechat_cli.core.db_cache import DBCache
        import atexit
        cache = work / 'decrypt-cache'; cache.mkdir(exist_ok=True)
        DBCache.CACHE_DIR = str(cache); DBCache.MTIME_FILE = str(cache / '_mtimes.json')
        self.app = AppContext(config['wechat_config'])
        atexit.unregister(self.app.cache.cleanup)
        self.self_id = config['self_username']
        if not self.self_id: raise ValueError('请填写本人的 username')
        self.contacts = config['contacts']
        path = self.app.cache.get('contact/contact.db')
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as c:
            names = {r[0]: (r[2] or r[1] or r[0]) for r in c.execute('SELECT username,nick_name,remark FROM contact')}
        if self.self_id not in names: raise ValueError('当前微信账号与本人 username 不匹配')
        for contact in self.contacts:
            peer = contact['username']
            if not contact.get('enabled'): continue
            if peer == self.self_id or peer.endswith('@chatroom') or peer not in names: raise ValueError('只支持已核对的联系人私聊')
            if contact.get('auto_send') and (names[peer] != contact.get('display_name') or list(names.values()).count(names[peer]) != 1):
                raise ValueError('自动发送要求对方显示名/备注唯一且与配置一致')

    def poll(self, peer, since):
        from wechat_cli.core.messages import decompress_content, find_msg_db_keys
        table = 'Msg_' + hashlib.md5(peer.encode()).hexdigest()
        events = []
        for relative in find_msg_db_keys(self.app.all_keys):
            path = self.app.cache.get(relative)
            if not path: raise ValueError('消息分库暂时不可读')
            with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as c:
                if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone(): continue
                rows = c.execute(f'''SELECT m.local_id,m.server_id,m.local_type,m.sort_seq,m.create_time,
                   n.user_name,m.message_content,m.WCDB_CT_message_content FROM [{table}] m
                   LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id WHERE m.create_time>=?
                   ORDER BY m.create_time,m.sort_seq,m.local_id''', (since,))
                for local, server, kind, order, stamp, sender, content, ct in rows:
                    if sender not in (peer, self.self_id): continue
                    text = decompress_content(content, ct) if kind & 0xffffffff == 1 else '[未解析媒体：需要本人查看]'
                    if text is None: raise ValueError('有消息未能解码，暂停本轮处理')
                    ident = 'server:' + str(server) if server and str(server) != '0' else relative + ':' + str(local) + ':' + str(stamp)
                    events.append(dict(id=ident, role='self' if sender == self.self_id else 'partner', text=text, ts=stamp, order=order or 0))
        return events


def inspect_ui(window=None):
    import uiautomation as auto
    # Read-only inspection. Save only in ignored work directory, never public logs.
    stack = [(window or auto.GetForegroundControl(), 0)]; result = []
    while stack and len(result) < 2500:
        control, depth = stack.pop()
        result.append(dict(depth=depth, name=control.Name, automation_id=control.AutomationId, class_name=control.ClassName, type=control.ControlTypeName))
        if depth < 32: stack.extend((x, depth+1) for x in control.GetChildren())
    return result


def diagnose_uia():
    """Read existing WeChat UIA trees, without activation, clicks or memory writes."""
    import uiautomation as auto
    import win32gui, win32process, psutil
    windows = []
    def visit(hwnd, unused):
        if not win32gui.IsWindowVisible(hwnd): return
        try:
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if psutil.Process(pid).name().lower() in ('weixin.exe','wechat.exe'):
                windows.append(dict(hwnd=hwnd, nodes=inspect_ui(auto.ControlFromHandle(hwnd))))
        except (psutil.Error, OSError): pass
    win32gui.EnumWindows(visit, None)
    return windows


def load(path):
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if type(config.get('enabled')) is not bool: raise ValueError('enabled必须是true或false')
    if type(config.get('allow_casual_self_state',False)) is not bool:
        raise ValueError('allow_casual_self_state必须是true或false')
    # Ignore retired selector/transport options in existing private configurations.
    config.pop('sender',None);config.pop('ui',None)
    for key, default, low, high in [('poll_seconds',1,.25,60),('merge_seconds',2,0,60)]:
        value = config.get(key, default)
        if type(value) not in (float,int) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(key+'超出允许范围')
    workers = config.get('generation_workers',2)
    if type(workers) is not int or not 1 <= workers <= 4: raise ValueError('生成并发数必须为1到4')
    base = path.resolve().parent
    for key in ('wechat_config','persona_prompt_file'):
        if config.get(key): config[key] = str((base / Path(os.path.expandvars(config[key])).expanduser()).resolve())
    peers = set()
    for contact in config['contacts']:
        if any(type(contact.get(k)) is not bool for k in ('enabled','auto_send')): raise ValueError('联系人开关必须是true或false')
        if contact.get('enabled'):
            if not contact.get('username') or contact['username'] in peers: raise ValueError('联系人 username 为空或重复')
            peers.add(contact['username'])
            if not contact.get('index'): raise ValueError('请绑定该联系人的知识库')
            contact['index'] = str((base / contact['index']).resolve())
    return config


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['run','status','pause','inspect-ui','diagnose-uia','activate-uia','restore-uia','timings'])
    p.add_argument('--config', type=Path, default=Path('autoreply.json'))
    p.add_argument('--allow-send', action='store_true', help='Also requires global and contact auto-send switches')
    p.add_argument('--work', type=Path, default=Path('work/autoreply'))
    a = p.parse_args(); a.work.mkdir(parents=True, exist_ok=True)
    if a.command in ('activate-uia','restore-uia'):
        from uia_activation import activate,restore
        ok=(activate if a.command=='activate-uia' else restore)(a.work)
        print(('UIA已激活，发送仍需前台焦点。' if a.command=='activate-uia' else '本工具修改的UIA标志已还原。') if ok else '未完成；未发送消息，请检查微信状态和私有运行目录。')
        if not ok:raise ValueError('UIA标志操作未完成')
        return
    if a.command == 'diagnose-uia':
        start = time.perf_counter(); result = diagnose_uia()
        (a.work/'uia-diagnostic.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(windows=len(result),nodes=sum(len(w['nodes']) for w in result),
            edit_controls=sum(n['type']=='EditControl' for w in result for n in w['nodes']),
            elapsed_ms=round((time.perf_counter()-start)*1000,2)), ensure_ascii=False))
        print('只读取现有控件，未唤醒无障碍开关；详情保存在私有work目录。'); return
    if a.command == 'timings':
        records = []
        for path in (a.work/'timings.previous.jsonl',a.work/'timings.jsonl'):
            if path.exists():
                for line in path.read_text(encoding='utf-8').splitlines():
                    try: records.append(json.loads(line))
                    except ValueError: pass
        stages = {}
        for record in records: stages.setdefault(record['stage'],[]).append(record['elapsed_ms'])
        for stage, values in sorted(stages.items()):
            values.sort()
            print(f'{stage}: n={len(values)}, mean={sum(values)/len(values):.1f}ms, p95={values[math.ceil(len(values)*.95)-1]:.1f}ms')
        if not stages: print('尚无耗时记录；运行服务后再查看。')
        return
    if a.command == 'pause':
        (a.work / 'PAUSE').touch(); print('已请求暂停，不再生成或发送。删除PAUSE文件后可恢复。'); return
    if a.command == 'inspect-ui':
        print('5秒后只读检查前台窗口，请切换到微信。'); time.sleep(5)
        (a.work/'ui-controls.json').write_text(json.dumps(inspect_ui(), ensure_ascii=False, indent=2), encoding='utf-8'); print('控件信息保存在私有work目录。'); return
    # Inspection of status must not mutate a running sender's dispatch state.
    if a.command == 'status':
        with closing(sqlite3.connect((a.work/'state.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as db:
            print(json.dumps(db.execute('SELECT peer,status,note,parts FROM jobs').fetchall(), ensure_ascii=False, indent=2))
        return
    # OS file lock: a second service instance cannot send duplicate replies.
    import msvcrt
    with (a.work/'service.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        config = load(a.config)
        timings = Timings(a.work/'timings.jsonl')
        source = DatabaseSource(config, a.work); queue = Queue(a.work/'state.sqlite',timings)
        queue.db.execute("UPDATE jobs SET status='superseded',note='服务重新启动，不补发旧任务' WHERE status IN ('pending','ready')"); queue.db.commit()
        contacts = [c for c in config['contacts'] if c.get('enabled')]
        if not contacts: raise ValueError('尚未开启任何联系人')
        started = time.time()
        for c in contacts: queue.ingest(c['username'], source.poll(c['username'], started-86400), started, baseline=True)
        print('服务已启动；仅处理启动后消息。Ctrl+C停止；pause命令可暂停。')
        generator = Generator(config,timings); sender = make_sender(config)
        pool = GenerationPool(queue,generator,config.get('generation_workers',2),timings)
        merge = config.get('merge_seconds',2); max_age = config.get('max_message_age_seconds',180)
        if hasattr(sender,'refresh'):
            def refresh(contact):
                now=time.time(); peer=contact['username']
                with timings.span('send_refresh'):
                    queue.ingest(peer,[e for e in source.poll(peer,max(started-10,now-600)) if e['ts']>started],now,config.get('manual_pause_seconds',300))
                job=queue.db.execute('SELECT version,status,updated FROM jobs WHERE peer=?',(peer,)).fetchone()
                paused=queue.db.execute('SELECT until FROM pause WHERE peer=?',(peer,)).fetchone()
                return (not (a.work/'PAUSE').exists() and bool(job) and job[0]==sender.expected_version
                    and job[1] in ('ready','dispatching') and now-job[2]<=config.get('max_message_age_seconds',180)
                    and not (paused and paused[0]>now))
            sender.refresh=refresh
        try:
            while True:
                tick = time.perf_counter()
                pool.collect(max_age=max_age)
                if (a.work/'PAUSE').exists(): time.sleep(1); continue
                # Poll every contact before submitting generation or entering GUI work.
                for c in contacts:
                    try:
                        now = time.time(); peer = c['username']
                        with timings.span('poll'):
                            incoming = source.poll(peer, max(started-10, now-600))
                        queue.ingest(peer, [e for e in incoming if e['ts'] <= started], now, baseline=True)
                        queue.ingest(peer, [e for e in incoming if e['ts'] > started], now, config.get('manual_pause_seconds',300))
                    except Exception as error:
                        print('本轮已跳过：'+type(error).__name__+'（请检查配置、微信或依赖；未输出消息内容）')
                pool.collect(max_age=max_age)
                if (a.work/'PAUSE').exists(): continue
                pool.submit(contacts,time.time(),merge,max_age)
                # All actual sends remain serialized on this main thread.
                for c in contacts:
                    try:
                        if not (a.work/'PAUSE').exists():
                            queue.dispatch(c,sender,time.time(),bool(config.get('enabled')) and a.allow_send,max_age)
                    except Exception as error:
                        print('发送已跳过：'+type(error).__name__+'（未输出消息内容）')
                time.sleep(max(.05,config.get('poll_seconds',1)-(time.perf_counter()-tick)))
        finally:
            pool.close(); queue.db.close()


if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: print('服务已停止。')
    except Exception as e: print('未启动或已停止：'+type(e).__name__+'；请检查配置和私有运行目录。'); sys.exit(1)
