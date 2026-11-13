# SPDX-License-Identifier: Apache-2.0
"""Synthetic end-to-end check. Never accesses a real WeChat process or network."""
from pathlib import Path
import argparse
from contextlib import contextmanager
import hashlib
import importlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from bootstrap_vendor import activate
activate()
import zstandard


@contextmanager
def database(path):
    c = sqlite3.connect(path)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def command(*args):
    subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    with tempfile.TemporaryDirectory(prefix="portable-rag-synthetic-") as temporary:
        base = Path(temporary)
        plain = base / "plain"
        (plain / "contact").mkdir(parents=True)
        (plain / "message").mkdir()
        with database(plain / "contact/contact.db") as c:
            c.execute("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT)")
            c.executemany("INSERT INTO contact VALUES(?,?,?)", [("sample_self", "测试本人", ""), ("sample_partner", "测试对方", "唯一测试备注"), ("sample_other", "不应被导出的联系人", "")])
        table = "Msg_" + hashlib.md5(b"sample_partner").hexdigest()
        for shard in range(2):
            with database(plain / f"message/message_{shard}.db") as c:
                c.execute("CREATE TABLE Name2Id(user_name TEXT)")
                c.executemany("INSERT INTO Name2Id VALUES(?)", [("sample_self",), ("sample_partner",)])
                c.execute(f"CREATE TABLE [{table}](local_id INTEGER,server_id TEXT,local_type INTEGER,sort_seq INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB,WCDB_CT_message_content INTEGER)")
                c.execute("CREATE TABLE unrelated_private_table(text TEXT)")
                c.execute("INSERT INTO unrelated_private_table VALUES('must not copy unrelated table')")
                local = 0
                for session in range(shard * 10, (shard + 1) * 10):
                    for turn in range(8):
                        timestamp = 1700000000 + session * 86400 + turn * 180
                        question = f"第{session}天的话题{turn}，今天这件事情你觉得怎么样？"
                        if session==19:question+=' OUTSIDE_DATE_CANARY'
                        if session == 0 and turn == 0:
                            question += " 测试称呼 联系电话13800000000"
                        rows = [(2, question), (1, f"我觉得第{turn}件事情可以一起看看"), (1, "我们慢慢聊就好")]
                        for index, (sender, text) in enumerate(rows):
                            local += 1
                            compressed = session == 1 and turn == 1 and index == 0
                            content = zstandard.ZstdCompressor().compress(text.encode()) if compressed else text
                            c.execute(f"INSERT INTO [{table}] VALUES(?,?,?,?,?,?,?,?)", (local, str((shard + 1) * 100000 + local), 1, local, timestamp + index * 10, sender, content, 4 if compressed else 0))
        snapshot = base / "snapshot"
        command(ROOT / "tools/export_for_cleaning.py", "--plaintext-dir", plain, "--self-username", "sample_self", "--partner-name", "唯一测试备注", "--output", snapshot)
        meta = json.loads((snapshot / "plaintext-cache/_mtimes.json").read_text(encoding="utf-8"))
        assert all(not Path(v["path"]).is_absolute() for v in meta.values())
        with database(snapshot / "plaintext-cache/contact.db") as c:
            assert c.execute("SELECT count(*) FROM contact").fetchone()[0] == 2
        with database(snapshot / "plaintext-cache/message_0.db") as c:
            assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='unrelated_private_table'").fetchone()
        # Move the entire snapshot to exercise relative paths independently of the export location.
        moved = base / "snapshot-moved"
        snapshot.rename(moved)
        redactions = base / "redactions.json"
        redactions.write_text(json.dumps({"测试称呼": "[昵称]"}), encoding="utf-8")
        clean = base / "clean"
        command(ROOT / "tools/clean_wechat_persona.py", "--source", moved, "--output", clean, "--self-username", "sample_self", "--partner-name", "唯一测试备注", "--redactions", redactions)
        records = [json.loads(line) for line in (clean / "clean_messages.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(records) == 480
        assert all(r["speaker"] in ("我", "对方") for r in records)
        assert "[昵称]" in records[0]["text"] and "[手机号]" in records[0]["text"]
        assert not any("13800000000" in r["text"] for r in records)
        assert all(r["role"] == ("partner" if r["id"].split(":")[1] and (int(r["id"].split(":")[1]) - 1) % 3 == 0 else "self") for r in records)
        assert not list(clean.glob("*.zip"))
        episodes = base / "episodes"
        builder = importlib.import_module("build_persona_episodes")
        config = base / "credentials.json"
        config.write_text((ROOT / "config/credentials.example.json").read_text(encoding="utf-8"), encoding="utf-8")
        try:
            builder.api.credentials(config)
            raise AssertionError("Empty configuration must be rejected before any network call")
        except ValueError as error:
            assert "URL" in str(error)
        config.write_text(json.dumps({"embedding_endpoint": "https://embeddings.example.invalid/v1/embeddings", "embedding_model": "synthetic-model", "embedding_token": "synthetic-test-only"}), encoding="utf-8")
        args = argparse.Namespace(source=clean, previous=None, output=episodes, cache=None, config=config)
        builder.prepare(args)
        docs = [json.loads(line) for line in (episodes / "rag_documents.jsonl").read_text(encoding="utf-8").splitlines()]
        assert docs and any(len(d["metadata"]["target_bubbles"]) == 2 for d in docs)
        assert all(not (set(d["metadata"]["prompt_message_ids"]) & set(d["metadata"]["target_message_ids"])) for d in docs)
        def mock_embeddings(endpoint, key, model, inputs):
            assert model == "synthetic-model" and key == "synthetic-test-only"
            data = []
            for index, value in enumerate(inputs):
                vector = [0.0] * 7
                vector[int(hashlib.sha256(value.encode()).hexdigest(), 16) % 7] = 1.0
                data.append({"index": index, "embedding": vector})
            return {"data": data}
        with patch.object(builder.api, "post", side_effect=mock_embeddings) as mocked:
            builder.embed(args)
            assert mocked.call_count > 0
        with patch.object(builder.api, "post", side_effect=AssertionError("Completed vectors must be reused")):
            builder.embed(args)
        with database(episodes / "persona-rag.sqlite") as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert c.execute("SELECT count(*) FROM documents WHERE length(vector)=28 AND length(context_vector)=28").fetchone()[0] == len(docs)
        # One-day selection: no minimum-100 gate, no out-of-range context, no
        # raw sensitive values in either query/context vectors or stored documents.
        selected_day=datetime.datetime.fromtimestamp(1700000000,datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
        selected_clean=base/'selected-clean';selected_episodes=base/'selected-episodes'
        redactions.write_text(json.dumps({'测试称呼':'[昵称]','我觉得':'[表达]','我们':'[称谓]'}),encoding='utf-8')
        command(ROOT/'tools/clean_wechat_persona.py','--source',moved,'--output',selected_clean,
            '--self-username','sample_self','--partner-name','唯一测试备注','--redactions',redactions,
            '--start-date',selected_day,'--end-date',selected_day,'--redaction-policy','replace')
        selected_records=list(builder.api.lines(selected_clean/'clean_messages.jsonl'))
        assert len(selected_records)==24 and all(r['timestamp'].startswith(selected_day) for r in selected_records)
        selected_report=json.loads((selected_clean/'cleaning_report.json').read_text(encoding='utf-8'))
        assert selected_report['filters']['outside_date_range']==456 and selected_report['small_sample']
        assert selected_report['splits']['train']>0 and selected_report['splits']['test']==0
        assert json.loads((selected_clean/'style_profile.json').read_text(encoding='utf-8'))['evidence_message_count']==0
        selected_args=argparse.Namespace(source=selected_clean,previous=None,output=selected_episodes,cache=None,config=config)
        builder.prepare(selected_args)
        def selected_embeddings(endpoint,key,model,inputs):
            for text in inputs:
                assert all(value not in text for value in ('13800000000','测试称呼','OUTSIDE_DATE_CANARY'))
            return mock_embeddings(endpoint,key,model,inputs)
        with patch.object(builder.api,'post',side_effect=selected_embeddings):builder.embed(selected_args)
        with database(selected_episodes/'persona-rag.sqlite') as c:
            for text,embedding,context in c.execute('SELECT text,embedding_text,context_text FROM documents'):
                assert all(value not in text+embedding+context for value in ('13800000000','测试称呼','OUTSIDE_DATE_CANARY'))
        # Empty selections fail before creating output or requesting any vectors.
        empty=base/'empty-clean'
        result=subprocess.run([sys.executable,str(ROOT/'tools/clean_wechat_persona.py'),'--source',str(moved),'--output',str(empty),
            '--self-username','sample_self','--partner-name','唯一测试备注','--start-date','2099-01-01'],capture_output=True)
        assert result.returncode!=0 and not empty.exists()
        saved_config = json.loads(config.read_text(encoding="utf-8"))
        changed = dict(saved_config, embedding_model="a-different-model")
        config.write_text(json.dumps(changed), encoding="utf-8")
        with patch.object(builder.api, "post", side_effect=AssertionError("Model mismatch must fail before network")):
            try:
                builder.embed(args)
                raise AssertionError("Different models must not share an index")
            except ValueError:
                pass
        config.write_text(json.dumps(saved_config), encoding="utf-8")
        with database(episodes / "persona-rag.sqlite") as c:
            assert json.loads(c.execute("SELECT value FROM metadata WHERE key='dimension'").fetchone()[0]) == 7
            assert json.loads(c.execute("SELECT value FROM metadata WHERE key='model'").fetchone()[0]) == "synthetic-model"
        for field in ["embedding_model", "embedding_token"]:
            missing = dict(saved_config, **{field: ""})
            config.write_text(json.dumps(missing), encoding="utf-8")
            try:
                builder.api.credentials(config)
                raise AssertionError("A required config field was empty")
            except ValueError:
                pass
        config.write_text(json.dumps(saved_config), encoding="utf-8")
        assert not list(base.rglob("*.zip"))
        print("PASS: synthetic export/clean/chunk/embed, one-day UTC+8 selection, replacement-only embedding inputs, small-sample partitions, empty selection and no-network resume")


if __name__ == "__main__":
    main()
