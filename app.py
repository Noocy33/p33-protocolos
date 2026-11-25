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
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
import sqlite3
import threading
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from leitura import extrair

ROOT = Path(__file__).resolve().parent
DB = Path(os.getenv('P33_DB', str(ROOT / 'dados' / 'p33.sqlite3')))
TYPES = {'atestado': 'Atestado', 'ponto': 'Ocorrência de ponto',
         'troca': 'Troca de plantão', 'ferias': 'Férias', 'afastamento': 'Afastamento'}
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
        CREATE TABLE IF NOT EXISTS exclusoes(
          id INTEGER PRIMARY KEY, protocolo TEXT NOT NULL, operador TEXT NOT NULL,
          instante TEXT NOT NULL, motivo TEXT NOT NULL);
        """)
        existing = {row['name'] for row in con.execute('PRAGMA table_info(protocolos)')}
        for name in ('data_inicio', 'data_fim', 'cid', 'medico', 'local_atestado', 'token_atestado',
                     'outro_profissional', 'matricula_outro', 'data_troca', 'hora_inicio', 'hora_fim', 'detalhes',
                     'ocorrencias_ponto', 'tratativas_ponto', 'troca_ponto_de', 'troca_ponto_para'):
            if name not in existing:
                con.execute(f"ALTER TABLE protocolos ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
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

def prazo_retencao(criado):
    try:
        instante = datetime.fromisoformat(criado)
        if instante.tzinfo is None:
            return None
        return (instante.astimezone(timezone.utc) + timedelta(days=90)).isoformat(timespec='seconds')
    except (ValueError, TypeError, OverflowError):
        return None

def excluir_vencidos(escopo, instante=None):
    """Executa retenção somente quando chamada explicitamente para um escopo definido."""
    if escopo not in ('todos', 'concluidos'):
        raise ValueError('Escopo de retenção inválido.')
    current = instante or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError('O horário de referência precisa incluir fuso.')
    current = current.astimezone(timezone.utc)
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        rows = con.execute('SELECT id,ano,criado,status FROM protocolos').fetchall()
        selected = []
        for row in rows:
            deadline = prazo_retencao(row['criado'])
            if deadline and datetime.fromisoformat(deadline) <= current and (escopo == 'todos' or row['status'] == 'baixado'):
                selected.append(row)
        for row in selected:
            con.execute('INSERT INTO exclusoes (protocolo,operador,instante,motivo) VALUES (?,?,?,?)',
                        (codigo(row), 'sistema', current.isoformat(timespec='seconds'), 'retenção de 90 dias'))
            con.execute('DELETE FROM auditoria WHERE protocolo=?', (row['id'],))
            con.execute('DELETE FROM protocolos WHERE id=?', (row['id'],))
        return len(selected)

def iniciar_retencao(escopo, parar):
    def executar():
        while not parar.is_set():
            try:
                excluir_vencidos(escopo)
            except (sqlite3.Error, ValueError):
                print('Falha na rotina de retenção. Verifique o banco e a configuração.', flush=True)
            if parar.wait(3600):
                break
    thread = threading.Thread(target=executar, name='retencao', daemon=True)
    thread.start()
    return thread

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
              'data': 10, 'dias': 3, 'data_inicio': 10, 'data_fim': 10,
              'cid': 30, 'medico': 120, 'local_atestado': 160, 'token_atestado': 200,
              'outro_profissional': 120, 'matricula_outro': 30, 'data_troca': 10,
              'hora_inicio': 5, 'hora_fim': 5, 'detalhes': 1000,
              'ocorrencias_ponto': 8000, 'tratativas_ponto': 500,
              'troca_ponto_de': 10, 'troca_ponto_para': 10}
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
    if result['matricula_outro'] and not re.fullmatch(r'[A-Za-z0-9-]{2,30}', result['matricula_outro']):
        raise ValueError('Matrícula do outro profissional inválida.')
    for key in ('data', 'data_inicio', 'data_fim', 'data_troca', 'troca_ponto_de', 'troca_ponto_para'):
        if not result[key]:
            continue
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', result[key]):
            raise ValueError('Use uma data válida.')
        date.fromisoformat(result[key])
    if result['data_inicio'] and result['data_fim'] and result['data_fim'] < result['data_inicio']:
        raise ValueError('A data final não pode ser anterior à data inicial.')
    if result['dias'] and not re.fullmatch(r'[0-9]{1,3}', result['dias']):
        raise ValueError('Quantidade de dias inválida.')
    for key in ('hora_inicio', 'hora_fim'):
        if result[key] and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', result[key]):
            raise ValueError('Informe um horário válido.')
    try:
        ocorrencias = json.loads(result['ocorrencias_ponto'] or '[]')
        tratativas = json.loads(result['tratativas_ponto'] or '[]')
    except (ValueError, TypeError):
        raise ValueError('Confira os dados da ocorrência de ponto.')
    if not isinstance(ocorrencias, list) or len(ocorrencias) > 10:
        raise ValueError('Informe até 10 ocorrências por protocolo.')
    for item in ocorrencias:
        if not isinstance(item, dict) or set(item) != {'data', 'ocorrencia', 'justificativa'}:
            raise ValueError('Confira os dados da ocorrência de ponto.')
        if any(not isinstance(item[key], str) or len(item[key]) > limit for key, limit in (('data', 10), ('ocorrencia', 200), ('justificativa', 500))):
            raise ValueError('Confira os dados da ocorrência de ponto.')
        if item['data']:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['data']):
                raise ValueError('Confira a data da ocorrência de ponto.')
            date.fromisoformat(item['data'])
    validas = {'compensacao', 'abonar', 'descontar_dia', 'descontar_horas', 'troca', 'horas_extras'}
    if not isinstance(tratativas, list) or any(not isinstance(x, str) or x not in validas for x in tratativas) or len(set(tratativas)) != len(tratativas):
        raise ValueError('Confira as tratativas de ponto selecionadas.')
    result['ocorrencias_ponto'] = json.dumps(ocorrencias, ensure_ascii=False, separators=(',', ':')) if ocorrencias else ''
    result['tratativas_ponto'] = json.dumps(tratativas, ensure_ascii=False, separators=(',', ':')) if tratativas else ''
    return result

def detectar(data):
    if data.startswith(b'%PDF-'):
        return 'application/pdf', '.pdf'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg'
    raise ValueError('Envie um PDF, PNG ou JPEG válido.')

def enviar_email(destinatario, codigo_protocolo, arquivo, mime):
    host = os.getenv('P33_SMTP_HOST', '').strip()
    usuario = os.getenv('P33_SMTP_USUARIO', '').strip()
    senha = os.getenv('P33_SMTP_SENHA', '')
    remetente = os.getenv('P33_SMTP_REMETENTE', usuario).strip()
    if not all((host, usuario, senha, remetente)):
        raise ValueError('Configure a conta remetente de e-mail antes de enviar.')
    if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', destinatario) or parseaddr(destinatario)[1] != destinatario:
        raise ValueError('Informe um endereço de e-mail válido.')
    if not arquivo or not mime:
        raise ValueError('Este protocolo não possui documento para anexar.')
    _, ext = detectar(arquivo)
    msg = EmailMessage()
    msg['From'], msg['To'] = remetente, destinatario
    msg['Subject'] = f'Encaminhamento de protocolo {codigo_protocolo}'
    msg.set_content(f'Olá,\n\nSegue em anexo o documento do protocolo {codigo_protocolo}.\n')
    maintype, subtype = mime.split('/', 1)
    msg.add_attachment(arquivo, maintype=maintype, subtype=subtype, filename=f'{codigo_protocolo}{ext}')
    mode = os.getenv('P33_SMTP_SEGURANCA', 'starttls').lower()
    port = int(os.getenv('P33_SMTP_PORTA', '465' if mode == 'ssl' else '587'))
    if mode not in ('ssl', 'starttls'):
        raise ValueError('Configure a segurança SMTP como ssl ou starttls.')
    context = ssl.create_default_context()
    client = (smtplib.SMTP_SSL(host, port, timeout=20, context=context) if mode == 'ssl'
              else smtplib.SMTP(host, port, timeout=20))
    with client:
        if mode == 'starttls':
            client.starttls(context=context)
        client.login(usuario, senha)
        refused = client.send_message(msg)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)

FIELDS = 'id,ano,criado,status,tipo,nome,matricula,setor,data,dias,data_inicio,data_fim,cid,medico,local_atestado,token_atestado,outro_profissional,matricula_outro,data_troca,hora_inicio,hora_fim,detalhes,ocorrencias_ponto,tratativas_ponto,troca_ponto_de,troca_ponto_para,destino,referencia,revisao,origem,mime'

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
        if self.server.sem_login and self.headers.get('Host', '').lower() not in (
                f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
            return self.reply(403, {'erro': 'Acesso permitido apenas pelo endereço local.'})
        assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                  '/style.css': ('style.css', 'text/css'),
                  '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
                  '/service-worker.js': ('service-worker.js', 'text/javascript'),
                  '/icon-192.png': ('icon-192.png', 'image/png'),
                  '/icon-512.png': ('icon-512.png', 'image/png')}
        if method == 'GET' and path in assets:
            name, mime = assets[path]
            content_type = mime if mime == 'image/png' else mime + '; charset=utf-8'
            return self.reply(200, (ROOT / 'web' / name).read_bytes(), content_type)
        if method == 'POST' and self.headers.get('X-P33-Request') != '1':
            return self.reply(403, {'erro': 'Solicitação inválida.'})
        if method == 'GET' and path == '/api/config':
            return self.reply(200, {'demo': self.server.demo, 'sem_login': self.server.sem_login,
                                    'retencao_automatica': getattr(self.server, 'retencao_automatica', False),
                                    'retencao_escopo': getattr(self.server, 'retencao_escopo', '')})
        with db() as con:
            if method == 'POST' and path == '/api/demo-login' and self.server.demo:
                profile = self.objeto().get('perfil', 'supervisao')
                if profile not in ('supervisao', 'recepcao'):
                    raise ValueError('Perfil inválido.')
                return self.start_session(con, {'login': profile, 'perfil': profile})
            if method == 'POST' and path == '/api/login':
                return self.login(con)
            user = ({'login': 'local', 'perfil': 'supervisao'} if self.server.sem_login
                    else self.session(con))
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
                return self.reply(200, [dict(row, codigo=codigo(row), retencao_ate=prazo_retencao(row['criado'])) for row in rows])
            if method == 'GET' and path == '/api/exportar' and supervisor:
                return self.exportar(con)
            if method == 'POST' and path == '/api/exportar':
                if not supervisor:
                    return self.reply(403, {'erro': 'Exportação exclusiva da supervisão.'})
                return self.exportar_arquivo(con)
            if method == 'POST' and path == '/api/upload':
                return self.upload(con, user)
            if method == 'POST' and path == '/api/protocolos':
                fields = campos_form(self.objeto())
                if not fields['tipo'] or not (fields['nome'] or fields['matricula'] or fields['cpf']):
                    raise ValueError('Informe o tipo e ao menos um identificador.')
                columns = ','.join(fields)
                placeholders = ','.join('?' for _ in fields)
                cur = con.execute(
                    f'INSERT INTO protocolos (ano,criado,{columns}) VALUES (?,?,{placeholders})',
                    (datetime.now(TZ).year, agora(), *fields.values()))
                auditar(con, cur.lastrowid, user['login'], 'Recebimento manual registrado')
                con.commit()
                created = con.execute('SELECT id,ano FROM protocolos WHERE id=?', (cur.lastrowid,)).fetchone()
                return self.reply(201, {'id': created['id'], 'codigo': codigo(created)})
            match = re.fullmatch(r'/api/protocolos/(\d+)(/arquivo|/reler|/excluir|/enviar-email)?', path)
            if not match:
                return self.reply(404, {'erro': 'Página indisponível.'})
            if not supervisor:
                return self.reply(403, {'erro': 'Acesso exclusivo da supervisão.'})
            pid, suffix = int(match[1]), match[2]
            if method == 'POST' and suffix == '/excluir':
                con.execute('BEGIN IMMEDIATE')
            row = con.execute(f'SELECT {FIELDS},cpf FROM protocolos WHERE id=?', (pid,)).fetchone()
            if row is None:
                return self.reply(404, {'erro': 'Protocolo não encontrado.'})
            if method == 'POST' and suffix == '/enviar-email':
                if row['status'] != 'conferido':
                    return self.reply(409, {'erro': 'Confira o protocolo antes de enviar.'})
                obj = self.objeto()
                if obj.get('revisao') != row['revisao']:
                    return self.reply(409, {'erro': 'O registro mudou. Abra novamente antes de enviar.'})
                address = obj.get('destinatario', '')
                if not isinstance(address, str) or len(address) > 100:
                    raise ValueError('Informe um endereço de e-mail válido.')
                arquivo = con.execute('SELECT arquivo FROM protocolos WHERE id=?', (pid,)).fetchone()[0]
                try:
                    enviar_email(address.strip(), codigo(row), arquivo, row['mime'])
                except (smtplib.SMTPException, OSError, TimeoutError):
                    return self.reply(502, {'erro': 'Não foi possível confirmar o envio pelo servidor de e-mail. Verifique a configuração e a caixa de enviados antes de tentar novamente.'})
                auditar(con, pid, user['login'], 'E-mail aceito pelo servidor para ' + address.strip())
                con.commit()
                return self.reply(200, {'mensagem': 'E-mail aceito pelo servidor. Confira a entrega com o destinatário.'})
            if method == 'POST' and suffix == '/excluir':
                if self.objeto().get('revisao') != row['revisao']:
                    return self.reply(409, {'erro': 'O registro mudou. Abra novamente antes de excluir.'})
                con.execute('INSERT INTO exclusoes (protocolo,operador,instante,motivo) VALUES (?,?,?,?)',
                            (codigo(row), user['login'], agora(), 'exclusão manual'))
                con.execute('DELETE FROM auditoria WHERE protocolo=?', (pid,))
                con.execute('DELETE FROM protocolos WHERE id=?', (pid,))
                con.commit()
                return self.reply(200, {})
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
                return self.reply(200, dict(row, codigo=codigo(row), retencao_ate=prazo_retencao(row['criado']), eventos=[dict(event) for event in events]))
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
        tipo = self.headers.get('X-P33-Tipo', '').strip()
        if tipo and tipo not in TYPES:
            raise ValueError('Tipo de documento inválido.')
        digest = hashlib.sha256(data).hexdigest()
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT id,ano FROM protocolos WHERE hash=?', (digest,)).fetchone()
        if existing:
            return self.reply(409, {'erro': f'Este arquivo já foi recebido. Protocolo {codigo(existing)}.',
                                    'id': existing['id']})
        year = datetime.now(TZ).year
        cur = con.execute('INSERT INTO protocolos (ano,criado,hash,arquivo,mime,origem,tipo) VALUES (?,?,?,?,?,?,?)',
                          (year, agora(), digest, data, mime, 'PDF' if ext == '.pdf' else 'Foto', tipo))
        pid = cur.lastrowid
        auditar(con, pid, user['login'], 'Documento recebido e protocolado')
        con.commit()
        result = self.ler(con, user, pid, data, ext, 1)
        self.reply(201, {'id': pid, 'codigo': codigo({'ano': year, 'id': pid}),
                         'metodos': result['metodos'], 'avisos': result['avisos'],
                         'campos_sugeridos': list(result.get('campos', {}))})

    def ler(self, con, user, pid, data, ext, revisao):
        if not READERS.acquire(blocking=False):
            return {'metodos': [], 'avisos': ['Protocolo salvo. A supervisão pode tentar a leitura novamente.']}
        try:
            result = extrair(data, ext)
        finally:
            READERS.release()
        fields = result['campos']
        current = con.execute('SELECT tipo FROM protocolos WHERE id=?', (pid,)).fetchone()
        if current and current['tipo'] and fields.get('tipo') and current['tipo'] != fields['tipo']:
            result['avisos'].append('O tipo escolhido difere do identificado na leitura. Confira o documento.')
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

    def exportar_arquivo(self, con):
        obj = self.objeto()
        formato = obj.get('formato')
        ids = obj.get('ids')
        if formato not in ('xlsx', 'pdf') or not isinstance(ids, list) or len(ids) > 900:
            raise ValueError('Formato ou seleção inválida. Exporte até 900 protocolos por vez.')
        if any(type(value) is not int or value < 1 for value in ids) or len(ids) != len(set(ids)):
            raise ValueError('Seleção de protocolos inválida.')
        rows = []
        if ids:
            marks = ','.join('?' for _ in ids)
            rows = con.execute(f'SELECT {FIELDS} FROM protocolos WHERE id IN ({marks}) ORDER BY id DESC', ids).fetchall()
        try:
            from exportacao import linhas_relatorio, excel, pdf
            records = linhas_relatorio(rows, TYPES, STATUS, codigo)
            output = excel(records) if formato == 'xlsx' else pdf(records)
        except ImportError:
            return self.reply(503, {'erro': 'A exportação não está instalada. Instale as dependências do projeto.'})
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if formato == 'xlsx' else 'application/pdf'
        return self.reply(200, output, mime, {'Content-Disposition': f'attachment; filename="protocolos-p33.{formato}"'})

if __name__ == '__main__':
    import getpass
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--sem-login', action='store_true', help='Acesso local sem autenticação')
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
        host = '127.0.0.1' if args.sem_login else os.getenv('P33_HOST', '127.0.0.1')
        auto_retencao = os.getenv('P33_RETENCAO_AUTOMATICA') == '1'
        escopo_retencao = os.getenv('P33_RETENCAO_ESCOPO', '')
        if auto_retencao and escopo_retencao not in ('todos', 'concluidos'):
            raise SystemExit('Defina P33_RETENCAO_ESCOPO como todos ou concluidos antes de ativar a retenção.')
        server = ThreadingHTTPServer((host, args.port), Handler)
        server.demo = args.demo
        server.sem_login = args.sem_login
        server.retencao_automatica = auto_retencao
        server.retencao_escopo = escopo_retencao if auto_retencao else ''
        parar_retencao = threading.Event()
        if auto_retencao:
            iniciar_retencao(escopo_retencao, parar_retencao)
        print(f'P33 Protocolos em http://127.0.0.1:{args.port}', flush=True)
        try:
            server.serve_forever()
        finally:
            parar_retencao.set()
            server.server_close()
