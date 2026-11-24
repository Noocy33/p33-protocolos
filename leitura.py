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

def campos(texto):
    try:
        obj = json.loads(texto)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        texto = '\n'.join(f'{key}: {value}' for key, value in obj.items()
                          if isinstance(value, (str, int)))
    patterns = {
        'nome': r'^\s*(?:nome(?:\s+(?:do colaborador|do paciente|completo))?|colaborador|paciente)\s*:\s*([^\r\n]{3,100})',
        'cpf': r'\bCPF\s*[:=]?\s*(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b',
        'matricula': r'\bmatr[ií]cula\s*[:=]?\s*([A-Z0-9-]{2,30})\b',
        'data': r'^\s*(?:data(?:\s+(?:do documento|de emissão|da ocorrência))?)\s*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b',
        'dias': r'(?:\b(\d{1,3})[^\S\r\n]+dias?\b|\bdias\s*:\s*(\d{1,3})\b)',
    }
    result = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, texto, re.I | re.M)
        if match:
            result[key] = next(value for value in match.groups() if value is not None).strip()
    if 'cpf' in result:
        result['cpf'] = re.sub(r'\D', '', result['cpf'])
    if 'data' in result:
        try:
            value = result['data']
            result['data'] = (dt.datetime.strptime(value, '%d/%m/%Y').date()
                              if '/' in value else dt.date.fromisoformat(value)).isoformat()
        except ValueError:
            del result['data']
    if re.search(r'\batestado\b', texto, re.I):
        result['tipo'] = 'atestado'
    if re.search(r'ocorr[eê]ncia\s+de\s+ponto', texto, re.I):
        result['tipo'] = 'ponto'
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
                warnings.append('Falha ao ler o texto do PDF.')
            try:
                prefix = Path(folder) / 'pagina'
                executar('pdftoppm', '-f', '1', '-l', '5', '-scale-to', '1800', '-png',
                         str(path), str(prefix))
                images = sorted(Path(folder).glob('pagina-*.png'))
            except (OSError, subprocess.SubprocessError):
                warnings.append('Falha ao renderizar páginas do PDF.')
        for image in images:
            try:
                qr = qr_imagem(image)
                if qr:
                    qr_codes.append(qr)
            except (OSError, TypeError, subprocess.SubprocessError):
                warnings.append('Leitor de QR indisponível para parte do documento.')
        # PDF digital com identificação suficiente dispensa OCR.
        pdf_fields = campos('\n'.join(texts))
        if not all(pdf_fields.get(key) for key in ('nome', 'matricula', 'cpf')):
            for image in images:
                try:
                    text = executar('tesseract', str(image), 'stdout', '-l',
                                    os.getenv('P33_OCR_LANG', 'por'), '--psm', '6')
                    if text.strip():
                        texts.append(text)
                        methods.append('OCR')
                except (OSError, subprocess.SubprocessError):
                    warnings.append('Leitura por OCR indisponível para parte do documento.')
    candidates = {}
    sources = [campos(text) for text in texts]
    for qr in qr_codes:
        if not re.match(r'^\w+://', qr.strip()):
            sources.append(campos(qr))
    for field in ('nome', 'cpf', 'matricula', 'tipo', 'data', 'dias'):
        values = {source[field] for source in sources if field in source}
        if len(values) == 1:
            candidates[field] = values.pop()
        elif len(values) > 1:
            warnings.append(f'Leituras divergentes no campo {field}. Confira o original.')
    if qr_codes:
        methods.append('QR Code')
        if any(re.match(r'^\w+://', qr.strip()) for qr in qr_codes):
            warnings.append('Há um QR com link externo. Confira pelo canal oficial.')
    if not any(candidates.get(key) for key in ('nome', 'cpf', 'matricula')):
        warnings.append('Identificação insuficiente. Preencha os dados manualmente.')
    return {'campos': candidates, 'metodos': list(dict.fromkeys(methods)),
            'avisos': list(dict.fromkeys(warnings))}
