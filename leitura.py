"""Extração local. Links presentes em QR são tratados como texto."""
import ctypes as c
import ctypes.util
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

def executar(*args):
    result = subprocess.run(args, capture_output=True, timeout=12, check=True)
    return result.stdout.decode('utf-8', errors='replace')

def qr_imagem(path):
    # ImageMagick gera um PGM com dimensões limitadas para a biblioteca ZBar.
    magick = shutil.which('magick') or shutil.which('convert')
    pgm = subprocess.run(
        [magick, '-limit', 'memory', '128MiB', '-limit', 'map', '256MiB',
         str(path), '-auto-orient', '-resize', '1800x1800>', '-colorspace',
         'gray', '-depth', '8', 'pgm:-'],
        capture_output=True, timeout=12, check=True
    ).stdout
    header = re.match(rb'P5\s+(\d+)\s+(\d+)\s+255\s', pgm)
    if not header:
        return ''
    width, height = map(int, header.groups())
    pixels = c.create_string_buffer(pgm[header.end():])
    if len(pixels) != width * height + 1:
        return ''
    lib = c.CDLL(ctypes.util.find_library('zbar') or 'libzbar.so.0')
    signatures = {
        'image_scanner_create': (c.c_void_p, []),
        'image_scanner_destroy': (None, [c.c_void_p]),
        'image_create': (c.c_void_p, []),
        'image_destroy': (None, [c.c_void_p]),
        'image_set_format': (None, [c.c_void_p, c.c_uint]),
        'image_set_size': (None, [c.c_void_p, c.c_uint, c.c_uint]),
        'image_set_data': (None, [c.c_void_p, c.c_void_p, c.c_ulong, c.c_void_p]),
        'scan_image': (c.c_int, [c.c_void_p, c.c_void_p]),
        'image_first_symbol': (c.c_void_p, [c.c_void_p]),
        'symbol_get_data': (c.c_char_p, [c.c_void_p]),
        'symbol_get_type': (c.c_int, [c.c_void_p]),
        'symbol_next': (c.c_void_p, [c.c_void_p]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(lib, 'zbar_' + name)
        fn.restype, fn.argtypes = result, args
    scanner, img = lib.zbar_image_scanner_create(), lib.zbar_image_create()
    try:
        lib.zbar_image_set_format(img, int.from_bytes(b'Y800', 'little'))
        lib.zbar_image_set_size(img, width, height)
        lib.zbar_image_set_data(img, pixels, width * height, None)
        lib.zbar_scan_image(scanner, img)
        symbol = lib.zbar_image_first_symbol(img)
        while symbol:
            if lib.zbar_symbol_get_type(symbol) == 64:
                return lib.zbar_symbol_get_data(symbol).decode('utf-8', 'replace')[:4000]
            symbol = lib.zbar_symbol_next(symbol)
        return ''
    finally:
        lib.zbar_image_destroy(img)
        lib.zbar_image_scanner_destroy(scanner)

def qr_imagem_opencv(path):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return ''
    value, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    return value[:4000]

def campos(texto):
    try:
        obj = json.loads(texto)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        texto = '\n'.join(f"{key.replace('_', ' ')}: {value}" for key, value in obj.items()
                          if isinstance(value, (str, int)))
    patterns = {
        'nome': r'^\s*(?:nome(?:\s+(?:do colaborador|do paciente|completo))?|colaborador|paciente)\s*:\s*([^\r\n]{3,100})',
        'cpf': r'\bCPF(?:\s+d[oa]\s+paciente)?\s*[:=]?\s*(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b',
        'matricula': r'^\s*(?:matr[ií]cula(?:\s+do\s+colaborador)?|chapa)[ \t]*[:=][ \t]*(?!(?:nome|setor|data)\b)([A-Z0-9-]{2,30})\b',
        'setor': r'^\s*(?:setor|unidade\s+de\s+trabalho)[ \t]*[:=][ \t]*([^\r\n]{2,80})',
        'data': r'^\s*(?:data(?:\s+(?:do documento|de emissão|da ocorrência))?|emissão)\s*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b',
        'dias': r'(?:\b(\d{1,3})[^\S\r\n]+dias?\b|\bdias\s*:\s*(\d{1,3})\b)',
        'data_inicio': r'^\s*(?:data\s+(?:de\s+)?in[ií]cio(?:\s+do\s+afastamento)?|in[ií]cio(?:\s+do\s+afastamento)?)\s*[:=]\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b',
        'data_fim': r'^\s*(?:data\s+(?:de\s+)?(?:fim|t[eé]rmino)(?:\s+do\s+afastamento)?|(?:fim|t[eé]rmino)(?:\s+do\s+afastamento)?)\s*[:=]\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b',
        'cid': r'^\s*CID(?:-?10|-?11)?\s*[:=]\s*([A-Z][0-9]{2}(?:\.[A-Z0-9]{1,4})?)\b',
        'medico': r'^\s*(?:m[eé]dic[oa](?:\s+(?:respons[aá]vel|assistente))?|profissional(?:\s+de\s+sa[uú]de)?)\s*[:=]\s*([^\r\n]{3,120})',
        'local_atestado': r'^\s*(?:local(?:\s+(?:do\s+atendimento|do\s+atestado))?|unidade(?:\s+de\s+sa[uú]de)?|estabelecimento)\s*[:=]\s*([^\r\n]{2,160})',
        'token_atestado': r'^\s*(?:token(?:\s+do\s+atestado)?|c[oó]digo\s+(?:de\s+)?(?:valida[cç][aã]o|verifica[cç][aã]o))\s*[:=]\s*([^\s\r\n]{2,200})',
        'outro_profissional': r'^\s*(?:outro\s+profissional|profissional\s+(?:da\s+troca|substituto)|colega\s+da\s+troca)[ \t]*[:=][ \t]*([^\r\n]{3,120})',
        'matricula_outro': r'^\s*matr[ií]cula\s+(?:do\s+outro\s+profissional|do\s+substituto|outro)[ \t]*[:=][ \t]*([A-Z0-9-]{2,30})\b',
        'data_troca': r'^\s*(?:data\s+(?:do\s+plant[aã]o|da\s+troca)|data\s+troca)[ \t]*[:=][ \t]*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b',
        'hora_inicio': r'^\s*(?:hor[aá]rio\s+(?:de\s+)?in[ií]cio|hora\s+in[ií]cio|entrada)[ \t]*[:=][ \t]*((?:[01]\d|2[0-3]):[0-5]\d)\b',
        'hora_fim': r'^\s*(?:hor[aá]rio\s+(?:de\s+)?(?:fim|t[eé]rmino)|hora\s+fim|sa[ií]da)[ \t]*[:=][ \t]*((?:[01]\d|2[0-3]):[0-5]\d)\b',
    }
    result = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, texto, re.I | re.M)
        if match:
            result[key] = next(value for value in match.groups() if value is not None).strip()
    if 'data_inicio' not in result:
        match = re.search(r'a\s+partir\s+de\s*(?:[^\r\n()]{0,40}\s*)?\(?(\d{2}/\d{2}/\d{4})\)?\s+por\s+\d{1,3}\s+dias?', texto, re.I)
        if match:
            result['data_inicio'] = match.group(1)
    if 'medico' not in result:
        match = re.search(r'^\s*Dr\(a\)\.\s*([^\r\n|]{3,120}?)\s*\|\s*CRM\b', texto, re.I | re.M)
        if match:
            result['medico'] = match.group(1).strip()
    if 'token_atestado' not in result:
        match = re.search(r'^\s*Token[^\r\n]{0,40}\r?\n\s*([A-Z0-9-]{4,200})\s*$', texto, re.I | re.M)
        if match:
            result['token_atestado'] = match.group(1)
    if 'token_atestado' not in result:
        match = re.search(r'\bc[oó]digo\s+de\s+acesso\s+[eé]\s*:\s*([A-Z0-9-]{4,200})\b', texto, re.I)
        if match:
            result['token_atestado'] = match.group(1)
    if 'cpf' in result:
        result['cpf'] = re.sub(r'\D', '', result['cpf'])
    dates = re.findall(r'^[ \t]*data\s+da\s+ocorr[eê]ncia[ \t]*:[ \t]*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})[ \t]*$', texto, re.I | re.M)
    ocorrencias = re.findall(r'^[ \t]*ocorr[eê]ncia[ \t]*:[ \t]*(\S[^\r\n]{0,199})$', texto, re.I | re.M)
    justificativas = re.findall(r'^[ \t]*justificativa[ \t]*:[ \t]*(\S[^\r\n]{0,499})$', texto, re.I | re.M)
    if len(dates) == 1 and len(ocorrencias) == 1 and len(justificativas) <= 1:
        try:
            occurrence_date = (dt.datetime.strptime(dates[0], '%d/%m/%Y').date() if '/' in dates[0] else dt.date.fromisoformat(dates[0])).isoformat()
            result['ocorrencias_ponto'] = json.dumps([{'data':occurrence_date, 'ocorrencia':ocorrencias[0].strip(), 'justificativa':justificativas[0].strip() if justificativas else ''}], ensure_ascii=False, separators=(',', ':'))
        except ValueError:
            pass
    for key in ('data', 'data_inicio', 'data_fim', 'data_troca'):
        if key not in result:
            continue
        try:
            value = result[key]
            result[key] = (dt.datetime.strptime(value, '%d/%m/%Y').date()
                              if '/' in value else dt.date.fromisoformat(value)).isoformat()
        except ValueError:
            del result[key]
    tipo_explicito = re.search(r'^\s*(?:tipo(?:\s+de\s+documento)?\s*:\s*)?(troca\s+de\s+plant[aã]o|f[eé]rias|afastamento|ocorr[eê]ncia\s+de\s+ponto|atestado|troca|ferias|ponto)\s*$', texto, re.I | re.M)
    if tipo_explicito:
        value = tipo_explicito.group(1).lower()
        result['tipo'] = ('troca' if value.startswith('troca') else 'ferias' if value in ('ferias', 'férias')
                          else 'afastamento' if value == 'afastamento' else 'ponto' if value.startswith(('ponto', 'ocorr'))
                          else 'atestado')
    elif re.search(r'ocorr[eê]ncia\s+de\s+ponto', texto, re.I):
        result['tipo'] = 'ponto'
    elif re.search(r'\batestado\b', texto, re.I):
        result['tipo'] = 'atestado'
    return result

def extrair(data, ext):
    methods, warnings, texts, qr_codes = [], [], [], []
    with tempfile.TemporaryDirectory(prefix='p33-') as folder:
        path = Path(folder) / ('documento' + ext)
        path.write_bytes(data)
        images = [path]
        if ext == '.pdf':
            images = []
            try:
                text = executar('pdftotext', '-f', '1', '-l', '5', '-layout', str(path), '-')
                if text.strip():
                    texts.append(text)
                    methods.append('Texto do PDF')
            except (OSError, subprocess.SubprocessError):
                try:
                    import pypdfium2 as pdfium
                    pdf = pdfium.PdfDocument(str(path))
                    try:
                        for index in range(min(len(pdf), 5)):
                            page = pdf[index]
                            try:
                                textpage = page.get_textpage()
                                try:
                                    text = textpage.get_text_range()
                                finally:
                                    textpage.close()
                                if text.strip():
                                    texts.append(text)
                            finally:
                                page.close()
                    finally:
                        pdf.close()
                    if texts:
                        methods.append('Texto do PDF')
                except (ImportError, OSError, RuntimeError, ValueError):
                    warnings.append('Falha ao ler o texto do PDF.')
            try:
                prefix = Path(folder) / 'pagina'
                executar('pdftoppm', '-f', '1', '-l', '5', '-scale-to', '1800', '-png',
                         str(path), str(prefix))
                images = sorted(Path(folder).glob('pagina-*.png'))
            except (OSError, subprocess.SubprocessError):
                try:
                    import cv2
                    import pypdfium2 as pdfium
                    pdf = pdfium.PdfDocument(str(path))
                    try:
                        for index in range(min(len(pdf), 5)):
                            page = pdf[index]
                            try:
                                width, height = page.get_size()
                                bitmap = page.render(scale=min(1800 / max(width, height), 2.5))
                                image_path = Path(folder) / f'pagina-{index}.png'
                                if not cv2.imwrite(str(image_path), bitmap.to_numpy()):
                                    raise OSError('Falha ao salvar página renderizada')
                                images.append(image_path)
                                bitmap.close()
                            finally:
                                page.close()
                    finally:
                        pdf.close()
                except (ImportError, OSError, RuntimeError, ValueError):
                    warnings.append('Falha ao renderizar páginas do PDF.')
        for image in images:
            try:
                qr = qr_imagem(image)
                if qr:
                    qr_codes.append(qr)
            except (OSError, TypeError, subprocess.SubprocessError):
                try:
                    qr = qr_imagem_opencv(image)
                    if qr:
                        qr_codes.append(qr)
                except (ImportError, OSError, ValueError):
                    warnings.append('Leitor de QR indisponível para parte do documento.')
        # PDF digital com identificação suficiente dispensa OCR.
        pdf_fields = campos('\n'.join(texts))
        if not all(pdf_fields.get(key) for key in ('nome', 'matricula', 'cpf')):
            for image in images:
                try:
                    tesseract = shutil.which('tesseract')
                    if not tesseract and os.name == 'nt':
                        installed = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Tesseract-OCR' / 'tesseract.exe'
                        if installed.is_file():
                            tesseract = str(installed)
                    language = os.getenv('P33_OCR_LANG', 'por')
                    tessdata = Path(__file__).resolve().parent / 'dados'
                    extra = ['--tessdata-dir', str(tessdata)] if (tessdata / f'{language}.traineddata').is_file() else []
                    text = executar(tesseract or 'tesseract', str(image), 'stdout', *extra, '-l',
                                    language, '--psm', '6')
                    if text.strip():
                        texts.append(text)
                        methods.append('OCR')
                except (OSError, subprocess.SubprocessError):
                    warnings.append('Leitura por OCR indisponível para parte do documento.')
    candidates = {}
    sources = [campos(text) for text in texts]
    for qr in qr_codes:
        if not re.match(r'^\w+://', qr.strip()):
            qr_fields = campos(qr)
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{5,199}', qr.strip()):
                qr_fields['token_atestado'] = qr.strip()
            sources.append(qr_fields)
    for field in ('nome', 'cpf', 'matricula', 'setor', 'tipo', 'data', 'dias', 'data_inicio', 'data_fim', 'cid', 'medico', 'local_atestado', 'token_atestado', 'outro_profissional', 'matricula_outro', 'data_troca', 'hora_inicio', 'hora_fim', 'ocorrencias_ponto'):
        values = {source[field] for source in sources if field in source}
        if len(values) == 1:
            candidates[field] = values.pop()
        elif len(values) > 1:
            warnings.append(f'Leituras divergentes no campo {field}. Confira o original.')
            if field in pdf_fields:
                candidates[field] = pdf_fields[field]
    if qr_codes:
        methods.append('QR Code')
        if any(re.match(r'^\w+://', qr.strip()) for qr in qr_codes):
            warnings.append('Há um QR com link externo. Confira pelo canal oficial.')
    if not any(candidates.get(key) for key in ('nome', 'cpf', 'matricula')):
        warnings.append('Identificação insuficiente. Preencha os dados manualmente.')
    return {'campos': candidates, 'metodos': list(dict.fromkeys(methods)),
            'avisos': list(dict.fromkeys(warnings))}
