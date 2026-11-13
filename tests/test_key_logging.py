# SPDX-License-Identifier: Apache-2.0
"""Synthetic SQLCipher page controls: no real keys, databases or process access."""
import contextlib
import hashlib
import hmac
import io
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from bootstrap_vendor import activate
activate()
from wechat_cli.keys import common

KEY = bytes(range(1, 33))
SALT = bytes(range(80, 96))


def page(salt):
    body = salt + b'Z' * (4096-16-64)
    mac_key = hashlib.pbkdf2_hmac('sha512', KEY, bytes(x ^ 0x3a for x in salt), 2, dklen=32)
    digest = hmac.new(mac_key, body[16:] + struct.pack('<I', 1), hashlib.sha512).digest()
    return body + digest


class KeyOutputTests(unittest.TestCase):
    def setUp(self):
        self.db = [('message/sample.db', 'unused', 4096, SALT.hex(), page(SALT))]
        self.salts = {SALT.hex(): ['message/sample.db']}

    def assert_private(self, output):
        self.assertNotIn(KEY.hex(), output)
        self.assertNotIn(SALT.hex(), output)

    def test_raw_key_encodings_keep_validation_without_printing_secrets(self):
        self.assertTrue(common.verify_enc_key(KEY, self.db[0][-1]))
        for encoded in (KEY.hex(), KEY.hex()+SALT.hex(), KEY.hex()+'aa'*16+SALT.hex()):
            with self.subTest(length=len(encoded)):
                found = {}; remaining = set(self.salts); logs = []
                common.scan_memory_for_keys(("x'"+encoded+"'").encode(), re.compile(b"x'([0-9a-fA-F]{64,192})'"),
                    self.db, self.salts, found, remaining, 0, 123, logs.append)
                self.assertEqual(found, {SALT.hex(): KEY.hex()})
                self.assertEqual(remaining, set())
                self.assert_private('\n'.join(logs))

    def test_cross_verification_and_saved_keys_remain_complete(self):
        other = bytes(range(96,112)); db=self.db+[('message/other.db','unused',4096,other.hex(),page(other))]
        salts=dict(self.salts);salts[other.hex()]=['message/other.db']
        keys={SALT.hex():KEY.hex()};logs=[]
        common.cross_verify_keys(db,salts,keys,logs.append)
        self.assertEqual(keys[other.hex()],KEY.hex())
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'all_keys.json'
            result=common.save_results(db,salts,keys,str(output),logs.append)
            saved=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result,keys)
            self.assertEqual(saved['message/sample.db']['enc_key'],KEY.hex())
            self.assertEqual(saved['message/sample.db']['salt'],SALT.hex())
        text='\n'.join(logs);self.assert_private(text);self.assertNotIn(other.hex(),text)

    def test_config_cipher_direct_candidate_has_no_key_output(self):
        from wechat_cli.keys import scanner_windows as scanner
        found={};remaining=set(self.salts);output=io.StringIO()
        with contextlib.redirect_stdout(output):
            count=scanner._verify_direct_key_candidate(KEY.hex(),SALT.hex(),self.db,self.salts,found,remaining)
        self.assertEqual(count,1);self.assertEqual(found[SALT.hex()],KEY.hex())
        self.assert_private(output.getvalue())

    def test_invalid_candidate_still_rejected(self):
        found={};logs=[]
        common.scan_memory_for_keys(("x'"+'ff'*32+SALT.hex()+"'").encode(),re.compile(b"x'([0-9a-fA-F]{64,192})'"),
            self.db,self.salts,found,set(self.salts),0,123,logs.append)
        self.assertEqual(found,{})
        with self.assertRaises(RuntimeError):common.save_results(self.db,self.salts,found,'unused',logs.append)

    def test_missing_key_does_not_print_salt(self):
        other=bytes(range(96,112));db=self.db+[('message/missing.db','unused',4096,other.hex(),page(other))]
        logs=[]
        with tempfile.TemporaryDirectory() as temp:
            common.save_results(db,{**self.salts,other.hex():['message/missing.db']},{SALT.hex():KEY.hex()},str(Path(temp)/'keys.json'),logs.append)
        self.assert_private('\n'.join(logs));self.assertNotIn(other.hex(),'\n'.join(logs))

    def test_init_does_not_echo_sensitive_exception_details(self):
        from click.testing import CliRunner
        from wechat_cli.commands.init import init
        for error in (RuntimeError(KEY.hex()+SALT.hex()),ValueError(KEY.hex()+SALT.hex())):
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as temp:
                folder=Path(temp)/'db_storage';folder.mkdir()
                with patch('wechat_cli.commands.init.STATE_DIR',temp), \
                     patch('wechat_cli.commands.init.CONFIG_FILE',str(Path(temp)/'config.json')), \
                     patch('wechat_cli.commands.init.KEYS_FILE',str(Path(temp)/'all_keys.json')), \
                     patch('wechat_cli.keys.extract_keys',side_effect=error):
                    result=CliRunner().invoke(init,['--db-dir',str(folder)])
                self.assertEqual(result.exit_code,1);self.assert_private(result.output)

    def test_extract_config_and_fallback_paths_save_without_disclosing(self):
        from wechat_cli.keys import scanner_windows as scanner
        for config_hit in (True,False):
            def config_scan(pid,regions,read_region,read_mem,db,salts,keys,remaining):
                if config_hit:keys[SALT.hex()]=KEY.hex();remaining.clear();return 1
                return 0
            with self.subTest(config=config_hit),tempfile.TemporaryDirectory() as temp:
                output=io.StringIO();saved=Path(temp)/'all_keys.json'
                with patch.object(scanner,'collect_db_files',return_value=(self.db,self.salts)), \
                     patch.object(scanner,'_get_pids',return_value=[(123,0)]), \
                     patch.object(scanner.kernel32,'OpenProcess',return_value=123), \
                     patch.object(scanner.kernel32,'CloseHandle'), \
                     patch.object(scanner,'_enum_regions',return_value=[(0,200)]), \
                     patch.object(scanner,'_read_mem',return_value=("x'"+KEY.hex()+SALT.hex()+"'").encode()), \
                     patch.object(scanner,'_scan_config_cipher',side_effect=config_scan), \
                     contextlib.redirect_stdout(output):
                    result=scanner.extract_keys('unused',str(saved))
                self.assertEqual(result[SALT.hex()],KEY.hex())
                self.assertEqual(json.loads(saved.read_text(encoding='utf-8'))['message/sample.db']['enc_key'],KEY.hex())
                self.assert_private(output.getvalue())


if __name__=='__main__':unittest.main()
