"""Relatórios de protocolos sem anexos, CPF ou dados clínicos."""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

HEADERS = ['Protocolo', 'Documento', 'Colaborador', 'Matrícula', 'Setor', 'Recebimento', 'Status']


def linhas_relatorio(rows, types, statuses, codigo):
    result = []
    for row in rows:
        recebido = datetime.fromisoformat(row['criado']).astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M')
        result.append([codigo(row), types.get(row['tipo'], ''), row['nome'], row['matricula'],
                       row['setor'], recebido, statuses.get(row['status'], '')])
    return result


def excel(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Protocolos'
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.fill = PatternFill('solid', fgColor='174457')
        cell.font = Font(color='FFFFFF', bold=True)
        cell.alignment = Alignment(wrap_text=True)
    for row in rows:
        sheet.append([str(value or '') for value in row])
        for cell in sheet[sheet.max_row]:
            cell.data_type = 's'
    for index, width in enumerate((22, 26, 36, 18, 26, 21, 24), start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def pdf(rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=landscape(A4), leftMargin=10*mm, rightMargin=10*mm, topMargin=12*mm, bottomMargin=12*mm)
    body = ParagraphStyle('table', fontName='Helvetica', fontSize=7, leading=9, wordWrap='CJK')
    head = ParagraphStyle('header', parent=body, textColor=colors.white, fontName='Helvetica-Bold')
    title = ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=colors.HexColor('#174457'))
    values = [[Paragraph(escape(str(value or '')).replace('\n', '<br/>'), body) for value in row] for row in rows]
    table = Table([[Paragraph(escape(text), head) for text in HEADERS]] + values,
                  colWidths=[32*mm, 42*mm, 59*mm, 27*mm, 38*mm, 32*mm, 32*mm], repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#174457')),
                               ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f2f7f8')]),
                               ('VALIGN',(0,0),(-1,-1),'TOP'), ('GRID',(0,0),(-1,-1),0.25,colors.HexColor('#dce5ec')),
                               ('LEFTPADDING',(0,0),(-1,-1),4), ('RIGHTPADDING',(0,0),(-1,-1),4),
                               ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    subtitle = ParagraphStyle('subtitle', parent=body, fontSize=9, leading=12)
    story = [Paragraph('Protocolo Eletrônico — Relação de documentos', title), Spacer(1, 4*mm),
             Paragraph(f'{len(rows)} protocolo(s) · Recebimento no horário de São Paulo', subtitle), Spacer(1, 4*mm), table]
    document.build(story)
    return stream.getvalue()
