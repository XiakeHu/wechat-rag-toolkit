# SPDX-License-Identifier: Apache-2.0
"""Synthetic redaction and date boundaries; never opens real chats or networks."""
import datetime
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import clean_wechat_persona as cleaner
from build_persona_episodes import usable


class CleaningOptionsTests(unittest.TestCase):
    def test_date_range_contains_full_final_day(self):
        start,end=cleaner.date_bounds('2026-11-01','2026-11-02')
        self.assertEqual(end-start,2*86400)
        self.assertEqual(datetime.datetime.fromtimestamp(start,datetime.timezone.utc).isoformat(),'2026-10-31T16:00:00+00:00')
        self.assertEqual(datetime.datetime.fromtimestamp(end,datetime.timezone.utc).isoformat(),'2026-11-02T16:00:00+00:00')
        self.assertTrue(start<=end-1<end)

    def test_open_date_limits(self):
        self.assertEqual(cleaner.date_bounds(),(None,None))
        self.assertIsNone(cleaner.date_bounds(end='2026-11-02')[0])
        self.assertIsNone(cleaner.date_bounds(start='2026-11-02')[1])

    def test_invalid_dates_rejected(self):
        for start,end in [('2026-11-03','2026-11-02'),('2026-02-30',None),('2026-1-01',None),('tomorrow',None)]:
            with self.assertRaises(ValueError):cleaner.date_bounds(start,end)

    def test_builtin_and_custom_redaction(self):
        previous=cleaner.EXTRA_REDACTIONS
        try:
            cleaner.EXTRA_REDACTIONS={'示例姓名全称':'[姓名]','示例姓名':'[简称]'}
            text,flags=cleaner.clean('示例姓名全称 13800000000 sample@example.invalid 密码:example-secret')
            self.assertIn('[姓名]',text);self.assertNotIn('[简称]全称',text)
            for original in ('示例姓名','13800000000','sample@example.invalid','example-secret'):self.assertNotIn(original,text)
            self.assertIn('手机号',flags);self.assertIn('自定义替换',flags)
        finally:cleaner.EXTRA_REDACTIONS=previous

    def test_redacted_turns_require_explicit_opt_in(self):
        row=dict(kind='text',redaction_flags=['手机号'])
        self.assertFalse(usable(row))
        self.assertTrue(usable(dict(row,allow_redacted_for_rag=True)))
        self.assertFalse(usable(dict(row,kind='unparsed_media',allow_redacted_for_rag=True)))


if __name__=='__main__':unittest.main()
