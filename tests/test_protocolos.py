import json
from io import BytesIO
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app
from leitura import campos, extrair


class ProtocolosTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.folder.name) / 'test.sqlite3'
        with closing(sqlite3.connect(app.DB)) as db, db:
            db.execute("CREATE TABLE protocolos (id INTEGER PRIMARY KEY AUTOINCREMENT, ano INTEGER NOT NULL, criado TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'recebido', tipo TEXT NOT NULL DEFAULT '', nome TEXT NOT NULL DEFAULT '', cpf TEXT NOT NULL DEFAULT '', matricula TEXT NOT NULL DEFAULT '', setor TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '', dias TEXT NOT NULL DEFAULT '', destino TEXT NOT NULL DEFAULT '', referencia TEXT NOT NULL DEFAULT '', revisao INTEGER NOT NULL DEFAULT 1, hash TEXT UNIQUE, arquivo BLOB, mime TEXT, origem TEXT NOT NULL DEFAULT 'Manual')")
            db.execute("INSERT INTO protocolos (ano,criado,nome) VALUES (2026,'2026-01-01T00:00:00+00:00','Registro antigo')")
        app.init()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.server.demo = False
        self.server.sem_login = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DB = self.original_db
        self.folder.cleanup()

    def request(self, path, data=None, headers=None):
        headers = {'X-P33-Request': '1', **(headers or {})}
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        req = Request(self.base + path, data=data, headers=headers)
        with urlopen(req, timeout=30) as response:
            return json.load(response)

    def test_mobile_manifest_and_icons_are_served(self):
        manifest = self.request('/manifest.webmanifest')
        self.assertEqual(manifest['start_url'], '/')
        self.assertEqual(manifest['display'], 'standalone')
        with urlopen(self.base + '/service-worker.js') as response:
            self.assertEqual(response.headers.get_content_type(), 'text/javascript')
            self.assertIn(b'Network-only', response.read())
        self.assertEqual({icon['sizes'] for icon in manifest['icons']}, {'192x192', '512x512'})
        for icon in manifest['icons']:
            with urlopen(self.base + icon['src']) as response:
                self.assertEqual(response.headers.get_content_type(), 'image/png')
                self.assertEqual(response.read(8), b'\x89PNG\r\n\x1a\n')

    def test_categories_creation_and_review_preserve_old_data(self):
        self.assertEqual(self.request('/api/protocolos/1')['nome'], 'Registro antigo')
        for kind in app.TYPES:
            payload = {'tipo': kind, 'nome': 'Pessoa Teste', 'matricula': 'T-12',
                       'setor': 'Teste', 'data': '2026-01-02',
                       'outro_profissional': 'Colega Teste' if kind == 'troca' else '',
                       'data_troca': '2026-01-05' if kind == 'troca' else '',
                       'hora_inicio': '07:00' if kind == 'troca' else '',
                       'hora_fim': '19:00' if kind == 'troca' else ''}
            record = self.request('/api/protocolos', payload)
            detail = self.request(f"/api/protocolos/{record['id']}")
            self.assertEqual(detail['tipo'], kind)
            self.assertEqual(detail['outro_profissional'], payload['outro_profissional'])
            self.request(f"/api/protocolos/{record['id']}", {**payload, 'revisao':detail['revisao'], 'acao':'avancar', 'confirmado':True})
            self.assertEqual(self.request(f"/api/protocolos/{record['id']}")['status'], 'conferido')

    def test_type_selection_on_upload_and_validation(self):
        content = Path('exemplos/QR_Code.png').read_bytes()
        record = self.request('/api/upload', content, {'X-P33-Tipo':'ferias'})
        self.assertEqual(self.request(f"/api/protocolos/{record['id']}")['tipo'], 'ferias')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/protocolos', {'tipo':'troca','nome':'Teste','hora_inicio':'25:00'})
        self.assertEqual(error.exception.code, 400)

    def test_explicit_document_type_reading(self):
        for text, expected in [('Troca de plantão','troca'),('Férias','ferias'),
                               ('Afastamento','afastamento'),('Ocorrência de ponto','ponto')]:
            self.assertEqual(campos(text).get('tipo'), expected)

    def test_campos_da_troca_em_texto_e_qr_estruturado(self):
        values = {'tipo':'troca', 'nome':'Pessoa Teste', 'matricula':'A-42',
                  'setor':'Clínica', 'outro_profissional':'Pessoa Colega',
                  'matricula_outro':'B-21', 'data_troca':'2026-11-25',
                  'hora_inicio':'07:00', 'hora_fim':'19:00'}
        decoded = campos(json.dumps(values, ensure_ascii=False))
        for key, value in values.items():
            self.assertEqual(decoded.get(key), value, key)
        only_other = campos('Matrícula do outro profissional: B-21')
        self.assertNotIn('matricula', only_other)
        self.assertEqual(only_other['matricula_outro'], 'B-21')

    def test_leitura_de_pdf_foto_e_qr_ficticios(self):
        fixtures = [('Ocorrencia_de_Ponto.pdf', '.pdf', 'Texto do PDF'),
                    ('Atestado_Foto.png', '.png', 'OCR'),
                    ('QR_Code.png', '.png', 'QR Code')]
        for filename, extension, method in fixtures:
            with self.subTest(arquivo=filename):
                result = extrair((Path('exemplos') / filename).read_bytes(), extension)
                self.assertIn(method, result['metodos'], result['avisos'])
                self.assertTrue(any(result['campos'].get(key) for key in ('nome','matricula','cpf')))

    def test_sugestoes_da_leitura_sao_gravadas_no_protocolo(self):
        fields = {'nome':'Pessoa Teste', 'tipo':'troca', 'setor':'Clínica',
                  'outro_profissional':'Colega Teste', 'matricula_outro':'B-21',
                  'data_troca':'2026-11-25', 'hora_inicio':'07:00', 'hora_fim':'19:00'}
        result = {'campos':fields, 'metodos':['QR Code'], 'avisos':[]}
        with patch('app.extrair', return_value=result):
            record = self.request('/api/upload', Path('exemplos/QR_Code.png').read_bytes(), {'X-P33-Tipo':'troca'})
        detail = self.request(f"/api/protocolos/{record['id']}")
        for key, value in fields.items():
            self.assertEqual(detail[key], value, key)
            self.assertIn(key, record['campos_sugeridos'])

    def test_ocorrencia_de_ponto_com_varias_linhas_e_tratativas(self):
        rows = [{'data':'2026-11-20','ocorrencia':'Entrada sem registro','justificativa':'Relato de teste'},
                {'data':'2026-11-21','ocorrencia':'Saída sem registro','justificativa':'Outro relato'}]
        choices = ['compensacao', 'troca', 'horas_extras']
        payload = {'tipo':'ponto','nome':'Pessoa Teste','matricula':'T-12','setor':'Teste',
                   'data':'2026-11-20','ocorrencias_ponto':json.dumps(rows),
                   'tratativas_ponto':json.dumps(choices), 'troca_ponto_de':'2026-11-22',
                   'troca_ponto_para':'2026-11-23'}
        created = self.request('/api/protocolos', payload)
        path = f"/api/protocolos/{created['id']}"
        detail = self.request(path)
        self.assertEqual(json.loads(detail['ocorrencias_ponto']), rows)
        self.assertEqual(json.loads(detail['tratativas_ponto']), choices)
        self.request(path, {**payload, 'acao':'avancar', 'confirmado':True, 'revisao':detail['revisao']})
        detail = self.request(path)
        self.assertEqual(detail['status'], 'conferido')
        self.assertEqual(detail['troca_ponto_para'], '2026-11-23')
        self.assertEqual(json.loads(detail['ocorrencias_ponto']), rows)

    def test_chapa_e_validacao_das_tratativas(self):
        self.assertEqual(campos('Chapa: 12345\nNome: Pessoa Teste')['matricula'], '12345')
        self.assertNotIn('matricula', campos('CHAPA: NOME:'))
        with self.assertRaises(HTTPError) as error:
            self.request('/api/protocolos', {'tipo':'ponto','nome':'Teste', 'tratativas_ponto':'["invalida"]'})
        self.assertEqual(error.exception.code, 400)

    def test_leitura_de_ocorrencia_rotulada_sem_inventar_tratativa(self):
        text = 'OCORRÊNCIA DE PONTO\nChapa: 12345\nData da ocorrência: 20/11/2026\nOcorrência: Registro ausente\nJustificativa: Falha no relógio\n'
        result = campos(text)
        self.assertEqual(result['tipo'], 'ponto')
        self.assertEqual(result['matricula'], '12345')
        self.assertEqual(json.loads(result['ocorrencias_ponto']), [{'data':'2026-11-20','ocorrencia':'Registro ausente','justificativa':'Falha no relógio'}])
        self.assertNotIn('tratativas_ponto', result)
        self.assertNotIn('ocorrencias_ponto', campos('OCORRÊNCIA DE PONTO\nData da ocorrência:\nOcorrência:\nJustificativa:\n'))

    def test_exportacao_excel_pdf_respeita_selecao_e_trata_formulas(self):
        from openpyxl import load_workbook
        import pypdfium2 as pdfium
        first = self.request('/api/protocolos', {'tipo':'ponto','nome':'Pessoa Teste','matricula':'A-12','setor':'=Teste','data':'2026-11-20'})
        second = self.request('/api/protocolos', {'tipo':'ferias','nome':'Outra Pessoa','matricula':'B-34','setor':'UTI','data':'2026-11-21'})
        def download(fmt, ids):
            request = Request(self.base + '/api/exportar', data=json.dumps({'formato':fmt,'ids':ids}).encode(),
                              headers={'X-P33-Request':'1','Content-Type':'application/json'})
            with urlopen(request, timeout=30) as response:
                return response.headers.get_content_type(), response.read()
        mime, content = download('xlsx', [first['id']])
        self.assertEqual(mime, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        sheet = load_workbook(BytesIO(content)).active
        self.assertEqual(sheet.max_row, 2)
        self.assertEqual([cell.value for cell in sheet[1]], ['Protocolo','Documento','Colaborador','Matrícula','Setor','Recebimento','Status'])
        self.assertEqual(sheet.cell(2, 3).value, 'Pessoa Teste')
        self.assertEqual(sheet.cell(2, 4).value, 'A-12')
        self.assertEqual(sheet.cell(2, 5).value, '=Teste')
        self.assertEqual(sheet.cell(2, 5).data_type, 's')
        self.assertNotIn('B-34', str(list(sheet.values)))
        mime, content = download('pdf', [second['id']])
        self.assertEqual(mime, 'application/pdf')
        document = pdfium.PdfDocument(content)
        page = document[0]
        textpage = page.get_textpage()
        content_text = textpage.get_text_range()
        self.assertIn('B-34', content_text)
        self.assertIn('Outra Pessoa', content_text)
        self.assertNotIn('A-12', content_text)
        textpage.close(); page.close(); document.close()
        self.server.sem_login = False
        with self.assertRaises(HTTPError) as error:
            download('xlsx', [first['id']])
        self.assertEqual(error.exception.code, 401)

    def test_prazo_de_90_dias_e_exclusao_com_registro_minimo(self):
        self.assertEqual(app.prazo_retencao('2024-01-01T00:00:00+00:00'), '2024-03-31T00:00:00+00:00')
        self.assertIsNone(app.prazo_retencao('inválido'))
        created = self.request('/api/protocolos', {'tipo':'atestado','nome':'Pessoa Fictícia'})
        path = f"/api/protocolos/{created['id']}"
        item = self.request(path)
        self.assertEqual(item['retencao_ate'], app.prazo_retencao(item['criado']))
        with self.assertRaises(HTTPError) as error:
            self.request(path + '/excluir', {'revisao':item['revisao'] + 1})
        self.assertEqual(error.exception.code, 409)
        with app.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM exclusoes').fetchone()[0], 0)
        self.request(path + '/excluir', {'revisao':item['revisao']})
        with self.assertRaises(HTTPError) as error:
            self.request(path)
        self.assertEqual(error.exception.code, 404)
        with app.db() as db:
            deletion = dict(db.execute('SELECT protocolo,operador,instante,motivo FROM exclusoes').fetchone())
            self.assertEqual(deletion['protocolo'], created['codigo'])
            self.assertEqual(deletion['operador'], 'local')
            self.assertNotIn('Pessoa Fictícia', str(deletion))

    def test_retencao_escopo_e_limite_de_90_dias(self):
        with app.db() as db:
            concluido = db.execute("INSERT INTO protocolos (ano,criado,status,nome,arquivo) VALUES (2026,?,?,?,?)", ('2026-01-01T00:00:00+00:00','baixado','Fictício',b'arquivo')).lastrowid
            pendente = db.execute("INSERT INTO protocolos (ano,criado,status,nome) VALUES (2026,?,?,?)", ('2026-01-01T00:00:00+00:00','recebido','Fictício')).lastrowid
            recente = db.execute("INSERT INTO protocolos (ano,criado,status,nome) VALUES (2026,?,?,?)", ('2026-01-02T00:00:00+00:00','baixado','Fictício')).lastrowid
            app.auditar(db, concluido, 'teste', 'Evento fictício')
        when = datetime(2026, 4, 1, tzinfo=timezone.utc)
        self.assertEqual(app.excluir_vencidos('concluidos', when), 1)
        with app.db() as db:
            self.assertIsNone(db.execute('SELECT id FROM protocolos WHERE id=?', (concluido,)).fetchone())
            self.assertIsNotNone(db.execute('SELECT id FROM protocolos WHERE id=?', (pendente,)).fetchone())
            self.assertIsNotNone(db.execute('SELECT id FROM protocolos WHERE id=?', (recente,)).fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auditoria WHERE protocolo=?', (concluido,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT motivo FROM exclusoes').fetchone()[0], 'retenção de 90 dias')
        self.assertEqual(app.excluir_vencidos('todos', when), 2)  # pendente e registro antigo
        with self.assertRaises(ValueError):
            app.excluir_vencidos('indefinido', when)


if __name__ == '__main__':
    unittest.main()
