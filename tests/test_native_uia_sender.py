# SPDX-License-Identifier: Apache-2.0
"""Mock UIA provider. Never sends real keys or opens WeChat."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_uia_sender import NativeUIASender,chat_name


class Editor:
    def __init__(self):self.Value='';self.HasKeyboardFocus=True;self.keys=[]
    def GetValuePattern(self):return self
    def SetValue(self,text,waitTime=0):self.Value=text
    def SetFocus(self):self.HasKeyboardFocus=True;return True
    def SendKeys(self,keys,waitTime=0):self.keys.append(keys)


class SenderTests(unittest.TestCase):
    def test_only_known_placeholder_suffix_is_removed(self):
        self.assertEqual(chat_name('测试联系人长按鼠标 语音输入'),'测试联系人')
        self.assertEqual(chat_name('测试联系人按住鼠标 语音输入文字'),'测试联系人')
        self.assertEqual(chat_name('测试联系人二'),'测试联系人二')
    def setUp(self):
        self.sender=NativeUIASender();self.edit=Editor();self.front=True
        def snapshot(contact,expected_text=''):
            if contact.get('display_name')!='test' or self.edit.Value!=expected_text:raise ValueError('wrong contact or draft')
            return 42,self.edit
        self.sender.snapshot=snapshot
        self.sender.foreground=lambda hwnd,edit:self.front and edit.HasKeyboardFocus
        self.contact=dict(display_name='test')

    def test_one_enter_no_clipboard_or_mouse(self):
        self.sender.send(self.contact,'测试回复')
        self.assertEqual(self.edit.keys,['{Enter}']);self.assertEqual(self.edit.Value,'测试回复')

    def test_wrong_contact_and_draft_do_not_write(self):
        for contact in (dict(display_name='other'),self.contact):
            self.edit.Value='本人的草稿'
            with self.assertRaises(ValueError):self.sender.send(contact,'测试回复')
            self.assertEqual(self.edit.Value,'本人的草稿');self.assertEqual(self.edit.keys,[])

    def test_focus_lost_after_write_does_not_enter(self):
        def set_value(text,waitTime=0):self.edit.Value=text;self.edit.HasKeyboardFocus=False
        self.edit.SetValue=set_value
        with self.assertRaises(ValueError):self.sender.send(self.contact,'测试回复')
        self.assertEqual(self.edit.keys,[])

    def test_background_does_not_write(self):
        self.front=False
        with self.assertRaises(ValueError):self.sender.send(self.contact,'测试回复')
        self.assertEqual(self.edit.Value,'');self.assertEqual(self.edit.keys,[])

    def test_new_message_after_write_blocks_enter(self):
        checks=iter([True,False]);self.sender.refresh=lambda c:next(checks)
        with self.assertRaises(ValueError):self.sender.send(self.contact,'测试回复')
        self.assertEqual(self.edit.keys,[])

    def test_submission_error_never_uses_another_method(self):
        def send(keys,waitTime=0):self.edit.keys.append(keys);raise OSError('uncertain')
        self.edit.SendKeys=send
        with self.assertRaises(OSError):self.sender.send(self.contact,'测试回复')
        self.assertEqual(self.edit.keys,['{Enter}'])


if __name__=='__main__':unittest.main()
