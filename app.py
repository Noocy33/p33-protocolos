import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import csv
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from leitura import extrair

ROOT = Path(__file__).resolve().parent
DB = Path(os.getenv('P33_DB', str(ROOT / 'dados' / 'p33.sqlite3')))
TYPES = {'atestado': 'Atestado', 'ponto': 'Ocorrência de ponto'}
STATUS = {'recebido': 'A conferir', 'conferido': 'Conferido',
          'encaminhado': 'Encaminhado', 'baixado': 'Lançamento confirmado'}
NEXT = dict(zip(list(STATUS)[:-1], list(STATUS)[1:]))
LIMIT = 12 * 1024 * 1024
TZ = ZoneInfo('America/Sao_Paulo')
READERS = threading.BoundedSemaphore(2)

def agora():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def db():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        with con:
            yield con
    finally:
        con.close()

def init(demo=False):
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios(
          login TEXT PRIMARY KEY, salt TEXT NOT NULL, senha TEXT NOT NULL, perfil TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessoes(
          id TEXT PRIMARY KEY, login TEXT NOT NULL REFERENCES usuarios(login) ON DELETE CASCADE,
          expira TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS protocolos(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ano INTEGER NOT NULL,
          criado TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'recebido',
          tipo TEXT NOT NULL DEFAULT '', nome TEXT NOT NULL DEFAULT '',
          cpf TEXT NOT NULL DEFAULT '', matricula TEXT NOT NULL DEFAULT '',
          setor TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '',
          dias TEXT NOT NULL DEFAULT '', destino TEXT NOT NULL DEFAULT '',
          referencia TEXT NOT NULL DEFAULT '', revisao INTEGER NOT NULL DEFAULT 1,
          hash TEXT UNIQUE, arquivo BLOB, mime TEXT, origem TEXT NOT NULL DEFAULT 'Manual');
        CREATE TABLE IF NOT EXISTS auditoria(
          id INTEGER PRIMARY KEY, protocolo INTEGER NOT NULL REFERENCES protocolos(id),
          login TEXT NOT NULL, evento TEXT NOT NULL, instante TEXT NOT NULL);
        """)
        if demo:
            if DB.name != 'p33-demo.sqlite3':
                raise SystemExit('A demonstração requer um banco separado.')
            save_user(con, 'supervisao', secrets.token_urlsafe(32), 'supervisao', True)
            save_user(con, 'recepcao', secrets.token_urlsafe(32), 'recepcao', True)
            if not con.execute('SELECT 1 FROM protocolos LIMIT 1').fetchone():
                today = datetime.now(TZ).date()
                rows = [
                    ('atestado', 'Mariana Silva', 'MAT-1042', 'Clínica Médica', 'recebido'),
                    ('ponto', 'Gabriel Almeida', 'MAT-1081', 'Centro Cirúrgico', 'recebido'),
                    ('atestado', 'Camila Santos', 'MAT-1018', 'CME', 'conferido'),
                    ('ponto', 'Lucas Oliveira', 'MAT-1024', 'Pronto Atendimento', 'encaminhado'),
                    ('atestado', 'Paula Ferreira', 'MAT-1033', 'Clínica Médica', 'baixado'),
                ]
                for offset, (tipo, nome, mat, setor, status) in enumerate(rows):
                    cur = con.execute(
                        'INSERT INTO protocolos (ano,criado,tipo,nome,matricula,setor,data,status,destino,referencia) VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (today.year, agora(), tipo, nome, mat, setor, str(today - timedelta(days=offset)),
                         status, 'RH' if status in ('encaminhado', 'baixado') else '',
                         'DEMO-001' if status == 'baixado' else ''))
                    auditar(con, cur.lastrowid, 'supervisao', 'Amostra fictícia criada')

def senha_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

def save_user(con, login, password, role, ignore=False):
    salt = secrets.token_hex(16)
    sql = 'INSERT OR IGNORE' if ignore else 'INSERT'
    con.execute(f'{sql} INTO usuarios VALUES (?,?,?,?)', (login, salt, senha_hash(password, salt), role))

def auditar(con, pid, login, event):
    con.execute('INSERT INTO auditoria (protocolo,login,evento,instante) VALUES (?,?,?,?)',
                (pid, login, event, agora()))

def codigo(row):
    return f"P33-{row['ano']}-{row['id']:05d}"

def cpf_valido(value):
    if len(value) != 11 or len(set(value)) == 1:
        return False
    for length in (9, 10):
        check = (sum(int(n) * w for n, w in zip(value, range(length + 1, 1, -1))) * 10) % 11 % 10
        if check != int(value[length]):
            return False
    return True

def campos_form(obj):
    limits = {'nome': 120, 'cpf': 18, 'matricula': 30, 'tipo': 20, 'setor': 80,
              'data': 10, 'dias': 3}
    result = {}
    for key, limit in limits.items():
        value = obj.get(key, '')
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise ValueError(f'Valor inválido em {key}.')
        result[key] = value.strip()
    if result['tipo'] and result['tipo'] not in TYPES:
        raise ValueError('Tipo inválido.')
    if result['cpf']:
        if not re.fullmatch(r'[0-9.\- ]+', result['cpf']):
            raise ValueError('CPF inválido.')
        result['cpf'] = re.sub(r'[^0-9]', '', result['cpf'])
        if len(result['cpf']) != 11:
            raise ValueError('Informe um CPF com 11 dígitos.')
    if result['matricula'] and not re.fullmatch(r'[A-Za-z0-9-]{2,30}', result['matricula']):
        raise ValueError('Matrícula inválida.')
    if result['data']:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', result['data']):
            raise ValueError('Use uma data válida.')
        date.fromisoformat(result['data'])
    if result['dias'] and not re.fullmatch(r'[0-9]{1,3}', result['dias']):
        raise ValueError('Quantidade de dias inválida.')
    return result

def detectar(data):
    if data.startswith(b'%PDF-'):
        return 'application/pdf', '.pdf'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg'
    raise ValueError('Envie um PDF, PNG ou JPEG válido.')

FIELDS = 'id,ano,criado,status,tipo,nome,matricula,setor,data,dias,destino,referencia,revisao,origem,mime'

class Handler(BaseHTTPRequestHandler):
    server_version = 'P33'
    def log_message(self, *_):
        pass

    def reply(self, code, data=b'', mime='application/json; charset=utf-8', headers=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def body(self, max_size=16384):
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= max_size:
            raise ValueError('Conteúdo vazio ou acima do limite de envio.')
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError('Envio incompleto.')
        return data

    def objeto(self):
        obj = json.loads(self.body())
        if not isinstance(obj, dict):
            raise ValueError('Formato de formulário inválido.')
        return obj

    def sid(self):
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        return cookie['p33_session'].value if 'p33_session' in cookie else ''

    def session(self, con):
        return con.execute(
            'SELECT u.login,u.perfil FROM sessoes s JOIN usuarios u USING(login) WHERE id=? AND expira>?',
            (hashlib.sha256(self.sid().encode()).hexdigest(), agora())).fetchone()

    def cookie(self, value, age):
        secure = '; Secure' if os.getenv('P33_SECURE_COOKIE') == '1' else ''
        return f'p33_session={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}'

    def do_GET(self):
        self.run_request('GET')

    def do_POST(self):
        self.run_request('POST')

    def run_request(self, method):
        try:
            self.route(method, urlsplit(self.path).path)
        except (ValueError, UnicodeError, TypeError, KeyError) as exc:
            msg = str(exc) if type(exc) is ValueError else 'Dados inválidos.'
            self.reply(400, {'erro': msg})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except sqlite3.OperationalError:
            self.reply(503, {'erro': 'Banco temporariamente indisponível. Tente novamente.'})

    def route(self, method, path):
        assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                  '/style.css': ('style.css', 'text/css')}
        if method == 'GET' and path in assets:
            name, mime = assets[path]
            return self.reply(200, (ROOT / 'web' / name).read_bytes(), mime + '; charset=utf-8')
        if method == 'POST' and self.headers.get('X-P33-Request') != '1':
            return self.reply(403, {'erro': 'Solicitação inválida.'})
        if method == 'GET' and path == '/api/config':
            return self.reply(200, {'demo': self.server.demo})
        with db() as con:
            if method == 'POST' and path == '/api/demo-login' and self.server.demo:
                profile = self.objeto().get('perfil', 'supervisao')
                if profile not in ('supervisao', 'recepcao'):
                    raise ValueError('Perfil inválido.')
                return self.start_session(con, {'login': profile, 'perfil': profile})
            if method == 'POST' and path == '/api/login':
                return self.login(con)
            user = self.session(con)
            if user is None:
                return self.reply(401, {'erro': 'Entre com sua conta para continuar.'})
            supervisor = user['perfil'] == 'supervisao'
            if method == 'GET' and path == '/api/me':
                return self.reply(200, dict(user))
            if method == 'POST' and path == '/api/logout':
                con.execute('DELETE FROM sessoes WHERE id=?', (hashlib.sha256(self.sid().encode()).hexdigest(),))
                con.commit()
                return self.reply(200, {}, headers={'Set-Cookie': self.cookie('', 0)})
            if method == 'GET' and path == '/api/protocolos':
                fields = FIELDS if supervisor else 'id,ano,criado,status,tipo,setor,data,revisao,origem'
                rows = con.execute(f'SELECT {fields} FROM protocolos ORDER BY id DESC').fetchall()
                return self.reply(200, [dict(row, codigo=codigo(row)) for row in rows])
            if method == 'GET' and path == '/api/exportar' and supervisor:
                return self.exportar(con)
            if method == 'POST' and path == '/api/upload':
                return self.upload(con, user)
            if method == 'POST' and path == '/api/protocolos':
                fields = campos_form(self.objeto())
                if not fields['tipo'] or not (fields['nome'] or fields['matricula'] or fields['cpf']):
                    raise ValueError('Informe o tipo e ao menos um identificador.')
                cur = con.execute(
                    'INSERT INTO protocolos (ano,criado,nome,cpf,matricula,tipo,setor,data,dias) VALUES (?,?,?,?,?,?,?,?,?)',
                    (datetime.now(TZ).year, agora(), *fields.values()))
                auditar(con, cur.lastrowid, user['login'], 'Recebimento manual registrado')
                con.commit()
                created = con.execute('SELECT id,ano FROM protocolos WHERE id=?', (cur.lastrowid,)).fetchone()
                return self.reply(201, {'id': created['id'], 'codigo': codigo(created)})
            match = re.fullmatch(r'/api/protocolos/(\d+)(/arquivo|/reler)?', path)
            if not match:
                return self.reply(404, {'erro': 'Página indisponível.'})
            if not supervisor:
                return self.reply(403, {'erro': 'Acesso exclusivo da supervisão.'})
            pid, suffix = int(match[1]), match[2]
            row = con.execute(f'SELECT {FIELDS},cpf FROM protocolos WHERE id=?', (pid,)).fetchone()
            if row is None:
                return self.reply(404, {'erro': 'Protocolo não encontrado.'})
            if method == 'GET' and suffix == '/arquivo' and row['mime']:
                data = con.execute('SELECT arquivo FROM protocolos WHERE id=?', (pid,)).fetchone()[0]
                _, ext = detectar(data)
                auditar(con, pid, user['login'], 'Original baixado')
                con.commit()
                return self.reply(200, data, row['mime'],
                                  {'Content-Disposition': f'attachment; filename="{codigo(row)}{ext}"'})
            if method == 'POST' and suffix == '/reler' and row['mime'] and row['status'] == 'recebido':
                data = con.execute('SELECT arquivo FROM protocolos WHERE id=?', (pid,)).fetchone()[0]
                return self.reply(200, self.ler(con, user, pid, data, detectar(data)[1], row['revisao']))
            if method == 'GET' and not suffix:
                events = con.execute('SELECT login,evento,instante FROM auditoria WHERE protocolo=? ORDER BY id DESC', (pid,))
                return self.reply(200, dict(row, codigo=codigo(row), eventos=[dict(event) for event in events]))
            if method == 'POST' and not suffix:
                return self.atualizar(con, user, row)
            return self.reply(404, {'erro': 'Operação indisponível.'})

    def login(self, con):
        obj = self.objeto()
        login, password = obj.get('login', ''), obj.get('senha', '')
        if not isinstance(login, str) or not isinstance(password, str) or len(password) > 256:
            raise ValueError('Credenciais inválidas.')
        row = con.execute('SELECT * FROM usuarios WHERE login=?', (login.strip().lower(),)).fetchone()
        derived = senha_hash(password, row['salt'] if row else '00' * 16)
        if not row or not secrets.compare_digest(derived, row['senha']):
            return self.reply(401, {'erro': 'Usuário ou senha inválidos.'})
        return self.start_session(con, row)

    def start_session(self, con, row):
        token = secrets.token_urlsafe(32)
        con.execute('DELETE FROM sessoes WHERE expira<=?', (agora(),))
        con.execute('INSERT INTO sessoes VALUES (?,?,?)',
                    (hashlib.sha256(token.encode()).hexdigest(), row['login'],
                     (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(timespec='seconds')))
        con.commit()
        self.reply(200, {'login': row['login'], 'perfil': row['perfil']},
                   headers={'Set-Cookie': self.cookie(token, 28800)})

    def upload(self, con, user):
        data = self.body(LIMIT)
        mime, ext = detectar(data)
        digest = hashlib.sha256(data).hexdigest()
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT id,ano FROM protocolos WHERE hash=?', (digest,)).fetchone()
        if existing:
            return self.reply(409, {'erro': f'Este arquivo já foi recebido. Protocolo {codigo(existing)}.',
                                    'id': existing['id']})
        year = datetime.now(TZ).year
        cur = con.execute('INSERT INTO protocolos (ano,criado,hash,arquivo,mime,origem) VALUES (?,?,?,?,?,?)',
                          (year, agora(), digest, data, mime, 'PDF' if ext == '.pdf' else 'Foto'))
        pid = cur.lastrowid
        auditar(con, pid, user['login'], 'Documento recebido e protocolado')
        con.commit()
        result = self.ler(con, user, pid, data, ext, 1)
        self.reply(201, {'id': pid, 'codigo': codigo({'ano': year, 'id': pid}),
                         'metodos': result['metodos'], 'avisos': result['avisos']})

    def ler(self, con, user, pid, data, ext, revisao):
        if not READERS.acquire(blocking=False):
            return {'metodos': [], 'avisos': ['Protocolo salvo. A supervisão pode tentar a leitura novamente.']}
        try:
            result = extrair(data, ext)
        finally:
            READERS.release()
        fields = result['campos']
        if fields:
            assignments = ','.join(f"{key}=CASE WHEN {key}='' THEN ? ELSE {key} END" for key in fields)
            cur = con.execute(
                f"UPDATE protocolos SET {assignments},revisao=revisao+1 WHERE id=? AND status='recebido' AND revisao=?",
                (*fields.values(), pid, revisao))
            if cur.rowcount:
                auditar(con, pid, user['login'], 'Sugestões de leitura preenchidas')
            else:
                result['avisos'].append('O protocolo foi atualizado durante a leitura. Abra a versão atual.')
        con.commit()
        return result

    def atualizar(self, con, user, row):
        obj = self.objeto()
        if obj.get('revisao') != row['revisao']:
            return self.reply(409, {'erro': 'O registro mudou. Feche e reabra o protocolo.'})
        action = obj.get('acao')
        status = row['status']
        updates = {}
        if action in ('salvar', 'avancar') and status == 'recebido':
            updates = campos_form(obj)
        if action == 'salvar' and status == 'recebido':
            event = 'Dados de conferência atualizados'
        elif action == 'avancar' and status in NEXT:
            if obj.get('confirmado') is not True:
                raise ValueError('Marque a confirmação da etapa.')
            if status == 'recebido':
                if not all(updates[key] for key in ('tipo', 'nome', 'matricula', 'data', 'setor')):
                    raise ValueError('Confira tipo, nome, matrícula, data e setor.')
                if updates['cpf'] and not cpf_valido(updates['cpf']):
                    raise ValueError('Confira o CPF informado.')
            if status in ('conferido', 'encaminhado'):
                key = 'destino' if status == 'conferido' else 'referencia'
                value = obj.get(key, '')
                if not isinstance(value, str) or not 2 <= len(value.strip()) <= 100:
                    raise ValueError('Informe o destino ou a referência do lançamento.')
                updates[key] = value.strip()
            updates['status'] = NEXT[status]
            event = STATUS[NEXT[status]]
        else:
            raise ValueError('Etapa indisponível para este protocolo.')
        assignment = ','.join(f'{key}=?' for key in updates)
        cur = con.execute(f'UPDATE protocolos SET {assignment},revisao=revisao+1 WHERE id=? AND revisao=?',
                          (*updates.values(), row['id'], obj.get('revisao')))
        if not cur.rowcount:
            return self.reply(409, {'erro': 'O registro mudou. Feche e reabra o protocolo.'})
        auditar(con, row['id'], user['login'], event)
        con.commit()
        return self.reply(200, {})

    def exportar(self, con):
        def cell(value):
            value = str(value)
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
        buff = io.StringIO()
        writer = csv.writer(buff, delimiter=';')
        writer.writerow(['Protocolo', 'Tipo', 'Matrícula', 'Setor', 'Data', 'Recebido UTC', 'Status', 'Destino', 'Referência'])
        for row in con.execute(f'SELECT {FIELDS} FROM protocolos ORDER BY id DESC'):
            writer.writerow(map(cell, (codigo(row), TYPES.get(row['tipo'], ''), row['matricula'],
                                       row['setor'], row['data'], row['criado'], STATUS[row['status']],
                                       row['destino'], row['referencia'])))
        return self.reply(200, buff.getvalue().encode('utf-8-sig'), 'text/csv; charset=utf-8',
                          {'Content-Disposition': 'attachment; filename="protocolos-p33.csv"'})

if __name__ == '__main__':
    import getpass
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '8080')))
    parser.add_argument('--criar-usuario')
    parser.add_argument('--perfil', choices=('recepcao','supervisao'), default='supervisao')
    args = parser.parse_args()
    if args.demo:
        DB = DB.with_name('p33-demo.sqlite3')
    os.umask(0o077)
    init(args.demo)
    if args.criar_usuario:
        login = args.criar_usuario.strip().lower()
        password = getpass.getpass('Senha (mínimo 12 caracteres): ')
        if not re.fullmatch(r'[a-z0-9._-]{3,50}', login) or len(password) < 12:
            raise SystemExit('Usuário ou senha inválidos.')
        try:
            with db() as con:
                save_user(con, login, password, args.perfil)
        except sqlite3.IntegrityError:
            raise SystemExit('Usuário já cadastrado.')
    else:
        server = ThreadingHTTPServer((os.getenv('P33_HOST', '127.0.0.1'), args.port), Handler)
        server.demo = args.demo
        print(f'P33 Protocolos em http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()
