"""Snapshots financeiros para reversão curta com conferência de concorrência."""
import hashlib
import json

TABLES=('assets','contracts','payments','contributions','wallet_entries','wallet_sources','settings')

def snapshot(connection):
    state={}
    for table in TABLES:
        if table=='settings':
            rows=connection.execute("SELECT * FROM settings WHERE key IN ('goal','rate') ORDER BY key")
        else:
            order='key' if table=='payments' else 'reference' if table=='wallet_sources' else 'id'
            rows=connection.execute(f'SELECT * FROM {table} ORDER BY {order}')
        state[table]=[dict(row) for row in rows]
    return json.dumps(state,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def fingerprint(state):
    return hashlib.sha256(state.encode()).hexdigest()

def restore(connection, state):
    # O conteúdo vem exclusivamente do snapshot criado pelo próprio servidor.
    data=json.loads(state)
    for table in TABLES:
        if table=='settings':
            connection.execute("DELETE FROM settings WHERE key IN ('goal','rate')")
        else:
            connection.execute(f'DELETE FROM {table}')
        for row in data[table]:
            columns=list(row)
            sql=f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
            connection.execute(sql,[row[column] for column in columns])
