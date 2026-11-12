"""Private, bounded conversation storage; archived turns are searchable by the LLM."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

from rag import BM25Index, Chunk, tokenize


class ConversationMemory:
    def __init__(self, path: Path, ttl_days: int = 7, max_sessions: int = 1000):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.ttl = ttl_days * 86400
        self.max_sessions = max_sessions
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, updated REAL NOT NULL,
                    summary TEXT NOT NULL DEFAULT '', summarized_to INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session, id);
                CREATE TABLE IF NOT EXISTS passages (
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, message_id INTEGER NOT NULL,
                    text TEXT NOT NULL, vector TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS passages_session ON passages(session);
            ''')
        path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA secure_delete=ON')
        return db

    def touch(self, session: str):
        now = time.time()
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE updated < ?', (now - self.ttl,))
            db.execute('INSERT OR IGNORE INTO sessions(id, updated) VALUES (?, ?)', (session, now))
            db.execute('UPDATE sessions SET updated=? WHERE id=?', (now, session))
            db.execute('DELETE FROM sessions WHERE id IN (SELECT id FROM sessions ORDER BY updated DESC LIMIT -1 OFFSET ?)', (self.max_sessions,))
            db.execute('DELETE FROM messages WHERE session NOT IN (SELECT id FROM sessions)')
            db.execute('DELETE FROM passages WHERE session NOT IN (SELECT id FROM sessions)')

    def snapshot(self, session: str) -> dict:
        self.touch(session)
        with self.connect() as db:
            row = dict(db.execute('SELECT * FROM sessions WHERE id=?', (session,)).fetchone())
            row['messages'] = [dict(m) for m in db.execute(
                'SELECT id, role, content FROM messages WHERE session=? ORDER BY id', (session,))]
            return row

    def summarize(self, session: str, text: str, through: int):
        with self.connect() as db:
            db.execute('UPDATE sessions SET summary=?, summarized_to=? WHERE id=?',
                       (text[:1200], through, session))

    def append(self, session: str, messages: list[dict], embedder):
        # Small passages prevent MiniLM from embedding only the start of a long answer.
        passages = []
        for index, message in enumerate(messages):
            words = message['content'].split()
            for start in range(0, len(words), 80):
                text = f"{message['role']}: " + ' '.join(words[start:start + 100])
                passages.append((index, text))
        vectors = list(embedder.embed([text for _, text in passages])) if passages else []
        with self.connect() as db:
            ids = []
            for message in messages:
                cursor = db.execute('INSERT INTO messages(session,role,content,created) VALUES (?,?,?,?)',
                                    (session, message['role'], message['content'][:6000], time.time()))
                ids.append(cursor.lastrowid)
            for (index, text), vector in zip(passages, vectors):
                db.execute('INSERT INTO passages(session,message_id,text,vector) VALUES (?,?,?,?)',
                           (session, ids[index], text, json.dumps([float(x) for x in vector])))
            db.execute('UPDATE sessions SET updated=? WHERE id=?', (time.time(), session))

    def search(self, session: str, query: str, embedder, limit: int = 4, order: str = 'relevance', role: str = 'any') -> list[dict]:
        self.touch(session)
        with self.connect() as db:
            rows = [dict(r) for r in db.execute(
                'SELECT p.id,p.message_id,p.text,p.vector,m.role FROM passages p JOIN messages m ON m.id=p.message_id WHERE p.session=? ORDER BY p.id', (session,))]
            message_numbers = {row[0]:number for number,row in enumerate(db.execute('SELECT id FROM messages WHERE session=? ORDER BY id',(session,)),1)}
        if role != 'any':
            rows = [row for row in rows if row['role'] == role]
        def result(row):
            return {'message_number': message_numbers[row['message_id']], 'role': row['role'], 'text': row['text']}
        if order != 'relevance':
            rows.sort(key=lambda row: (row['message_id'],row['id']), reverse=order=='newest')
            return [result(row) for row in rows[:limit]]
        if not rows:
            return []
        vector = list(next(embedder.query_embed(query)))
        norm = math.sqrt(sum(float(x) ** 2 for x in vector)) or 1
        def cosine(row):
            other = json.loads(row['vector'])
            if len(other) != len(vector):
                raise ValueError('Memory embeddings must be rebuilt after changing embedding models.')
            other_norm = math.sqrt(sum(x*x for x in other)) or 1
            return sum(float(a)*b for a,b in zip(vector, other)) / (norm * other_norm)
        semantic = sorted(rows, key=cosine, reverse=True)
        chunks = [Chunk(str(row['id']), row['text'], tokenize(row['text'])) for row in rows]
        lexical = BM25Index(chunks).search(query, max(12, limit * 3))
        scores = {}
        for rank, row in enumerate(semantic[:12]):
            scores[row['id']] = 1/(61+rank)
        for rank, (chunk, _) in enumerate(lexical):
            key = int(chunk.source)
            scores[key] = scores.get(key, 0) + 1/(61+rank)
        ranked = sorted(rows, key=lambda row: scores.get(row['id'], 0), reverse=True)[:limit]
        return [result(row) for row in ranked]

    def delete(self, session: str):
        with self.connect() as db:
            db.execute('DELETE FROM passages WHERE session=?', (session,))
            db.execute('DELETE FROM messages WHERE session=?', (session,))
            db.execute('DELETE FROM sessions WHERE id=?', (session,))
