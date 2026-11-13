# SPDX-License-Identifier: Apache-2.0
"""WeChat UIA editor + one foreground Enter, with exact conversation checks."""
import time
import re


def chat_name(name):
    # Empty editors append a version-dependent voice-input placeholder to Name.
    # The independent title label must still exactly match the real contact.
    return re.sub(r'(?:(?:长按|按住)鼠标\s*)?语音输入(?:文字)?$','',name).strip()


class NativeUIASender:
    def __init__(self):
        self.refresh=lambda contact:True
        self.binding=None

    def snapshot(self,contact,expected_text=''):
        import uiautomation as auto
        import win32gui,win32process,psutil
        windows=[]
        def visit(hwnd,unused):
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):return
            try:
                pid=win32process.GetWindowThreadProcessId(hwnd)[1]
                process=psutil.Process(pid)
                if process.name().lower() not in ('weixin.exe','wechat.exe'):return
                control=auto.ControlFromHandle(hwnd)
                if control.ClassName=='mmui::MainWindow':windows.append((control,hwnd,pid,process.create_time()))
            except (psutil.Error,OSError):pass
        win32gui.EnumWindows(visit,None)
        if len(windows)!=1:raise ValueError('没有唯一且已激活的微信主窗口')
        root,hwnd,pid,created=windows[0]
        identity=(hwnd,pid,created)
        if self.binding is not None and self.binding!=identity:raise ValueError('微信进程已改变，请重新启动服务核对账号')
        nodes=[];stack=[(root,0)]
        while stack:
            node,depth=stack.pop();nodes.append(node)
            if len(nodes)>5000:raise ValueError('控件树超出限制')
            if depth<32:stack.extend((child,depth+1) for child in node.GetChildren())
        edits=[n for n in nodes if n.AutomationId=='chat_input_field']
        titles=[n for n in nodes if n.AutomationId.endswith('.current_chat_name_label')]
        if len(edits)!=1 or len(titles)!=1:raise ValueError('会话控件缺失或不唯一')
        edit=edits[0]
        if not contact.get('display_name') or chat_name(edit.Name)!=contact['display_name'] or titles[0].Name!=contact['display_name']:
            raise ValueError('会话身份不一致')
        value=edit.GetValuePattern()
        if not edit.IsEnabled or not edit.IsKeyboardFocusable or value is None or value.IsReadOnly or value.Value!=expected_text:
            raise ValueError('输入不可用或存在本人草稿')
        self.binding=identity
        return hwnd,edit

    def foreground(self,hwnd,edit):
        import win32gui
        return win32gui.GetForegroundWindow()==hwnd and edit.HasKeyboardFocus

    def prepare(self,contact):
        try:
            hwnd,edit=self.snapshot(contact)
            edit.SetFocus()  # Provider focus; may foreground WeChat.
            for _ in range(5):
                if self.foreground(hwnd,edit):return True
                time.sleep(.05)
            return False
        except Exception:return False

    def preflight(self,contact):
        try:
            hwnd,edit=self.snapshot(contact)
            return self.foreground(hwnd,edit)
        except Exception:return False

    def send(self,contact,text):
        if not self.refresh(contact):raise ValueError('新消息或本人接管，取消发送')
        hwnd,edit=self.snapshot(contact)
        if not self.foreground(hwnd,edit):raise ValueError('微信或输入框已失去焦点')
        edit.GetValuePattern().SetValue(text,waitTime=0)
        time.sleep(.2)
        if not self.refresh(contact):raise ValueError('写入后有新消息或人工接管，未发送')
        checked_hwnd,checked_edit=self.snapshot(contact,expected_text=text)
        if checked_hwnd!=hwnd or not self.foreground(hwnd,checked_edit):raise ValueError('发送前焦点或窗口变化，未发送')
        # No Invoke: live WeChat returns success without submitting the message.
        # Exactly one global-key submission; database echo remains the receipt.
        checked_edit.SendKeys('{Enter}',waitTime=.05)
