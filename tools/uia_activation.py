# SPDX-License-Identifier: Apache-2.0
"""Explicit Qt accessibility activation with a local rollback record."""
import ctypes
from ctypes import wintypes as wt
import json


def dependencies(work):
    from replica_integrity import verify_replica_sources
    verify_replica_sources()
    from wechatauto import accessibility as acc
    from wechatauto.logger import wxlog
    wxlog.logger.disabled=True
    acc.GATE_CACHE_FILE=str(work/'uia-gate-cache.json')
    return acc.AccessibilityGate


def handle(pid):
    k=ctypes.windll.kernel32
    k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];k.OpenProcess.restype=wt.HANDLE
    k.CloseHandle.argtypes=[wt.HANDLE]
    h=k.OpenProcess(0x438,False,pid)
    if not h:raise OSError('无法打开微信无障碍标志')
    return k,h


def restore(work):
    import psutil
    path=work/'uia-gate.json'
    if not path.exists():return False
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('restored'):return True
    try:process=psutil.Process(record['pid'])
    except psutil.NoSuchProcess:return False
    if process.create_time()!=record['created']:return False
    gate=dependencies(work)
    module=gate._weixin_dll_module(record['pid'])
    if not module or module[0]!=record['base'] or module[2]!=record['dll']:return False
    k,h=handle(record['pid'])
    try:
        for change in record['changes']:
            if change['original']!=0 or change.get('restored'):continue
            address=record['base']+change['rva']
            current=gate._read_process_byte(h,address)
            if current==0:change['restored']=True
            elif current==1:change['restored']=gate._write_process_byte(h,address,0)
            else:raise ValueError('标志被其他程序更改，未覆盖')
        record['restored']=all(c['original']==1 or c.get('restored') for c in record['changes'])
        path.write_text(json.dumps(record,indent=2),encoding='utf-8')
        return record['restored']
    finally:k.CloseHandle(h)


def activate(work):
    import psutil,win32gui,win32process
    path=work/'uia-gate.json'
    gate=dependencies(work)
    if path.exists():
        previous=json.loads(path.read_text(encoding='utf-8'))
        try:
            if not previous.get('restored') and psutil.Process(previous['pid']).create_time()==previous['created']:
                if gate._mmui_present(previous['hwnd']):return True
                if not restore(work):raise ValueError('上次激活尚未还原')
        except psutil.NoSuchProcess:pass
    windows=[]
    def visit(hwnd,unused):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindowText(hwnd) not in ('微信','Weixin','WeChat'):return
        pid=win32process.GetWindowThreadProcessId(hwnd)[1]
        try:
            process=psutil.Process(pid)
            if process.name().lower() in ('weixin.exe','wechat.exe'):
                module=gate._weixin_dll_module(pid)
                if module:windows.append((hwnd,pid,process.create_time(),module))
        except psutil.Error:pass
    win32gui.EnumWindows(visit,None)
    if len(windows)!=1:raise ValueError('没有唯一微信主窗口，未激活')
    hwnd,pid,created,(base,size,dll)=windows[0]
    candidates=gate._qaccessible_candidate_rvas(dll)[:4]
    record=dict(hwnd=hwnd,pid=pid,created=created,base=base,dll=dll,changes=[],restored=False)
    def save():path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    k,h=handle(pid)
    try:
        for rva in candidates:
            if not 0<rva<size:continue
            original=gate._read_process_byte(h,base+rva)
            if original not in (0,1):continue
            change=dict(rva=rva,original=original,restored=False)
            record['changes'].append(change);save()
            if original==0 and not gate._write_process_byte(h,base+rva,1):
                change['restored']=True;save();continue
            if gate._mmui_present(hwnd,timeout=2):save();return True
            if original==0:
                change['restored']=gate._write_process_byte(h,base+rva,0);save()
                if not change['restored']:raise OSError('无法还原未生效的候选标志')
        return False
    except BaseException:
        for change in record['changes']:
            if change['original']==0 and not change.get('restored'):
                address=base+change['rva']
                if gate._read_process_byte(h,address)==1:
                    change['restored']=gate._write_process_byte(h,address,0)
        save()
        raise
    finally:k.CloseHandle(h)
