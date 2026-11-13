# SPDX-License-Identifier: Apache-2.0
"""Export one private conversation using wechat-cli's local database reader.
No process scanning, network requests or message sending is performed here.
Run wechat-cli init separately when a database key has not been configured.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile

from bootstrap_vendor import activate
activate()
from wechat_cli.core.messages import decompress_content


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def export(a, contact, databases):
    with closing(readonly(contact)) as c:
        own = c.execute("SELECT username,nick_name,remark FROM contact WHERE username=?", (a.self_username,)).fetchall()
        partner = c.execute("SELECT username,nick_name,remark FROM contact WHERE username=? OR nick_name=? OR remark=?", (a.partner_name,) * 3).fetchall()
    if len(own) != 1 or len(partner) != 1 or own[0][0] == partner[0][0]:
        raise ValueError("Self and partner must resolve to two distinct, unambiguous contacts")
    if partner[0][0].endswith("@chatroom"):
        raise ValueError("This two-person persona pipeline does not support group chats")
    table = "Msg_" + hashlib.md5(partner[0][0].encode()).hexdigest()
    cache = a.output / "plaintext-cache"
    cache.mkdir(parents=True)
    meta = {"contact/contact.db": {"path": "plaintext-cache/contact.db"}}
    with closing(sqlite3.connect(cache / "contact.db")) as target:
        target.execute("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT)")
        target.executemany("INSERT INTO contact VALUES(?,?,?)", [own[0], (partner[0][0], partner[0][1], a.partner_name)])
        target.commit()
    export_dir = a.output / "export"
    export_dir.mkdir()
    count = 0
    with (export_dir / "messages.jsonl").open("w", encoding="utf-8") as f:
        for rel, path in databases:
            name = Path(rel.replace("\\", "/")).name
            if not re.fullmatch(r"message_\d+\.db", name):
                continue
            with closing(readonly(path)) as c:
                if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                query = f"""SELECT m.local_id,m.server_id,m.local_type,m.sort_seq,m.create_time,
                    n.user_name,m.message_content,m.WCDB_CT_message_content
                    FROM [{table}] m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id
                    ORDER BY m.create_time,m.sort_seq,m.local_id"""
                target_path = cache / name
                with closing(sqlite3.connect(target_path)) as target:
                    target.execute("CREATE TABLE Name2Id(user_name TEXT)")
                    target.executemany("INSERT INTO Name2Id VALUES(?)", [(own[0][0],), (partner[0][0],), ("unresolved_sender",)])
                    target.execute(f"CREATE TABLE [{table}](local_id INTEGER,server_id TEXT,local_type INTEGER,sort_seq INTEGER,create_time INTEGER,real_sender_id INTEGER)")
                    for local, server, kind, seq, stamp, sender, content, ct in c.execute(query):
                        sender_id = 1 if sender == own[0][0] else 2 if sender == partner[0][0] else 3
                        target.execute(f"INSERT INTO [{table}] VALUES(?,?,?,?,?,?)", (local, str(server or ""), kind, seq, stamp, sender_id))
                        text = decompress_content(content, ct) if kind & 0xffffffff == 1 else ""
                        if text is None:
                            raise ValueError("A text message could not be decoded; no complete export was produced")
                        record = {"message_id": name + ":" + str(local), "timestamp": datetime.fromtimestamp(stamp).astimezone().isoformat(), "text": text}
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        count += 1
                    target.commit()
                meta["message/" + name] = {"path": "plaintext-cache/" + name}
    if count == 0:
        raise ValueError("No messages found on this computer for the selected conversation")
    (cache / "_mtimes.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"messages": count, "output": str(a.output), "network_calls": 0}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="wechat-cli config.json created on this computer")
    source.add_argument("--plaintext-dir", type=Path, help="Existing decrypted root containing contact/contact.db and message/message_N.db")
    p.add_argument("--self-username", required=True)
    p.add_argument("--partner-name", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output = a.output.resolve()
    if a.output.exists():
        raise ValueError("Choose a new output directory; existing data will not be overwritten")
    if a.plaintext_dir:
        base = a.plaintext_dir.resolve()
        if base.is_relative_to(a.output) or a.output.is_relative_to(base):
            raise ValueError("Source and output directories must be separate")
        export(a, base / "contact/contact.db", [("message/" + path.name, path) for path in sorted((base / "message").glob("message_*.db"))])
    else:
        from wechat_cli.core.context import AppContext
        from wechat_cli.core.db_cache import DBCache
        with tempfile.TemporaryDirectory(prefix="wechat-rag-export-") as work:
            DBCache.CACHE_DIR = work
            DBCache.MTIME_FILE = str(Path(work) / "_mtimes.json")
            app = AppContext(str(a.config.resolve()))
            import atexit
            atexit.unregister(app.cache.cleanup)
            contact = app.cache.get("contact/contact.db")
            if not contact:
                raise ValueError("No contact database key is available")
            export(a, contact, [(rel, app.cache.get(rel)) for rel in app.msg_db_keys if app.cache.get(rel)])


if __name__ == "__main__":
    main()
